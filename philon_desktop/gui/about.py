"""What Philon is, who it is named after, and what it owes.

Shown once at launch and reachable afterwards. A splash screen is unusual,
and this one earns its place by carrying the thing that has to be carried:
Philon's promise is that nothing leaves the machine and that no text is
invented, and the first launch is exactly when that is worth reading.

The text is data with tests on it rather than strings typed into a component,
so a claim cannot go missing without a test noticing. Ported from the source
application's `src/lib/about.ts`; only the shell/runtime credit line differs,
because this build ships Qt rather than Tauri.
"""

TITLE = "Philon"
SUBTITLE = "Documents converted with their evidence intact"
VERSION = "0.2.5"

ABOUT = (
    "Philon of Alexandria spent his life reading one tradition in the language of "
    "another. Writing in Greek in the first century, he worked through the Hebrew "
    "scriptures a passage at a time — quoting the line, then drawing out what he took "
    "it to mean — and he held that the literal sense had to stand even where the "
    "allegory moved him most, against contemporaries content to let the reading "
    "replace the text."
    "\n\n"
    "This is a small tribute to that discipline. A conversion is a reading: a PDF "
    "becomes Markdown only because something decided what was a heading, what was a "
    "table, and what the letters were. Philon keeps the source beside the reading — "
    "every block carries its page, its method, its confidence and what was checked — "
    "and marks what it cannot settle instead of smoothing it into fluent text nobody "
    "can go back and verify."
)

CREDIT = "Created by Charis Tsevis, with the help of Claude Code."

LINKS: tuple[tuple[str, str], ...] = (
    ("tsevis.com", "https://tsevis.com"),
    ("github.com/tsevis", "https://github.com/tsevis"),
)

LEGAL = (
    "The engraving above is a detail of an antique portrait plate: a robed scholar "
    "holding a closed book."
    "\n\n"
    "Extraction uses PDFium through pypdfium2 (BSD-3-Clause) and pypdf (BSD-3-Clause); "
    "images through Pillow (HPND). On-device recognition is Apple Vision, a macOS "
    "system framework. The desktop shell is Qt through PySide6 (LGPL-3.0-only), with "
    "SQLite (public domain) through Python's sqlite3. Interface icons are Phosphor "
    "(MIT). Every declared component, and whether it ships or only builds and tests, "
    "is listed in SBOM.cdx.json."
    "\n\n"
    "Philon does not reuse Marker code or models. Optional local model packs stay off "
    "until you enable them, and a pack whose licence has not been approved cannot be "
    "enabled at all."
    "\n\n"
    "Your documents are yours. Philon never sends a document, a fragment or a filename "
    "anywhere."
)
