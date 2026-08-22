# Python desktop parity report

## Scope and comparison

This port was compared against the authored source in `/Users/tsevis/AI/ClaudeCode/philon`: the Tauri/React workspace, Rust host and SQLite queue, canonical Python engine, manifest, benchmark harness, tests, packaging/release scripts, policies, and research RFCs. The original repository was read-only throughout.

The Python port retains the original engine and harness as its conversion/evidence authority. Its one intentional engine hardening converts a non-zero local BGE-M3 process into `EMBEDDING_FAILED` evidence rather than failing an otherwise valid Verified conversion. No vector is emitted in that case.

### The two engine copies had drifted, and this claim was not true

Until this revision the two copies of `engine/philon_engine.py` differed by **46
hunks**, not by the one divergence above. `diff` now reports exactly one hunk:
the `RuntimeError` in that `except` tuple, carrying a comment that says so, and
`engine/test_engine.py` is the same suite in both repositories.

The drift was not cosmetic and the port was the copy that was behind. It was
missing a fix that stops `action_convert` mutating its caller's request object
and aliasing that list into the response; it had no `enabled_model_ids` gate, so
a repair could use an approved pack the user had not enabled; its socket bridge
forwarded no progress and no `job_id`; and it carried **two live definitions
each** of `render_markdown` and `render_html`, the first of each being dead code
shadowed by the second. Nothing detected any of it, because each repository
tests its own copy and both suites passed throughout.

Anything that changes this file must change it in both places, and `diff` over
the two copies is the check that says whether that happened.

| Area | Status | Evidence |
|---|---|---|
| Native desktop shell and menus | Implemented | PySide6 Qt production binding; native File/View menus and keyboard shortcuts in `philon_desktop/app.py`. PyQt6 remains a development-only fallback when PySide6 is not installed. |
| Single Job and Batch primary workspaces | Implemented | Primary pages only; Library, Models, Diagnostics, Settings are side navigation. |
| PDF/image safety preflight | Implemented and verified | Canonical bounded preflight rejects invalid, empty, encrypted, oversized, malformed, multi-frame and decompression-bomb inputs. |
| Native PDF/OCR adaptive routing | Implemented; Apple Vision runtime gated | Canonical PDFium extraction, native-text health, per-page routes and adaptive DPI retained. Packaged macOS build compiles the Apple Vision helper; runtime is explicitly unavailable without it. |
| Evidence and source provenance | Implemented and verified | Block IDs, coordinates, confidence, route, warnings, alternatives, repair history, source crops, overlay exports and manifests retained. |
| Philon IR version | 0.3.0 in both | The one number the two projects may not drift on: it names the evidence shape a consumer reads, and `validate_ir` refuses any other. 0.3.0 added the page's own `/Rotate`, the source-declared links measured onto each block, and the page selection a conversion covers. A cache entry is named after the IR version it holds, so an entry written against 0.2.0 is never reached rather than read and rejected. |
| Output formats | Implemented and verified | Markdown, semantic HTML, IR, Marker-style clean-room JSON, chunks, optional embeddings, image assets, CSV, evidence and manifest. |
| Source/output/review UI | Implemented and smoke-tested | Fit/actual-size, zoomable source preview, page navigation, PDF/normalized-image overlays, block selection, in-app edit, candidate restore, repair request and crop access. |
| Persistent batch behavior | Implemented and verified | SQLite queue survives process restart, recovers interrupted running item, supports pause-after-current, cancel pending, retry, resume and export completed bundles. |
| Local model governance | Implemented and verified | Canonical manifest, approval/integrity/licence gates, offline discovery/readiness, manual-only Qwen/olmOCR, optional BGE-M3 sidecar. |
| Benchmark harness | Implemented and verified | Canonical cold/warm cache timing, contract checks, private-gold metrics and isolated comparator execution. |
| Local-only, licence, SBOM, fuzz/release checks | Implemented and verified | Python policy scripts, CycloneDX SBOM, engine fuzz tests and macOS package script. The SBOM policy requires a resolved version and a declared distribution for every component, and fails if a runtime requirement is undeclared. |

## Validation performed

| Check | Result |
|---|---|
| Canonical engine + fuzz + benchmark suite | 141 passed, 2 Apple Vision integration tests skipped because the helper was not enabled in this validation session. |
| Desktop persistence unit tests | 3 passed. |
| Policy/SBOM checks | Local-only, licence, and SBOM policies passed. |
| Desktop startup | Qt application constructed off-screen; six native workspace/secondary pages available. |
| Single-document end-to-end | Generated local PDF passed preflight; Verified conversion produced 2 evidence-linked blocks, a preview raster and a copied 10-file export bundle. The unavailable local BGE runtime emitted `EMBEDDING_FAILED` instead of aborting conversion. |
| Batch end-to-end | Two generated local PDFs completed from the SQLite queue; history entry persisted. |

## Explicit external gates and intentional exclusions

- **Apple Vision OCR:** included in source and package build, but not exercised here because its compiled macOS helper was not enabled. Absence is reported as evidence; OCR is never invented.
- **Qwen 3.8, olmOCR, BGE-M3:** Philon never downloads or activates these. They need locally managed files, a compatible local runtime, and the manifest's approval gate. BGE failures are retained as warnings and omit vectors.
- **Private corpus accuracy claims:** no private gold corpus was provided. The harness is present, but no claims of Marker parity, Docling parity, or superiority are made.
- **Geometric table/formula recognition:** deliberately unavailable in the canonical product. Philon exports only deterministically proven native tables and flags formulas; manual repair remains bounded, local and unselected by default.
- **DOCX, EPUB, TEI/JATS, ALTO/hOCR:** research-roadmap formats and intentionally excluded from the current source product, so they are not added in this parity port.
- **Qt's licence:** the bundle ships PySide6 and shiboken6 under LGPL-3.0-only while Philon's own source is MIT. They are unmodified, used through the public API, and present as separate dynamic libraries; distribution must keep them replaceable and carry the LGPL notice. The decision is recorded in the SBOM and enforced by the SBOM policy. The source project ships no copyleft component and refuses one mechanically, which is the one place the two policies deliberately differ.
- **Release signing/notarization:** packaging produces an ad-hoc signed, un-notarized local app with no Developer ID. Those credentials and notarization require the owner.

