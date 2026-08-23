r"""Reject actual network clients and forbidden production dependencies.

The patterns below are ordinary regular expressions. They were previously
written with doubled escapes inside raw strings -- `r"\\s"` rather than `r"\s"` --
which made every one of them require a literal backslash in the scanned source.
The policy therefore matched nothing at all: `import requests` in the engine
passed it, and the script still printed that it had passed. A policy that cannot
fail is not a policy, so this file now proves itself against known-bad samples
before it scans anything.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ROOT / "engine" / "philon_engine.py",
    # Listed so the exemption below is a deliberate skip of a known file rather
    # than a file nobody remembered to scan.
    ROOT / "engine" / "model_fetch.py",
    ROOT / "philon_desktop" / "core.py",
    ROOT / "philon_desktop" / "app.py",
    *sorted((ROOT / "philon_desktop" / "gui").glob("*.py")),
    ROOT / "philon_launcher.py",
    ROOT / "bench" / "run.py",
    ROOT / "requirements.txt",
]
FORBIDDEN = (
    r"(?:^|\n)\s*(?:from|import)\s+(?:requests|httpx|aiohttp|urllib3|boto3|openai|anthropic|socketserver)\b",
    r"(?:^|\n)\s*from\s+urllib\b",
    r"(?:^|\n)\s*import\s+urllib\b",
    r"(?:^|\n)\s*(?:requests|httpx|aiohttp|urllib3|boto3|openai|anthropic|pymupdf|fitz|marker|surya|mineru|nougat|torch)(?:[<=>@~!\s]|$)",
    r"(?:curl|wget)\s+(?:-\S+\s+)*https?://",
    r"\bsocket\.(?:create_connection|socket)\s*\(",
    r"\bAF_INET6?\b",
    r"\bwebbrowser\.open\b",
)

# The policy must be able to say no. Each sample is something that MUST be
# rejected; if any of them slips through, the patterns are broken and the whole
# check is worthless, so the run fails here rather than reporting a pass.
MUST_REJECT = (
    "import os\nimport requests\n",
    "from pathlib import Path\nfrom requests import get\n",
    "import httpx\n",
    "from urllib.request import urlopen\n",
    "import urllib.request\n",
    "pypdfium2==4.30.0\nrequests==2.32.0\n",
    "torch==2.4.0\n",
    "subprocess.run(['sh', '-c', 'curl https://example.invalid'])\n",
    "s = socket.create_connection((host, 443))\n",
    "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n",
    "webbrowser.open('https://example.invalid')\n",
)
# And it must be able to say yes: these are shapes the real sources contain.
MUST_ACCEPT = (
    "import asyncio\nserver = await asyncio.start_unix_server(handle, path=str(p))\n",
    "import subprocess\nsubprocess.run([str(helper), str(path)], check=True)\n",
    "pypdfium2==4.30.0\npypdf==5.1.0\nPillow==11.0.0\n",
    "from pathlib import Path\nimport statistics\n",
)


def offending(text: str) -> list[str]:
    return [pattern for pattern in FORBIDDEN if re.search(pattern, text, re.IGNORECASE)]


for sample in MUST_REJECT:
    if not offending(sample):
        raise SystemExit(f"Local-only policy is broken: it accepts {sample!r}")
for sample in MUST_ACCEPT:
    hits = offending(sample)
    if hits:
        raise SystemExit(f"Local-only policy is too broad: {sample!r} rejected by {hits}")

# ONE file is exempt: engine/model_fetch.py, which fetches a model pack a person
# explicitly asked for. The exemption is a named file rather than a relaxed
# pattern, so the guarantee the rest of the project makes is unchanged: a
# conversion still cannot reach the network, because none of the files that run
# one are allowed to. What the exempt file may itself do is held down by
# tests/model_fetch_policy.py.
EXEMPT = {
    ROOT / "engine" / "model_fetch.py": "explicit, allow-listed, digest-verified model pack fetch",
}

# The exemption has to be load-bearing. One listed here with no network call in
# it would silently widen the gate the day somebody adds one.
for path in EXEMPT:
    if not path.exists():
        raise SystemExit(f"Local-only policy exempts a missing file: {path}")
    if not offending(path.read_text(encoding="utf-8")):
        raise SystemExit(
            f"Local-only policy exempts {path.name}, but nothing in it needs the exemption. "
            "Remove the exemption rather than leaving the gate wider than the code."
        )

scanned = 0
for source in SOURCES:
    if not source.exists():
        raise SystemExit(f"Local-only policy cannot scan a missing source: {source}")
    if source in EXEMPT:
        continue
    hits = offending(source.read_text(encoding="utf-8"))
    if hits:
        raise SystemExit(f"Local-only policy rejected {source}: {', '.join(hits)}")
    scanned += 1

# The conversion engine must not import the fetcher at module scope. If it did,
# every conversion would load a network client and the separation the exemption
# rests on would exist only on paper. No leading whitespace: an indented import
# is inside a function, which is exactly where it is required to be.
for line in (ROOT / "engine" / "philon_engine.py").read_text(encoding="utf-8").splitlines():
    if re.match(r"^(?:from|import)\s+model_fetch\b", line):
        raise SystemExit(
            "engine/philon_engine.py imports model_fetch at module scope; "
            "it must import it inside the fetch action."
        )

print(f"Local-only policy passed for {scanned} production sources "
      f"({len(MUST_REJECT)} rejection samples and {len(MUST_ACCEPT)} acceptance samples verified first, "
      f"{len(EXEMPT)} named exemption checked to be load-bearing and not reachable from a conversion).")
