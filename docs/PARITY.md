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

`zsh scripts/install-git-hooks.sh` additionally installs it as a pre-commit
hook, so the check runs on every commit rather than whenever `verify-release`
is next run. The hook and its installer are on the byte-identical list too: a
hook enforcing one thing here and another thing in the source project would be
worse than no hook.

| Area | Status | Evidence |
|---|---|---|
| Source GUI parity: skin, layout, splash, dark mode | Implemented | `philon_desktop/gui` ports the source `src/styles.css` design tokens (light and dark), the three-panel conversion grid, batch queue/report, secondary workspaces, first-launch splash at the original 640×580 measurements, and the maker's mark. Phosphor icon path data (MIT) is rendered natively; off-screen GUI tests cover tokens, icons, shell structure and ported logic. |
| Native desktop shell and menus | Implemented | PySide6 Qt production binding; native File/View menus and keyboard shortcuts in `philon_desktop/app.py`. PyQt6 remains a development-only fallback when PySide6 is not installed. |
| Single Job and Batch primary workspaces | Implemented | Workspace command bar with Single Job/Batch job tabs; Library (with count badge), Models, Diagnostics and Settings live in the header navigation, matching the source application's v0.2 command layout. |
| PDF/image safety preflight | Implemented and verified | Canonical bounded preflight rejects invalid, empty, encrypted, oversized, malformed, multi-frame and decompression-bomb inputs. |
| Native PDF/OCR adaptive routing | Implemented; Apple Vision runtime gated | Canonical PDFium extraction, native-text health, per-page routes and adaptive DPI retained. Packaged macOS build compiles the Apple Vision helper; runtime is explicitly unavailable without it. |
| Evidence and source provenance | Implemented and verified | Block IDs, coordinates, confidence, route, warnings, alternatives, repair history, source crops, overlay exports and manifests retained. |
| Model provisioning | Implemented in both; first-run prompt source-only | Fifteen declared packs, eight of them fetchable. Discovery finds a copy already on the machine first — across the HuggingFace hub cache, the app-local stores and Philon's own managed store — and only offers to download what is genuinely absent. Every declared file carries a SHA-256 computed from a real copy, so a download is checked against known-good bytes rather than against whatever a host serves. **The first-run prompt is source-only.** Until 2026-08-23 this row claimed that first launch opens the Models pane, reports what it found, and records that it did. It does not, and never did: the port has no `modelSetupSeen` equivalent and starts on the workspace on a fresh `PHILON_DATA_DIR`, which was confirmed by constructing the shell against an empty one. `model_setup_summary` exists and is correct, but it is read only in the Models pane's own heading, where a person has already navigated. Whether the port should gain the prompt is the owner's call; what is fixed here is the record. |
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
| Canonical engine + fuzz + benchmark suite | 244 passed. |
| Desktop persistence unit tests | 5 passed (queue recovery, history clean-up, preference validation incl. enabled model ids). |
| Desktop shutdown tests | 6 passed; **71** desktop tests in total, all off-screen. Four are new and cover helper processes: that the bounded wait alone leaves one running, that the reap ends it, that ending it is what unblocks the worker, and that the listing never reports the `ps` doing the listing. |
| Desktop GUI shell tests | 52 passed off-screen: theme tokens, icon set, shell structure, splash gating, preference round-trips, the ported composition helpers, the interface face resolving to a family that is installed, the page-selection control, and the rotation, source-link, ruled-table and measured-formula evidence rows. Added 2026-08-23 from the coverage audit below: convert-button gating across all six of its branches, the error banner, the library's two-stage removal confirmation, the model pane's approval gate, and the byte-size, timestamp and model-setup summary helpers. |
| Policy/SBOM checks | Local-only, model-fetch, licence, SBOM and engine-parity policies passed -- five gates. |
| Desktop startup | Qt application constructed off-screen with five agreeing views. The check asserts that the stack, the ordered names and the header tabs describe the same set, rather than a fixed page count, so a view added to one and forgotten in the others is caught. It applies the interface face first, as `main()` does. |
| Single-document end-to-end | A generated four-page PDF, one page at `/Rotate 90` and one carrying a link annotation, converted under Verified: completed with no warnings, 4 pages, 7 evidence-linked blocks, rotation recorded as `[0, 0, 90, 0]`, one link measured onto the characters it covers, and 8 outputs written. |
| Batch end-to-end | Two generated local PDFs completed from the SQLite queue; history entry persisted. |
| Page selection end-to-end | The same document converted for pages `2-3` reported `page_selection: 2-3` and an IR carrying pages `[2, 3]`, into an export directory suffixed `-pages-2-3` so it cannot be mistaken for, or written over, the whole-document conversion. |

## Desktop coverage against the source project's suite

The port had 44 desktop tests against the source project's 104, and nobody had
established what that difference was made of. Audited 2026-08-23, behaviour by
behaviour rather than by counting. It is now 71, and the remaining difference is
described rather than left as a number.

**The engine difference is fully explained and is not a gap.** The source runs
its engine behind an authenticated Unix-socket bridge and tests it with
`engine/test_socket.py` (21 tests); the port runs the engine in-process and has
no bridge. 265 - 21 = 244, which is exactly the port's engine count. Nothing is
missing there. What the port owes instead is in the next section.

### Difference that is framework, not coverage

- **8 theme and icon tests are port-only.** The source's design tokens are CSS,
  which `vitest` does not test; the port re-expresses them as Python and so must
  check that light and dark carry the same keys and that the accents match the
  source stylesheet.
- **`ErrorBoundary.test.tsx` (5) has no counterpart and should not.** A React
  error boundary is a React mechanism. Qt has no equivalent surface to test.
- **`format.test.ts` (35) is thinner than it looks.** It unit-tests ten tiny
  pure functions. The port has the same behaviours, but some are inlined into
  their one caller — `isAnchorableLink` lives inside `link_summary` — so one
  port test covers what two source tests cover. `basename` is `Path().name`:
  stdlib, not the port's code to test.
- **Progress belonging to a different job** cannot happen here. The source
  filters progress by `job_id` because its bridge multiplexes. `_spawn` refuses
  a second worker under the same name, so only one conversion exists at a time;
  the property is structural rather than filtered, and a test would assert the
  absence of a mechanism.

### Gaps that were real, now closed

Each was a behaviour the source's suite covers and the port's did not. Two were
not merely untested — the code was wrong:

- **Convert-button gating.** The source spends four tests on it; the port had it
  as one compound expression in `_refresh_command_bar` and no test at all. All
  six branches are now covered, including that one preflight-blocked document
  stops the whole queue. Deleting the `not blocked` term makes the new test fail;
  that was checked.
- **The model pane's approval gate.** A policy-blocked pack must not offer a
  working switch or a download button. Deleting the `approved` term makes the new
  test fail; that was checked too.
- **The error banner** — that a local failure is shown rather than swallowed,
  that a conversion which produced nothing says why, and that the banner can be
  put away.
- **The library's removal confirmation.** A two-stage in-page confirm rather than
  a modal, so it is testable off-screen without putting a dialog on the screen.
- **`format_timestamp` — a real divergence, fixed.** The source names an absent
  timestamp "Date not recorded", because a record with no time is a fact about
  the record. The port returned the empty string, which rendered as a bare
  separator in the library row and read as a rendering fault.
- **`bytes_label`** — matched the source exactly and was untested, including the
  property that a non-empty file is never reported as "0 KB".
- **`model_setup_summary`** — untested, and correct: built-in runtimes and
  policy-blocked packs pad neither half of the count.

### Gaps that are real and still open

Named rather than closed, because closing them is a decision and not a test:

- **The first-run model prompt does not exist in the port.** The source opens
  model setup once, records that it did, and says what it found and what it could
  fetch (`modelSetupSeen`, two tests). The port has none of it and starts on the
  workspace. This row of the table above asserted the opposite until today; the
  claim is corrected there. Whether the port should gain the behaviour is the
  owner's call — it interacts with the about screen the port deliberately opens
  on *every* launch.
- **Stored preferences are validated on write and not on read.** The source
  parses `localStorage`, which any page can corrupt, and so validates on read
  across 14 tests. The port reads its own SQLite through `save_preferences`,
  which validates, so bad data can only arrive by editing the database directly.
  Lower severity and a genuinely different threat model, but it is not the same
  guarantee and should not be recorded as one.

## Process lifetime without a socket bridge

The source project's `engine/test_socket.py` covers auth, cancellation, and
taking the engine's grandchild down with the bridge. The port needs none of the
auth — there is no socket and no token — but the process-lifetime half applies
unchanged to whatever it does instead, and nobody had checked it.

**It was broken, and both halves of it were.** The engine runs in-process on a
`WorkThread`, so a helper it starts — the Apple Vision binary at a 90s timeout,
a llama.cpp run at 240s for embeddings or 420s for a repair — is a direct child
of the application rather than of a bridge whose death would take it along.
`closeEvent` called `wait_for_workers()`, whose result it discarded. Reproduced
against the real classes on 2026-08-23:

    child pid 4148 spawned
    wait_for_workers -> False
    child alive after the wait gave up: True
    QThread: Destroyed while thread '' is still running     # SIGABRT, exit 134

So quitting during a repair left llama.cpp holding a core with no window left to
cancel it from, *and* aborted the application on the way out, because Qt kills
the process when a running `QThread` is destroyed.

One mechanism fixes both. `stop_leftover_children()` lists this process's own
direct children, signals them, and kills anything that outlives the grace
period. The worker was blocked in `subprocess.run` waiting on exactly that
child, so ending it is also what lets the thread return — which is why
`closeEvent` now waits a second time instead of giving up:

    if not wait_for_workers():
        stop_leftover_children()
        wait_for_workers(2000)

`ps` is itself a child of this process and reports itself, so the listing
excludes it by pid; a child that exits between being listed and being signalled
is normal rather than an error, and every signal tolerates it. Four tests in
`tests/test_shutdown.py` cover it, including one that asserts the old behaviour
— that waiting alone leaves the helper running — so the reason the reap exists
is a test rather than a comment.

The engine was not touched. Every helper it starts already runs under
`subprocess.run(timeout=...)`, which kills its own child on timeout; what was
missing was only the case where the application goes away first, and that is the
desktop layer's to answer.

## Explicit external gates and intentional exclusions

- **Apple Vision OCR:** included in source and package build, but not exercised here because its compiled macOS helper was not enabled. Absence is reported as evidence; OCR is never invented.
- **Qwen 3.8, olmOCR, BGE-M3:** Philon never activates these on its own. They need locally managed files, a compatible local runtime, and the manifest's approval gate. BGE failures are retained as warnings and omit vectors.
- **Model packs a person asks for** are downloaded from their publisher over HTTPS, verified against the SHA-256 recorded in the manifest, and installed into a store Philon owns. Conversion still opens no connection: the fetcher is a separate module, the only file exempt from the local-only gate, and the gate checks the engine never imports it at module scope.
- **Private corpus accuracy claims:** no private gold corpus was provided. The harness is present, but no claims of Marker parity, Docling parity, or superiority are made.
- **Geometric table/formula recognition:** deliberately unavailable in the canonical product. Philon exports only deterministically proven native tables and flags formulas; manual repair remains bounded, local and unselected by default.
- **DOCX, EPUB, TEI/JATS, ALTO/hOCR:** research-roadmap formats and intentionally excluded from the current source product, so they are not added in this parity port.
- **Qt's licence:** the bundle ships PySide6 and shiboken6 under LGPL-3.0-only while Philon's own source is MIT. They are unmodified, used through the public API, and present as separate dynamic libraries; distribution must keep them replaceable and carry the LGPL notice. The decision is recorded in the SBOM and enforced by the SBOM policy. The source project ships no copyleft component and refuses one mechanically, which is the one place the two policies deliberately differ.
- **Release surface:** the port had no `releases/` directory and no release
  workflow where the source project has both, and `scripts/package-macos.sh`
  stopped at `dist/Philon.app` — a directory, which is not something a person
  downloads, and which loses the ad-hoc seal the script had just applied if it is
  copied rather than `ditto`d. Packaging now builds a disk image, runs `hdiutil
  verify` on it and records its SHA-256 against the bare filename;
  `.github/workflows/release.yml` does the same on a `v*` tag. The built image is
  **not** committed, which is the one place the port deliberately diverges from
  the source project's arrangement: measured 2026-08-23 the port's bundle is
  127 MB on disk and 48 MB compressed against the source's 24 MB DMG, because it
  carries its own interpreter and the Qt frameworks where the source borrows the
  system webview. A history of 48 MB images is not a repository anyone can clone.
  `releases/README.md` records the posture and the numbers; the workflow uploads
  the image as a build artifact so a build stays retrievable.
- **Release signing/notarization:** packaging produces an ad-hoc signed, un-notarized local app with no Developer ID. `codesign --verify --strict` passes — the ad-hoc seal is what makes that true — but `TeamIdentifier` is not set and Gatekeeper still refuses a downloaded copy on another machine. Those credentials and notarization require the owner.

