# Open questions — brief for a fresh session

Written 2026-08-23, at the end of a session that closed the geometric
table-recovery gap and four features after it. Everything below is either a
decision nobody has taken yet or a risk nobody has retired. It is written to be
handed to a session that has none of the context that produced it.

Nothing here is broken. Both projects are green, clean and pushed.

---

## Where things stand

Two repos, kept in parity:

    /Users/tsevis/AI/ClaudeCode/philon      (Tauri/React + Rust + Python engine)
    /Users/tsevis/AI/ClaudeCode/philon_p    (PySide6 Qt port of the same engine)

Both on `main`, clean, pushed, `main...origin/main`.

    cd philon    && npm run release:verify                             # exit 0
    cd philon_p  && PHILON_DATA_DIR=$(mktemp -d) zsh scripts/verify-release.sh   # exit 0

Baselines: philon 106 workspace + 250 engine + 8 Rust, 4 policy gates;
philon_p 44 desktop + 229 engine/fuzz/bench, 4 policy gates, shell with 5
agreeing views, zero `qt.qpa` font warnings. IR is at **0.5.0**. The model
manifest declares **15 packs, 8 fetchable, 3 unapproved**.

`engine/philon_engine.py` must be identical in both repos except **one**
documented hunk (a `RuntimeError` in an `except` tuple, commented in the source,
recorded in `philon_p/docs/PARITY.md`). `engine/model_fetch.py` and
`engine/model-manifest.json` must be byte-identical in both. **Nothing checks
any of this automatically** — see question 3.

---

## 1. The download path has never touched a real network

**The most likely thing to be quietly broken.**

Philon can now fetch a model pack, and offers to on first launch. Every test
mocks the connection, so what is proven is the *policy* — HTTPS, an exact-match
host allow-list, redirect re-checking, a SHA-256 that must match before anything
is installed — and not that a real fetch succeeds. HuggingFace redirects blob
requests to a CDN, and redirect chains and CDN behaviour are exactly where this
kind of code fails.

One real download of `smolvlm-500m-local-candidate` (520 MB, the smallest
fetchable pack) would settle it. That pack is already present on this machine,
so testing the fetch means pointing it at an empty `PHILON_DATA_DIR` or removing
the discovered copy first — otherwise discovery short-circuits the download.

**Question:** run it, and if the redirect chain does not behave, fix
`_open_checked` in `engine/model_fetch.py` rather than widening the allow-list.

## 2. Seven licences are owner-confirmed, not verified

The manifest distinguishes two things and the distinction is load-bearing:

* `"Apache-2.0 — official olmOCR-2 model card verified 2026-08-16"` — checked.
* `"Apache-2.0 (SmolVLM) — owner-confirmed 2026-08-23; the GGUF mirror on this
  system carries weights only, so the upstream model card was not verifiable
  offline"` — asserted by the owner.

The GGUF mirrors in `~/.cache/huggingface/hub` contain weights and nothing else:
no README, no LICENSE. Seven packs are approved on the owner's say-so with that
stated plainly rather than with a fabricated verification date.

Two packs carry real restrictions and say so in their own entries:
`gemma-4-12b-local-candidate` (Gemma Terms of Use — **not** OSI, terms travel
with redistribution) and `minicpm-v-4.6-local-candidate` (commercial use
requires registration with the publisher). Both are authorized for private local
use and marked blocked from a distributed release.

**Question:** if Philon is ever distributed, each of the seven needs a real
model-card check. Until then, is owner-confirmed the right standing posture, or
should the restricted two be moved back to `approved: false`?

Related, and unresolved from the session: when asked which packs to approve, the
owner selected *both* "keep current approvals only" *and* the three options that
add approvals. It was read as approve-everything. **Confirm that reading.**

## 3. Engine parity is enforced by hand and by nothing else

`diff` over the two engine copies must report exactly one hunk. It is checked
manually at every step and there is no gate, no hook, and no CI.

This is the single most likely thing to break silently, because it breaks
without any test failing in either repo — each repo tests its own copy, and both
suites pass happily while the two engines drift.

**Question:** add a gate. The cheapest honest version is a script both
`verify-release` runs, given the other repo's path when it is present and
skipping loudly when it is not. A pre-commit hook is the alternative.

## 4. CI never runs

`release:verify` is only ever as current as the last person who ran it locally.
There is a `.github/` directory but nothing runs. GUI tests must stay off-screen
(`QT_QPA_PLATFORM=offscreen` at import) and must never be run without asking.

**Question:** is CI wanted at all, given the project is local-first and the
suites depend on a macOS Vision helper and a local model inventory? A partial CI
that runs the engine suite and the four policy gates on Linux would catch
parity drift and gate regressions without pretending to run the GUI.

## 5. Marker 2.0 is Apache-2.0, and the standing constraint assumed GPL

**This one changes a premise, not just a fact.**

`~/AI/marker` is at **version 2.0.0** on `master`, 7 local commits ahead of
`origin/master` (`datalab-to/marker`, no push access), 4 files dirty. Those
commits are fixes written *for* Marker and are unrelated to Philon.

The standing Philon constraint has been: *"Philon is MIT and reuses no Marker
code; keep it that way"* — with the rationale that Marker is GPL and therefore
incompatible.

**That rationale is out of date.** Marker relicensed: the history goes GPL →
OpenRAIL → Apache-2.0 (`65f73c9`, "Release prep: benchmarks harness,
competitive results, Apache 2.0"). The 2.0 `LICENSE` is the Apache License 2.0
with zero GPL references, and `pyproject.toml` says
`license = { text = "Apache-2.0" }`.

Apache-2.0 is compatible with an MIT project, subject to attribution and NOTICE
obligations. So the *legal* barrier to reading or reusing Marker is gone.

The constraint may still be the right call — a clean-room implementation with
four runtime dependencies is a deliberate product position, and Marker brings a
large ML stack Philon has spent real effort avoiding. But it should now be held
for that reason and stated as such, rather than for a licence reason that no
longer holds.

**Questions:**
- Restate the constraint on its real grounds (clean-room, dependency budget),
  or relax it now that Apache-2.0 permits reuse with attribution?
- The README says "Philon does not reuse Marker code or models" and the
  compatibility export was renamed from `marker_json` to `page_tree` partly on
  licence grounds. Does any of that wording need revisiting?
- Marker 2.0 is a newer comparison target than the one Philon's benchmark
  claims were measured against. The heading-detection results recorded in the
  README ("100% recall at 100% precision" on three papers, etc.) were measured
  against an **older Marker**. Those numbers are now unlabelled as to version
  and should either be re-run against 2.0 or annotated with the version they
  were taken against.

## 6. Benchmarks are blocked, not absent

Public claims against Marker or Docling remain gated until the version-pinned
corpus, hardware and methodology in `bench/README.md` have been run. The harness
exists; the runs do not. See also the version-labelling problem in question 5.

## 7. Smaller decisions left open

- **Markdown and merged cells.** A recovered table with merged cells renders
  `colspan`/`rowspan` in HTML. Markdown cannot express either, so it keeps the
  grid square and leaves the covered opening blank rather than repeating a value
  the page wrote once. Faithful, but a reader may prefer the columns collapsed.
  On the reference paper this shows up as a 14x6 grid representing 3 logical
  columns.
- **Formula semantics.** Philon recovers *typesetting* from measured baselines
  and sizes — it writes `x^{2}` — and makes no claim about what an equation
  means. Parsing structure (fractions, radicals, matrices) is unstarted and
  would need either a much larger geometric model or a VLM.
- **`layout-ocr` and `local-repair`** remain `approved: false` with
  `"UNRESOLVED - benchmark and legal review required before distribution"`.
- **The CCITT fax defect** in `documents/CACHE_BOUNDARY_AND_ASSET_COST.md`: one
  image fails to decode and produces different bytes on every extraction, so its
  `bytes_sha256` provenance changes run to run. Recorded, deliberately unfixed.

---

## Standing constraints that still hold

- **Parity.** One documented hunk in `philon_engine.py`; `model_fetch.py` and
  `model-manifest.json` byte-identical. `diff` them.
- **Coordinate frame.** PDFium reports page size with `/Rotate` applied and text
  and path coordinates without it. Everything downstream is in the **displayed**
  frame. Put every recovered rectangle through `bbox_to_displayed_frame`, and
  classify rules *after* that conversion, not before.
- **IR version.** Adding a field means bumping it and saying what changed. The
  cache key carries the version, so old entries become unreachable rather than
  rejected — by design.
- **Prove or mark, never invent.** Ruled tables and measured script geometry are
  provable. Whitespace alignment is inference and is not emitted. Uncertainty
  becomes a warning.
- **Local only.** Conversion opens no connection. `engine/model_fetch.py` is the
  single named exemption; the gate checks that exemption is load-bearing and
  that the engine never imports the fetcher at module scope.
- **Tests.** Build PDF fixtures inline, byte by byte.
- **GUI tests must not open windows**, and must never be run without asking.
