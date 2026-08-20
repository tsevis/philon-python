import json
from pathlib import Path

manifest = json.loads((Path(__file__).resolve().parents[1] / "engine" / "model-manifest.json").read_text(encoding="utf-8"))
allowed = {"BSD-3-Clause / Apache-2.0", "Apple platform runtime"}
invalid = [pack["id"] for pack in manifest["packs"] if pack["required"] and (not pack["approved"] or pack["license"] not in allowed)]
if invalid:
    raise SystemExit(f"Required model packs lack an approved licence: {', '.join(invalid)}")
print(f"License policy passed for {len(manifest['packs'])} declared model packs.")

