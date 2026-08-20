# Philon Python

Philon Python is a native Qt desktop workspace for evidence-preserving local document conversion. It ports the completed macOS Philon product to a Python application without changing the original repository or replacing its proven conversion model.

The application is local-first and offline by default. It has no cloud providers, telemetry, automatic model downloads, or automatic replacement of extracted source text.

## What is included

- Native **Single Job** and **Batch** workspaces, with Library, Models, Diagnostics, and Settings as secondary navigation.
- The canonical PDFium/Python conversion engine: bounded PDF/image preflight, native extraction, adaptive Apple Vision routing, source geometry, confidence, warnings, citations, cross-page tables, and evidence retention.
- Markdown, semantic HTML, Philon IR, clean-room Marker-style JSON, chunks, optional verified BGE-M3 embeddings, source assets, CSV tables, evidence reports, previews/overlays, and hashed output manifests.
- A page preview with page navigation, fit/actual-size controls, selected-block source-region overlays, block review/editing, retained candidates, and source-crop opening for manual repair candidates.
- SQLite-backed history, settings, persistent/recoverable batch queues, pause-after-current, cancellation of pending work, retry, cross-document review through the Library, and collision-safe export copying.
- Explicit model licence/readiness gates. User-managed Qwen 3.8 and olmOCR candidates remain manual-only; BGE-M3 is only requested by Verified conversion and never silently fabricates vectors.
- A private-corpus benchmark harness that records cold/warm timing, cache state, output contracts, private gold metrics, and isolated external comparators.

## Run locally

Use a Python 3.10+ virtual environment. Installing dependencies is a developer-controlled operation; Philon itself never downloads a model or uses the network.

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

This builds an unsigned `dist/Philon.app` with the locally compiled Apple Vision helper. Code signing in the script is ad-hoc only; owner-managed Developer ID signing, notarization, and release distribution remain intentionally separate.

See [the parity report](docs/PARITY.md) for implementation and validation status, including externally gated functionality.

