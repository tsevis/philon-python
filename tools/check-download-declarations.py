#!/usr/bin/env python3
"""Does every fetchable pack still resolve, at the size the manifest declares?

**This touches the network, and so is deliberately not part of
`scripts/verify-release.sh`.** The gates that run there must pass offline; a
release gate that fails when HuggingFace is slow is a gate people learn to
ignore. Run this by hand before a release, and whenever a download is reported
to fail.

It exists because the manifest makes a claim nothing else checks. Every declared
SHA-256 and byte count was computed from a real copy on the machine that wrote
it, which makes the manifest an accurate record of *that* copy -- and says
nothing about whether the publisher still serves the same bytes at the same
name. Upstream moves: a quantisation is withdrawn, a file is re-uploaded a
handful of bytes larger, a repository is reorganised. Nothing in the suite can
see it, because every test of the fetch path mocks the fetch.

It reads no bodies. For each declared file it opens the URL the fetcher would
open, through the fetcher's own allow-listed opener, compares the served
Content-Length to the declared byte count, and closes. Fifteen files cost a few
kilobytes.

`_open_checked` is private, and using it is the point: it is the only path that
applies the host allow-list and walks each redirect hop by hand. Re-implementing
the request here would exercise code this tool does not ship and prove nothing
about the code it does.

Exit status is 0 when every declared file resolved at its declared size.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

import model_fetch  # noqa: E402


def declared_packs() -> list[dict]:
    manifest = json.loads((ROOT / "engine" / "model-manifest.json").read_text(encoding="utf-8"))
    packs = manifest["packs"] if isinstance(manifest, dict) and "packs" in manifest else manifest
    return [pack for pack in packs if (pack.get("download") or {}).get("files")]


def check_file(repository: str, revision: str, item: dict) -> tuple[bool, str]:
    url = model_fetch.resolved_download_url(repository, item["name"], revision)
    try:
        response = model_fetch._open_checked(url)
    except Exception as exc:  # noqa: BLE001 - the report is the point, not the type
        return False, f"{type(exc).__name__}: {exc}"
    try:
        served = int(response.headers.get("Content-Length") or -1)
        host = urlparse(response.geturl()).netloc
    finally:
        response.close()
    declared = int(item["bytes"])
    if served != declared:
        return False, f"served {served} bytes, manifest declares {declared} (via {host})"
    return True, f"{served} bytes (via {host})"


def main() -> int:
    failures: list[str] = []
    checked = 0
    for pack in declared_packs():
        download = pack["download"]
        print(f"\n== {pack['id']}")
        for item in download["files"]:
            ok, detail = check_file(download["repository"], download.get("revision", "main"), item)
            checked += 1
            print(f"   {'ok  ' if ok else 'FAIL'} {item['name']}: {detail}")
            if not ok:
                failures.append(f"{pack['id']}/{item['name']}: {detail}")

    print()
    if failures:
        print(f"{len(failures)} of {checked} declared file(s) no longer match what the publisher serves:")
        for line in failures:
            print(f"  - {line}")
        print("\nThe manifest is byte-identical across both repositories and is held so by\n"
              "tests/parity_policy.py, so correcting it means correcting it in both.")
        return 1
    print(f"All {checked} declared file(s) resolve at the size the manifest declares.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
