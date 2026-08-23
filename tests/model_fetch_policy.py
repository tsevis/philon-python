r"""Hold the one network-capable file to the rules its exemption assumes.

`tests/local_only_policy.py` exempts `engine/model_fetch.py` so a person can
download a model pack they asked for. That exemption is only as good as what
this gate holds the file to. A file allowed to open connections and then
trusted to be careful is not a policy.

Five properties are checked in the source, and the manifest is checked against
the same rules, because a download block naming an arbitrary host or carrying
no digest would walk around all five. The fourth of them -- that the redirect
check is reachable at all -- was added after a real fetch showed the module
returning a 200 from a host its own allow-list refuses.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FETCHER = ROOT / "engine" / "model_fetch.py"
MANIFEST = ROOT / "engine" / "model-manifest.json"

source = FETCHER.read_text(encoding="utf-8")
failures: list[str] = []

# 1. Only the standard library. A new HTTP dependency would be a new SBOM entry
#    and a new supply-chain surface for the one file that talks to a host.
for banned in ("requests", "httpx", "aiohttp", "urllib3", "boto3"):
    if re.search(rf"(?:^|\n)\s*(?:from|import)\s+{banned}\b", source):
        failures.append(f"model_fetch.py imports {banned}; it must use only the standard library.")

# 2. An explicit host allow-list, consulted by exact match. A suffix test would
#    accept huggingface.co.example.invalid.
if "MODEL_HOST_ALLOWLIST = (" not in source:
    failures.append("model_fetch.py declares no MODEL_HOST_ALLOWLIST.")
if "host in MODEL_HOST_ALLOWLIST" not in source:
    failures.append("model_fetch.py never checks a host against MODEL_HOST_ALLOWLIST by exact match.")
if re.search(r"endswith\(\s*MODEL_HOST_ALLOWLIST", source):
    failures.append("model_fetch.py matches hosts by suffix, which accepts a look-alike domain.")

# 2a. The one rule that is not exact match. HuggingFace serves large files from a
#     per-region Xet CDN host that cannot be enumerated here, so hosts beneath a
#     named parent are allowed. That is only safe while the match is anchored on
#     a leading dot: `cdn.hf.co.example.invalid` ends with `cdn.hf.co` and would
#     pass an unanchored test, and does not end with `.cdn.hf.co`.
if "MODEL_HOST_ALLOWED_PARENTS" in source:
    if 'endswith("." + parent)' not in source:
        failures.append(
            "model_fetch.py matches an allowed parent domain without anchoring on a leading dot; "
            "that accepts a look-alike such as cdn.hf.co.example.invalid."
        )
    block = re.search(r"MODEL_HOST_ALLOWED_PARENTS\s*=\s*\(([^)]*)\)", source)
    for parent in re.findall(r'"([^"]+)"', block.group(1) if block else ""):
        # A parent with one label is a public suffix: ".co" would allow the world.
        if len(parent.split(".")) < 2:
            failures.append(f"model_fetch.py names {parent} as an allowed parent; that is too broad to be a host.")

# 3. HTTPS only, and every redirect hop re-checked rather than the first URL.
if 'scheme.lower() != "https"' not in source:
    failures.append("model_fetch.py does not require HTTPS.")
if "for _ in range(MAX_REDIRECTS" not in source or "is_allowed_url(current)" not in source:
    failures.append("model_fetch.py does not re-check each redirect hop against the allow-list.")

# 3a. And that the re-check is reachable. This is the property whose absence made
#     the loop above dead code: `urllib.request.urlopen` installs a redirect
#     handler that follows hops itself and returns only the final response, so
#     the allow-list covered the first URL and nothing after it. A real fetch came
#     back 200 from a host `is_allowed_url` refuses. The module must therefore
#     open through an opener built to refuse redirects, and must not reach for
#     `urlopen`, which cannot be given one.
if re.search(r"urllib\.request\.urlopen\s*\(", source):
    failures.append(
        "model_fetch.py calls urllib.request.urlopen, which follows redirects itself; "
        "the hand-written redirect check would never see a hop."
    )
if not re.search(r"class\s+_RefuseRedirects\(urllib\.request\.HTTPRedirectHandler\)", source):
    failures.append("model_fetch.py installs no handler that refuses to follow redirects.")
if not re.search(r"def redirect_request\([^)]*\):[^\n]*\n\s*return None", source):
    failures.append("model_fetch.py's redirect handler does not refuse the hop by returning None.")
if "build_opener(" not in source or "opener.open(request" not in source:
    failures.append("model_fetch.py does not open its connections through the opener it built.")

# 4. Nothing is installed before its bytes match.
if "hashlib.sha256()" not in source:
    failures.append("model_fetch.py computes no SHA-256.")
if "os.replace(temporary, destination)" not in source:
    failures.append("model_fetch.py does not install atomically from a temporary file.")
digest_at = source.find("did not match its declared SHA-256")
install_at = source.find("os.replace(temporary, destination)")
if digest_at < 0 or install_at < 0 or digest_at > install_at:
    failures.append("model_fetch.py installs the file before comparing its digest.")
if 'if not pack.get("approved", False)' not in source:
    failures.append("model_fetch.py does not refuse an unapproved pack.")

# 5. The manifest's own download blocks.
declared = json.loads(MANIFEST.read_text(encoding="utf-8"))
blocks = files = 0
for pack in declared.get("packs", []):
    download = pack.get("download")
    if not download:
        continue
    blocks += 1
    repository = str(download.get("repository", ""))
    if not repository or re.match(r"^https?:", repository, re.IGNORECASE) or ".." in repository:
        failures.append(f"{pack['id']} declares an unusable download repository.")
    if not pack.get("approved"):
        failures.append(f"{pack['id']} declares a download but is not approved; it could never be fetched.")
    for item in download.get("files", []):
        files += 1
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
            failures.append(f"{pack['id']}/{item.get('name')} has no usable SHA-256.")
        if not isinstance(item.get("bytes"), int) or item["bytes"] <= 0:
            failures.append(f"{pack['id']}/{item.get('name')} declares no byte count.")
        name = str(item.get("name", ""))
        if name.startswith("/") or ".." in name or "\\" in name:
            failures.append(f"{pack['id']} declares a file name that escapes its destination: {name}")

if failures:
    raise SystemExit("Model-fetch policy failed:\n  - " + "\n  - ".join(failures))
print(f"Model-fetch policy passed: 1 network-capable source held to HTTPS, an exact-match host "
      f"allow-list with dot-anchored parents, a redirect check that is reachable because the opener "
      f"refuses to follow hops itself, and a verified digest; {blocks} declared download blocks "
      f"covering {files} files, every one with a SHA-256 and a byte count.")
