#!/usr/bin/env python3
"""Regenerate ADOBE_GLYPH_VARIANTS in engine/philon_engine.py.

Development-only. fontTools is NOT a runtime dependency and is not shipped: it
is used here to read the Adobe Glyph List, whose relevant entries are then
written into the engine as a literal table. Prints the table to stdout.
"""
import re

from fontTools.agl import LEGACY_AGL2UV as AGL

SUFFIX = re.compile(r"(sansserif|sans|serif|smallcaps|small|oldstyle|superior|inferior|monospace)$", re.IGNORECASE)


def single(value):
    if isinstance(value, int):
        return value
    return value[0] if isinstance(value, (list, tuple)) and len(value) == 1 else None


def variants():
    """Every Corporate Use Subarea glyph that is a variant of a real character."""
    subarea = {name: single(v) for name, v in AGL.items()
               if single(v) is not None and 0xF600 <= single(v) <= 0xF8FF}
    for name, value in sorted(subarea.items(), key=lambda item: item[1]):
        match = SUFFIX.search(name)
        base = single(AGL.get(name[:match.start()])) if match else None
        if base is not None and base < 0xE000:
            yield value, base, name


if __name__ == "__main__":
    rows = list(variants())
    print(f"# {len(rows)} entries")
    for value, base, name in rows:
        print(f"0x{value:04X}: 0x{base:04X},  # {name}")
