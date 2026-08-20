import json
from pathlib import Path

bom = json.loads((Path(__file__).resolve().parents[1] / "SBOM.cdx.json").read_text(encoding="utf-8"))
names = {component["name"] for component in bom.get("components", [])}
required = {"PySide6", "pypdfium2", "pypdf", "Pillow"}
if bom.get("bomFormat") != "CycloneDX" or required - names:
    raise SystemExit(f"SBOM policy failed; missing: {', '.join(sorted(required - names))}")
print(f"SBOM policy passed for {len(names)} declared components.")
