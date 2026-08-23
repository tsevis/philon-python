r"""Check the two engine copies against each other, rather than by hand.

Philon is two repositories -- the Tauri/React source project and the PySide6
port -- sharing one conversion engine. Three files are the shared part:

    engine/philon_engine.py     identical but for ONE documented hunk
    engine/model_fetch.py       byte-identical
    engine/model-manifest.json  byte-identical

Nothing used to check any of it. That is the most dangerous kind of invariant,
because it breaks without a single test failing: each repository tests its own
copy, so both suites pass happily while the two engines drift apart. They once
drifted to forty-six hunks that way, and the port was the copy that was behind
-- missing a fix that stopped `action_convert` mutating its caller's request,
missing the `enabled_model_ids` gate, forwarding no progress from its socket
bridge, and carrying two live definitions each of `render_markdown` and
`render_html`. Every suite was green throughout.

So this runs in both `verify-release` paths, and it is deliberately the same
file in both repositories: it is a fourth shared file, and it checks itself.

When the peer repository is not on this machine it skips -- loudly, naming what
went unchecked, because a check that quietly passes when it did nothing is
worse than no check. Locally it never fails for the peer's absence: a clone of
one repository alone is a legitimate way to work.

CI is the one place where that leniency would defeat the point, because a gate
that passes without looking is indistinguishable from a gate that looked and
was satisfied. So `PHILON_PARITY_REQUIRE=1` turns the skip into a failure, and
the workflows set it.
"""
from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Byte-identical in both repositories. No exceptions, no documented hunks.
IDENTICAL = (
    Path("engine/model_fetch.py"),
    Path("engine/model-manifest.json"),
    Path("tests/parity_policy.py"),
)

#: Identical but for the one divergence recorded in the port's docs/PARITY.md.
DIVERGES = Path("engine/philon_engine.py")

#: The whole of that divergence. The port converts a non-zero local embedding
#: process into evidence rather than failing an otherwise valid conversion, so
#: its `except` tuple carries one more exception type. Anything else appearing
#: in the hunk is drift wearing the divergence's clothes.
DIVERGENCE_TYPE = "RuntimeError"


def _peer_root() -> Path | None:
    """The other repository, if it is on this machine.

    `PHILON_PARITY_PEER` wins when it is set, so a checkout laid out some other
    way can still be checked. Otherwise the siblings are tried by name.
    """
    override = os.environ.get("PHILON_PARITY_PEER", "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        return candidate if (candidate / DIVERGES).is_file() else None
    for name in ("philon", "philon_p"):
        candidate = ROOT.parent / name
        if candidate != ROOT and (candidate / DIVERGES).is_file():
            return candidate
    return None


def _is_port(root: Path) -> bool:
    """Which of the two this is. The port is the one carrying the parity report."""
    return (root / "docs" / "PARITY.md").is_file()


def _hunks(left: list[str], right: list[str], left_name: str, right_name: str) -> list[list[str]]:
    """The unified diff, split into hunks, so they can be counted and read."""
    diff = list(difflib.unified_diff(left, right, fromfile=left_name, tofile=right_name, n=0))
    hunks: list[list[str]] = []
    for line in diff:
        if line.startswith("@@"):
            hunks.append([])
        elif hunks and (line.startswith("+") or line.startswith("-")):
            hunks[-1].append(line)
    return hunks


def _check_divergence(hunk: list[str], port_is_right: bool) -> list[str]:
    """Whether the one hunk is the documented one and only the documented one."""
    problems: list[str] = []
    port_marker, source_marker = ("+", "-") if port_is_right else ("-", "+")
    port_lines = [line[1:] for line in hunk if line.startswith(port_marker)]
    source_lines = [line[1:] for line in hunk if line.startswith(source_marker)]

    code = [line for line in port_lines if line.strip() and not line.strip().startswith("#")]
    if len(code) != 1 or DIVERGENCE_TYPE not in code[0]:
        problems.append(
            f"The port's side of the hunk is not the documented divergence. Expected one line of "
            f"code carrying {DIVERGENCE_TYPE}; found {len(code)}: {code!r}"
        )
    if len(source_lines) != 1:
        problems.append(
            f"The source's side of the hunk is {len(source_lines)} lines, not the one it replaces: "
            f"{source_lines!r}"
        )
    elif code and code[0].replace(f", {DIVERGENCE_TYPE}", "") != source_lines[0]:
        problems.append(
            "The two sides of the hunk differ by more than the one exception type:\n"
            f"        source: {source_lines[0].strip()}\n"
            f"        port:   {code[0].strip()}"
        )
    if not any("PARITY.md" in line for line in port_lines):
        problems.append(
            "The divergence carries no comment pointing at docs/PARITY.md. It has to say in the "
            "source that it is deliberate, or the next reader will 'fix' it."
        )
    return problems


def main() -> int:
    peer = _peer_root()
    if peer is None:
        required = os.environ.get("PHILON_PARITY_REQUIRE", "").strip().lower() in {"1", "true", "yes"}
        print(
            f"Engine parity {'FAILED' if required else 'SKIPPED'}: the peer repository is not on "
            "this machine, so the engine, the fetcher and the manifest went unchecked against "
            "their other copy.\n"
            "  Set PHILON_PARITY_PEER to the other checkout to run this."
            + ("\n  PHILON_PARITY_REQUIRE is set, so going unchecked is an error here." if required else ""),
            file=sys.stderr,
        )
        return 1 if required else 0

    here, there = ROOT, peer
    port_is_right = _is_port(there)
    if port_is_right == _is_port(here):
        print(
            f"Engine parity FAILED: {here} and {there} both look like the same side of the pair "
            "(the port is the one carrying docs/PARITY.md). One of them is not what it claims.",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    for relative in IDENTICAL:
        mine, theirs = here / relative, there / relative
        if not theirs.is_file():
            failures.append(f"{relative} is missing from {there}; it must exist in both.")
            continue
        if mine.read_bytes() != theirs.read_bytes():
            count = len(_hunks(
                mine.read_text(encoding="utf-8").splitlines(keepends=True),
                theirs.read_text(encoding="utf-8").splitlines(keepends=True),
                str(mine), str(theirs),
            ))
            failures.append(
                f"{relative} is not byte-identical across the two repositories ({count} hunk(s)). "
                "This file carries no documented divergence."
            )

    hunks = _hunks(
        (here / DIVERGES).read_text(encoding="utf-8").splitlines(keepends=True),
        (there / DIVERGES).read_text(encoding="utf-8").splitlines(keepends=True),
        str(here / DIVERGES), str(there / DIVERGES),
    )
    if len(hunks) != 1:
        failures.append(
            f"{DIVERGES} differs by {len(hunks)} hunk(s); exactly one is documented. "
            f"Run: diff {here / DIVERGES} {there / DIVERGES}"
        )
    else:
        failures.extend(_check_divergence(hunks[0], port_is_right))

    if failures:
        print("Engine parity FAILED:\n  - " + "\n  - ".join(failures), file=sys.stderr)
        return 1

    print(
        f"Engine parity passed against {there}: {len(IDENTICAL)} file(s) byte-identical, and "
        f"{DIVERGES} differing by the one documented hunk."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
