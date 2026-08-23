# Python desktop parity report

## Scope and comparison

This port was compared against the authored source in `/Users/tsevis/AI/ClaudeCode/philon`: the Tauri/React workspace, Rust host and SQLite queue, canonical Python engine, manifest, benchmark harness, tests, packaging/release scripts, policies, and research RFCs. The original repository was read-only throughout.

The Python port retains the original engine and harness as its conversion/evidence authority. Its one intentional engine hardening converts a non-zero local BGE-M3 process into `EMBEDDING_FAILED` evidence rather than failing an otherwise valid Verified conversion. No vector is emitted in that case.

### The two engine copies had drifted, and this claim was not true

Until this revision the two copies of `engine/philon_engine.py` differed by **46
hunks**, not by the one divergence above. `diff` now reports exactly one hunk:
the `RuntimeError` in that `except` tuple, carrying a comment that says so, and
`engine/test_engine.py` is the same suite in both repositories, plus one port-only test that guards the divergence itself (`test_verified_embedding_runtime_failure_becomes_evidence_not_document_failure`).

The drift was not cosmetic and the port was the copy that was behind. It was
missing a fix that stops `action_convert` mutating its caller's request object
and aliasing that list into the response; it had no `enabled_model_ids` gate, so
a repair could use an approved pack the user had not enabled; its socket bridge
forwarded no progress and no `job_id`; and it carried **two live definitions
each** of `render_markdown` and `render_html`, the first of each being dead code
shadowed by the second. Nothing detected any of it, because each repository
tests its own copy and both suites passed throughout.

Anything that changes this file must change it in both places, and until
2026-08-23 `diff` over the two copies -- run by hand, by someone who remembered
-- was the only check that said whether that happened.

`tests/parity_policy.py` is that check now. It is a fourth shared file,
byte-identical in both repositories, checking itself alongside the other three,
and it runs inside both `verify-release` paths. Given the peer checkout it holds
`model_fetch.py`, `model-manifest.json` and itself to byte identity, and holds
`philon_engine.py` to exactly one hunk which must be *the documented one*: a
single line of code on this side differing from the source's by the one added
exception type, carrying a comment that names this file. A second divergence
hidden inside that hunk fails the gate. Without the peer on the machine it skips
loudly and passes, because one repository alone is a legitimate way to work;
`PHILON_PARITY_REQUIRE=1` makes the skip an error, and CI sets it.

| Area | Status | Evidence |
|---|---|---|
| Source GUI parity: skin, layout, splash, dark mode | Implemented | `philon_desktop/gui` ports the source `src/styles.css` design tokens (light and dark), the three-panel conversion grid, batch queue/report, secondary workspaces, first-launch splash at the original 640×580 measurements, and the maker's mark. Phosphor icon path data (MIT) is rendered natively; off-screen GUI tests cover tokens, icons, shell structure and ported logic. |
| Native desktop shell and menus | Implemented | PySide6 Qt production binding; native File/View menus and keyboard shortcuts in `philon_desktop/app.py`. PyQt6 remains a development-only fallback when PySide6 is not installed. |
| Single Job and Batch primary workspaces | Implemented | Workspace command bar with Single Job/Batch job tabs; Library (with count badge), Models, Diagnostics and Settings live in the header navigation, matching the source application's v0.2 command layout. |
| PDF/image safety preflight | Implemented and verified | Canonical bounded preflight rejects invalid, empty, encrypted, oversized, malformed, multi-frame and decompression-bomb inputs. |
| Native PDF/OCR adaptive routing | Implemented; Apple Vision runtime gated | Canonical PDFium extraction, native-text health, per-page routes and adaptive DPI retained. Packaged macOS build compiles the Apple Vision helper; runtime is explicitly unavailable without it. |
| Evidence and source provenance | Implemented and verified | Block IDs, coordinates, confidence, route, warnings, alternatives, repair history, source crops, overlay exports and manifests retained. |
| Model provisioning | Implemented in both | Fifteen declared packs, eight of them fetchable. Discovery finds a copy already on the machine first — across the HuggingFace hub cache, the app-local stores and Philon's own managed store — and only offers to download what is genuinely absent. Every declared file carries a SHA-256 computed from a real copy, so a download is checked against known-good bytes rather than against whatever a host serves. First launch opens the Models pane, reports what it found, and records that it did. |
| Philon IR version | 0.5.0 in both | The one number the two projects may not drift on: it names the evidence shape a consumer reads, and `validate_ir` refuses any other. 0.5.0 added the merged cells a table's missing rules prove, the formula a page's own script geometry proves, and the provenance an automatic local repair leaves behind; 0.4.0 had added the tables recovered from the rules a page draws; 0.3.0 had added the page's own `/Rotate`, the source-declared links measured onto each block, and the page selection a conversion covers. A cache entry is named after the IR version it holds, so an entry written against an older shape is never reached rather than read and rejected. |
| Rotated pages, running heads, source links, page ranges, ruled tables, formulas, automatic repair | Implemented in both | Eight engine changes carried identically in each copy. A rotated page is measured in the frame it is displayed in, rather than page size with `/Rotate` applied and text rectangles without it. A running head carrying its folio is recognised as repeating. Link annotations are measured onto the characters they cover, and only http, https and mailto become clickable. A job converts a page range, which is part of the cache key and the export directory name. A table the page rules is recovered from those rules and exported as a real table, and rules are classified only after the rectangle is moved into the displayed frame, so a quarter-turned table does not arrive transposed. A rule that stops is read as the merged cell it leaves, a table continued onto the next page is recognised by the columns it repeats rather than a heading it does not, and a formula is recognised from the baselines and sizes the page measurably set. Automatic local repair is off unless a run asks for it, retains the extracted text beside every replacement, and refuses a candidate that fails the format checks. `diff` over the two engine copies still reports one hunk. |
| IR 0.5.0 evidence in the interface | Implemented | The page-selection control asks for a range the engine already converts, refusing a malformed one before a job starts; the evidence summary reports the page's own rotation, the source-declared links measured onto the selected block including a target withheld as unanchorable, the tables recovered from the page's rules including the cells their missing rules merged, and what the page's script geometry made of the block — each counting what it could not recover rather than hiding it. |
| Output formats | Implemented and verified | Markdown, semantic HTML, IR, page-tree interchange JSON, chunks, optional embeddings, image assets, CSV, evidence and manifest. |
| Source/output/review UI | Implemented and smoke-tested | Fit/actual-size, zoomable source preview, page navigation, PDF/normalized-image overlays, block selection, in-app edit, candidate restore, repair request and crop access. |
| Persistent batch behavior | Implemented and verified | SQLite queue survives process restart, recovers interrupted running item, supports pause-after-current, cancel pending, retry, resume and export completed bundles. |
| Local model governance | Implemented and verified | Canonical manifest, approval/integrity/licence gates, offline discovery/readiness, optional BGE-M3 sidecar. Qwen/olmOCR repair is manual unless a run explicitly asks for an automatic pass, which retains the extracted text beside every replacement. The Qwen 2.5 VL **3B** copy is blocked on its Research Licence; that is specific to the checkpoint, not the family. |
| Benchmark harness | Implemented and verified | Canonical cold/warm cache timing, contract checks, private-gold metrics and isolated comparator execution. |
| Local-only, licence, SBOM, fuzz/release checks | Implemented and verified | Python policy scripts, CycloneDX SBOM, engine fuzz tests and macOS package script. The SBOM policy requires a resolved version and a declared distribution for every component, and fails if a runtime requirement is undeclared. |

## Validation performed

| Check | Result |
|---|---|
| Canonical engine + fuzz + benchmark suite | 235 passed. |
| Desktop persistence unit tests | 5 passed (queue recovery, history clean-up, preference validation incl. enabled model ids). |
| Desktop shutdown tests | 2 passed; 42 desktop tests in total, all off-screen. |
| Desktop GUI shell tests | 35 passed off-screen: theme tokens, icon set, shell structure, splash gating, preference round-trips, the ported composition helpers, the interface face resolving to a family that is installed, the page-selection control, and the rotation, source-link, ruled-table and measured-formula evidence rows. |
| Policy/SBOM checks | Local-only, model-fetch, licence, SBOM and engine-parity policies passed -- five gates. |
| Desktop startup | Qt application constructed off-screen with five agreeing views. The check asserts that the stack, the ordered names and the header tabs describe the same set, rather than a fixed page count, so a view added to one and forgotten in the others is caught. It applies the interface face first, as `main()` does. |
| Single-document end-to-end | A generated four-page PDF, one page at `/Rotate 90` and one carrying a link annotation, converted under Verified: completed with no warnings, 4 pages, 7 evidence-linked blocks, rotation recorded as `[0, 0, 90, 0]`, one link measured onto the characters it covers, and 8 outputs written. |
| Batch end-to-end | Two generated local PDFs completed from the SQLite queue; history entry persisted. |
| Page selection end-to-end | The same document converted for pages `2-3` reported `page_selection: 2-3` and an IR carrying pages `[2, 3]`, into an export directory suffixed `-pages-2-3` so it cannot be mistaken for, or written over, the whole-document conversion. |

## Explicit external gates and intentional exclusions

- **Apple Vision OCR:** included in source and package build, but not exercised here because its compiled macOS helper was not enabled. Absence is reported as evidence; OCR is never invented.
- **Qwen 3.8, olmOCR, BGE-M3:** Philon never activates these on its own. They need locally managed files, a compatible local runtime, and the manifest's approval gate. BGE failures are retained as warnings and omit vectors.
- **Model packs a person asks for** are downloaded from their publisher over HTTPS, verified against the SHA-256 recorded in the manifest, and installed into a store Philon owns. Conversion still opens no connection: the fetcher is a separate module, the only file exempt from the local-only gate, and the gate checks the engine never imports it at module scope.
- **Private corpus accuracy claims:** no private gold corpus was provided. The harness is present, but no claims of Marker parity, Docling parity, or superiority are made.
- **Geometric table/formula recognition:** deliberately unavailable in the canonical product. Philon exports only deterministically proven native tables and flags formulas; manual repair remains bounded, local and unselected by default.
- **DOCX, EPUB, TEI/JATS, ALTO/hOCR:** research-roadmap formats and intentionally excluded from the current source product, so they are not added in this parity port.
- **Qt's licence:** the bundle ships PySide6 and shiboken6 under LGPL-3.0-only while Philon's own source is MIT. They are unmodified, used through the public API, and present as separate dynamic libraries; distribution must keep them replaceable and carry the LGPL notice. The decision is recorded in the SBOM and enforced by the SBOM policy. The source project ships no copyleft component and refuses one mechanically, which is the one place the two policies deliberately differ.
- **Release signing/notarization:** packaging produces an ad-hoc signed, un-notarized local app with no Developer ID. Those credentials and notarization require the owner.

