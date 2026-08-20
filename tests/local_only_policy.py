"""Reject actual network clients and forbidden production dependencies."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sources = [ROOT / "engine" / "philon_engine.py", ROOT / "philon_desktop" / "core.py", ROOT / "requirements.txt"]
forbidden = (
    r"(?:^|\\n)\\s*(?:from|import)\\s+(?:requests|httpx|boto3|openai|urllib\\.request)",
    r"(?:^|\\n)\\s*(?:requests|httpx|boto3|openai|pymupdf|fitz|marker|surya|mineru|nougat|torch)(?:[<=>@\\s]|$)",
    r"(?:curl|wget)\\s+https?://",
)
for source in sources:
    text = source.read_text(encoding="utf-8").lower()
    matches = [item for item in forbidden if re.search(item, text, re.I)]
    if matches:
        raise SystemExit(f"Local-only policy rejected {source}: {', '.join(matches)}")
print(f"Local-only policy passed for {len(sources)} production sources.")
