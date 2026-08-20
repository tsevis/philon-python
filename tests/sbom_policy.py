"""Check that the SBOM describes the application that is actually built.

The port had five components carrying ranges rather than versions ("6.x",
"4.30+") while its bundle shipped dozens of packages the manifest never
mentioned: packaging ran against a general-purpose interpreter, so PyInstaller
collected everything it could import. Packaging now builds from the project
environment, and these checks keep the manifest honest about it.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTIONS = {"shipped": "required", "build-only": "excluded", "test-only": "excluded"}
# A version, not a range: a manifest that says "10.0+" cannot identify what shipped.
RESOLVED = re.compile(r"\d+(\.\d+)*([._-]?(a|b|rc|post|dev)\d+)?")

bom = json.loads((ROOT / "SBOM.cdx.json").read_text(encoding="utf-8"))
if bom.get("bomFormat") != "CycloneDX" or not bom.get("components"):
    raise SystemExit("SBOM must be a populated CycloneDX document.")

declared = {}
for component in bom["components"]:
    name = component.get("name")
    where = f"SBOM component {name or '(unnamed)'}"
    version = component.get("version", "")
    if not name or not RESOLVED.fullmatch(version):
        raise SystemExit(f"{where} must declare a resolved version, not {version!r}.")
    if not component.get("licenses"):
        raise SystemExit(f"{where} must declare a licence.")
    distribution = next(
        (p["value"] for p in component.get("properties", []) if p["name"] == "philon:distribution"),
        None,
    )
    if distribution not in DISTRIBUTIONS:
        raise SystemExit(f"{where} must declare philon:distribution as one of {', '.join(DISTRIBUTIONS)}.")
    if component.get("scope") != DISTRIBUTIONS[distribution]:
        raise SystemExit(f"{where} is {distribution}, so its CycloneDX scope must be {DISTRIBUTIONS[distribution]}.")
    declared[name.lower()] = distribution

# Anything the application requires at runtime has to be declared as shipped,
# so adding a dependency without recording it fails here rather than after a
# release.
for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
    requirement = line.strip()
    if not requirement or requirement.startswith("#"):
        continue
    name = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().lower()
    if declared.get(name) != "shipped":
        raise SystemExit(f"{name} is a runtime requirement but is not declared as a shipped SBOM component.")

counts = {}
for distribution in declared.values():
    counts[distribution] = counts.get(distribution, 0) + 1
summary = ", ".join(f"{value} {key}" for key, value in sorted(counts.items()))
print(f"SBOM policy passed for {len(declared)} declared components ({summary}).")
