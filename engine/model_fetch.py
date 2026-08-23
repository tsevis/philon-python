"""The one place Philon reaches the network, and the only one.

Everything else in this project is local by construction, and
`tests/local-only-policy.mjs` proves it by refusing an HTTP client anywhere in
the conversion path. Fetching a model pack is the single exception, and it is
carved as narrowly as it can be so that the guarantee the rest of the project
makes stays worth something:

* It lives in its own module. The conversion engine never imports it at module
  scope, so a conversion cannot reach the network even by accident.
* It speaks only to an explicit host allow-list, only over HTTPS, and it
  re-checks every redirect hop rather than trusting the first URL.
* It writes nothing until the bytes hash to the digest the manifest declares.
  A file that does not match is discarded, not installed.
* It refuses any pack the model policy has not approved, so the licence gate
  cannot be walked around by asking for a download.
* It is never called during a conversion. Only an explicit `fetch_model`
  request reaches it, and `tests/model-fetch-policy.mjs` checks that.

Nothing here is imported unless a person asks for a model, and the module uses
only the standard library, so the dependency count and the SBOM are unchanged.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

#: The only hosts Philon will open a connection to, ever. HuggingFace serves
#: model blobs from a separate CDN and redirects to it, so both are named here;
#: a redirect anywhere else is refused rather than followed.
MODEL_HOST_ALLOWLIST = (
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.huggingface.co",
    "cdn-lfs-eu-1.huggingface.co",
)

#: How many redirects a single file may take before Philon stops following.
MAX_REDIRECTS = 5
#: Read size. Large enough to be quick, small enough that a cancelled fetch
#: stops promptly and progress moves visibly.
CHUNK_BYTES = 1 << 20
#: A pack file larger than this is refused before a byte is read. The manifest
#: declares every size, so a server offering something else is not the pack.
MAX_PACK_FILE_BYTES = 40 * 1024 * 1024 * 1024


class ModelFetchError(RuntimeError):
    """A fetch that did not happen, with the reason a person can act on."""


def is_allowed_url(url: str) -> bool:
    """Whether one URL is HTTPS and points at a host on the allow-list.

    Host comparison is exact and case-folded. A suffix match would accept
    `huggingface.co.example.invalid`, which is a different host entirely.
    """
    try:
        parsed = urllib.parse.urlsplit(str(url))
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in MODEL_HOST_ALLOWLIST


def resolved_download_url(repository: str, filename: str, revision: str = "main") -> str:
    """The canonical URL one declared pack file is served from."""
    repo = urllib.parse.quote(str(repository).strip("/"), safe="/")
    name = urllib.parse.quote(str(filename).lstrip("/"))
    return f"https://huggingface.co/{repo}/resolve/{urllib.parse.quote(revision)}/{name}"


def _open_checked(url: str) -> Any:
    """Open a URL, checking every redirect hop against the allow-list.

    Redirects are followed by hand rather than by the default opener, because
    the default one would happily follow a redirect off the allow-list and the
    check would then only ever have covered the first URL.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_allowed_url(current):
            raise ModelFetchError(
                f"Refused a model URL that is not HTTPS on an allowed host: {current}"
            )
        request = urllib.request.Request(current, headers={"User-Agent": "Philon-local-model-fetch"})
        context = ssl.create_default_context()
        try:
            response = urllib.request.urlopen(request, timeout=60, context=context)  # noqa: S310 - allow-listed above
        except urllib.error.HTTPError as error:
            if error.code in (301, 302, 303, 307, 308) and error.headers.get("Location"):
                current = urllib.parse.urljoin(current, error.headers["Location"])
                continue
            raise ModelFetchError(f"The model host answered {error.code} for {current}.") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ModelFetchError(f"Could not reach the model host: {error}") from error
        location = response.headers.get("Location") if response.status in (301, 302, 303, 307, 308) else None
        if location:
            response.close()
            current = urllib.parse.urljoin(current, location)
            continue
        return response
    raise ModelFetchError("A model URL redirected more times than Philon will follow.")


def download_verified_file(url: str, destination: Path, expected_sha256: str | None,
                           expected_bytes: int | None = None,
                           progress: Callable[[int, int | None], None] | None = None) -> Path:
    """Fetch one file and install it only if its bytes are what was declared.

    The download lands in a temporary file beside the destination and is moved
    into place only after the digest matches. A partial or substituted file is
    therefore never visible under the name a runtime will load, which matters
    more here than anywhere else in Philon: this is the only content that
    arrives from off the machine.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    handle = _open_checked(url)
    declared = expected_bytes
    try:
        length = handle.headers.get("Content-Length")
        served = int(length) if length and length.isdigit() else None
        if served is not None and served > MAX_PACK_FILE_BYTES:
            raise ModelFetchError("The model host offered a file larger than Philon will accept.")
        if declared is not None and served is not None and served != declared:
            raise ModelFetchError(
                f"{destination.name} is {served} bytes on the host and {declared} in the manifest; refusing it."
            )
        total = declared if declared is not None else served
        temporary = Path(tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))[1])
        try:
            with open(temporary, "wb") as sink:
                while True:
                    chunk = handle.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_PACK_FILE_BYTES:
                        raise ModelFetchError("The model file exceeded the size Philon will accept.")
                    digest.update(chunk)
                    sink.write(chunk)
                    if progress:
                        progress(written, total)
            if declared is not None and written != declared:
                raise ModelFetchError(
                    f"{destination.name} arrived as {written} bytes, not the {declared} the manifest declares."
                )
            if expected_sha256 and digest.hexdigest() != str(expected_sha256).lower():
                raise ModelFetchError(
                    f"{destination.name} did not match its declared SHA-256 and was discarded."
                )
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    finally:
        handle.close()
    return destination


def pack_download_plan(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """What one pack's manifest entry says has to be fetched, checked as it is read."""
    download = pack.get("download") or {}
    repository = download.get("repository")
    files = download.get("files")
    if not repository or not isinstance(files, list) or not files:
        raise ModelFetchError(f"{pack.get('id')} does not declare anything to download.")
    revision = str(download.get("revision") or "main")
    plan: list[dict[str, Any]] = []
    for item in files:
        name = item.get("name")
        if not name or "\\" in str(name) or Path(str(name)).is_absolute() or ".." in Path(str(name)).parts:
            # A manifest is a local file, but it is still input, and a name that
            # climbs out of the destination would write wherever it liked.
            raise ModelFetchError(f"{pack.get('id')} declares an unusable file name: {name!r}")
        plan.append({
            "name": str(name),
            "url": resolved_download_url(repository, str(name), revision),
            "sha256": item.get("sha256"),
            "bytes": item.get("bytes"),
        })
    return plan


def fetch_model_pack(pack: dict[str, Any], destination_root: Path,
                     progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Fetch every file one approved pack declares, verifying each in turn.

    The pack must already have been approved by Philon's model policy. A
    download is not a way around the licence gate, so an unapproved pack is
    refused here as firmly as it is refused at the point of use.
    """
    if not pack.get("approved", False):
        raise ModelFetchError(
            f"{pack.get('id')} is not approved by Philon's local model policy and will not be fetched."
        )
    plan = pack_download_plan(pack)
    destination = Path(destination_root) / str(pack["id"])
    destination.mkdir(parents=True, exist_ok=True)
    fetched: list[str] = []
    skipped: list[str] = []
    for position, item in enumerate(plan, start=1):
        target = destination / item["name"]
        if target.exists() and _already_verified(target, item):
            skipped.append(item["name"])
            continue

        def report(written: int, total: int | None, item=item, position=position) -> None:
            if progress:
                progress({
                    "stage": "downloading", "file": item["name"],
                    "file_index": position, "file_count": len(plan),
                    "written_bytes": written, "total_bytes": total,
                })

        download_verified_file(item["url"], target, item.get("sha256"), item.get("bytes"), report)
        fetched.append(item["name"])
    return {
        "pack_id": pack["id"],
        "path": str(destination),
        "fetched": fetched,
        "already_present": skipped,
        "verified": all(item.get("sha256") for item in plan),
    }


def _already_verified(target: Path, item: dict[str, Any]) -> bool:
    """Whether a file already on disk is the one the manifest declares."""
    expected = item.get("sha256")
    if item.get("bytes") is not None and target.stat().st_size != int(item["bytes"]):
        return False
    if not expected:
        return False
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest() == str(expected).lower()


def remove_model_pack(pack_id: str, destination_root: Path) -> bool:
    """Delete a pack Philon fetched, and say whether there was one to delete.

    Only ever a directory Philon itself wrote, under the store it manages. A
    model a person put on their own machine is theirs, and is never touched.
    """
    target = Path(destination_root) / str(pack_id)
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def managed_store(data_root: Path) -> Path:
    """Where fetched packs live: one directory Philon owns, inside its own data."""
    return Path(data_root) / "models"
