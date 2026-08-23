# Philon Python

Philon Python is a native Qt desktop workspace for evidence-preserving local document conversion. It ports the completed macOS Philon product to a Python application without changing the original repository or replacing its proven conversion model.

The application is local-first and offline by default. It has no cloud providers, telemetry, automatic model downloads, or automatic replacement of extracted source text.

## What is included

- The source macOS application's GUI, ported panel for panel: header navigation with a Library count badge, the workspace command bar with **Single Job** and **Batch** job tabs, the three-column conversion grid, batch queue/report, Library, Models, Diagnostics and Settings workspaces, the first-launch splash, and light/dark appearances that follow the system.
- The canonical PDFium/Python conversion engine: bounded PDF/image preflight, native extraction, adaptive Apple Vision routing, source geometry reconciled into the frame the page is displayed in, confidence, warnings, citations, cross-page tables, repeated running heads recognised even when they carry a page number, link annotations measured onto the characters they cover, and evidence retention.
- A page range for a Single Job, written `1-5,8`. The selection is part of the cache key and of the export directory name, so a conversion of part of a document is never served for, or written over, the conversion of all of it.
- Markdown, semantic HTML, Philon IR, page-tree interchange JSON, chunks, optional verified BGE-M3 embeddings, source assets, CSV tables, evidence reports, previews/overlays, and hashed output manifests.
- A page preview with page navigation, fit/actual-size controls, selected-block source-region overlays, block review/editing, retained candidates, and source-crop opening for manual repair candidates
- An evidence summary that reports, per selected block, its source method, confidence, route, measured region, the page's own rotation, and the links the source declared — including a target withheld because its scheme is not one Philon will make clickable.
- SQLite-backed history, settings, persistent/recoverable batch queues, pause-after-current, cancellation of pending work, retry, cross-document review through the Library, and collision-safe export copying.
- Explicit model licence/readiness gates. User-managed Qwen 3.8 and olmOCR candidates are manual unless a run explicitly asks for automatic repair, which acts only where the health gate already refused to vouch for the text and retains the extracted words beside every replacement; BGE-M3 is only requested by Verified conversion and never silently fabricates vectors.
- A private-corpus benchmark harness that records cold/warm timing, cache state, output contracts, private gold metrics, and isolated external comparators.

## Run locally

Use a Python 3.10+ virtual environment. Installing dependencies is a developer-controlled operation.

Conversion opens no connection at all, and no document, fragment or filename
ever leaves the machine. There is exactly **one** thing Philon will fetch, and
only when you ask for it by name in the Models pane: a model pack. It lives in
`engine/model_fetch.py`, the single file exempt from the local-only gate — a
**named file**, not a relaxed pattern, so every source that runs a conversion is
still held to the original rule. The gate additionally checks that the exemption
is load-bearing and that the engine never imports the fetcher at module scope,
and `tests/model_fetch_policy.py` holds that one file to HTTPS, an exact-match
host allow-list, a re-check of every redirect hop, and a SHA-256 comparison that
must pass before anything is moved into place. It uses only the standard
library, so the dependency count and the SBOM are unchanged.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m philon_desktop
```

Application data is stored in `~/Library/Application Support/Philon Python`. Set `PHILON_DATA_DIR` to an alternate local directory for testing or an isolated deployment.

## Verify

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest engine/test_engine.py engine/test_fuzz.py bench/test_run.py -v
.venv/bin/python tests/local_only_policy.py
.venv/bin/python tests/license_policy.py
.venv/bin/python tests/sbom_policy.py
```

The original engine test suite requires `pypdfium2`, `pypdf`, and Pillow. Apple Vision integration tests additionally require the locally compiled macOS helper and `PHILON_VISION_INTEGRATION=1`.

## Package macOS

On an Apple Silicon Mac with Xcode command-line tools:

```bash
zsh scripts/package-macos.sh
```

The script creates `.venv` if it is missing and builds from it, so the bundle
carries the four runtime dependencies and nothing else that happens to be
importable on the machine. It builds an ad-hoc signed `dist/Philon.app` with the locally compiled Apple Vision helper. The ad-hoc seal is what lets `codesign --verify` pass; owner-managed Developer ID signing, notarization, and release distribution remain intentionally separate.

## Releases

A version number here describes this application, and moves when this
application changes. It does not track the source project: each releases when
it has something to release, so the two numbers drift apart on purpose rather
than one being bumped to match a fix it does not contain. This port is 0.2.3
while Philon is 0.2.2.

What does have to match is the engine contract and the IR version. The engine
contract remains at 0.2.0. The IR is at **0.5.0** in both projects: it gained
the merged cells a table's missing rules prove, the formula a page's own script
geometry proves, and the provenance an automatic local repair leaves behind.
0.4.0 had added the tables recovered from the rules a page draws, and 0.3.0 the
page's own `/Rotate`, the source-declared links measured onto each block, and
the page selection a conversion covers. A document converted by either project
at an IR version carries the same evidence and says so in `philon_ir_version`.

A cache entry is named after the IR version it holds, so an entry written
against an older shape is never reached rather than being read and rejected. An
entry that cannot be read back is recomputed from the source: reuse is an
optimisation, and a broken optimisation must not be able to refuse a document.

**0.2.3** — Packaging builds from the project virtual environment instead of
whatever interpreter is on `PATH`. PyInstaller collects what it can import, so
building from a general-purpose environment shipped it: the 0.2.2 bundle
carried numpy, IPython, matplotlib and a compiler toolchain, weighed 216MB, and
had an SBOM that could not honestly describe it. The same environment now runs
the verification, so the versions under test are the versions in the bundle,
and the SBOM records every one of them.

Closing the window also waits for background work. A `QThread` destroyed while
still running aborts the process, so quitting during a library refresh or an
export crashed rather than closed; the upgrade to PySide6 6.11 made it
reproducible.

**0.2.2** — Nothing in this port changed; the number was moved to match the
source project, which is the practice this project has since dropped. The
engine shutdown fix that prompted Philon 0.2.2 does not apply here: that bug
was in the Tauri host, which spawns the engine as a sidecar and left it running
after quitting. This port imports the engine into its own process, so there is
no sidecar to orphan. The packaging script and docs stopped calling the bundle
unsigned, which it has not been since it started carrying an ad-hoc seal.

**0.2.1** — The packaged application declares `com.tsevis.philon-python` rather
than PyInstaller's bare `Philon` default. The identifier is passed on the
command line in `scripts/package-macos.sh`, because that invocation regenerates
the spec file on every run and a hand-edited spec was being silently
overwritten. The project is licensed MIT.

## Licence and what ships

Philon's own source is MIT. The bundle also carries Qt, through PySide6 and
shiboken6, under LGPL-3.0-only: used unmodified and through the public API, and
shipped as separate dynamic libraries rather than linked into the application
binary. Distribution has to keep those libraries replaceable and carry the LGPL
notice.

That decision is recorded against the components in `SBOM.cdx.json`, and
`tests/sbom_policy.py` fails if a shipped copyleft component does not carry it.
The source project refuses copyleft in its shipped set outright; this port
cannot, because Qt is the interface, so the rule here is that the decision is
written down rather than assumed.

See [the parity report](docs/PARITY.md) for implementation and validation status, including externally gated functionality.

