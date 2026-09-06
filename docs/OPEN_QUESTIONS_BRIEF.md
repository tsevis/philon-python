# Open questions — brief for a fresh session

Written 2026-08-23 and revised the same day, after a session that took the six
decisions the first draft asked for and retired three of the risks. It is
written to be handed to a session that has none of the context that produced it.

Nothing here is broken. Both projects are green, clean and pushed.

---

## Where things stand

Two repos, kept in parity:

    /Users/tsevis/AI/ClaudeCode/philon      (Tauri/React + Rust + Python engine)
    /Users/tsevis/AI/ClaudeCode/philon_p    (PySide6 Qt port of the same engine)

Both on `main`, clean, pushed, `main...origin/main`.

    cd philon    && npm run release:verify                             # exit 0
    cd philon_p  && PHILON_DATA_DIR=$(mktemp -d) zsh scripts/verify-release.sh   # exit 0

Baselines: philon 104 workspace + 265 engine + 9 Rust, 5 policy gates;
philon_p 44 desktop + 244 engine/fuzz/bench, 5 policy gates, shell with 5
agreeing views, zero `qt.qpa` font warnings. IR is at **0.5.0**. The model
manifest declares **15 packs, 7 fetchable, 3 unapproved**.

`engine/philon_engine.py` must be identical in both repos except **one**
documented hunk (a `RuntimeError` in an `except` tuple, commented in the source,
recorded in `philon_p/docs/PARITY.md`). `engine/model_fetch.py`,
`engine/model-manifest.json`, `tests/parity_policy.py`,
`scripts/git-hooks/pre-commit` and `scripts/install-git-hooks.sh` must be
byte-identical in both. `tests/parity_policy.py` is what checks all of that; it
runs in both `verify-release` paths and, once the hook is installed, on every
commit — see question 3.

---

## 1. The download path has now touched a real network — CLOSED, and it was broken

`smolvlm-500m-local-candidate` was fetched for real on 2026-08-23 against an
empty `PHILON_DATA_DIR`: 520 MB across two files, both digests matched, both
installed, 107 seconds. The fetch works.

**It worked for the wrong reason, and the fix is the interesting part.**

`_open_checked` followed redirects by hand and re-checked every hop against the
allow-list. That loop was unreachable. `urllib.request.urlopen` installs its own
redirect handler, follows hops itself, and returns only the final response — so
the allow-list was applied to the first URL and to nothing after it. Asking for
a file on `huggingface.co` returned a 200 from `us.aws.cdn.hf.co`, a host
`is_allowed_url` refuses, and nothing in the path had looked. Every test mocked
`_open_checked` itself, so the whole suite exercised the loop and never the
opener that bypassed it. This is exactly the shape of defect that only a real
connection finds.

Two changes:

* `_RefuseRedirects`, an `HTTPRedirectHandler` whose `redirect_request` returns
  `None`, installed on an opener the module builds and opens through. urllib now
  raises each 3xx instead of following it, and the hand-written loop sees every
  hop. `engine/test_engine.py` stands up a loopback server that redirects and
  asserts both halves — that the default opener follows it and that Philon's
  does not. That test fails against the old behaviour; it was checked.
* The allow-list was stale, not merely bypassed. HuggingFace no longer serves
  large files from `cdn-lfs*.huggingface.co`; it serves them from Xet, on a CDN
  host named for the region the client resolves to. `MODEL_HOST_ALLOWED_PARENTS`
  now names `cdn.hf.co`, and a host matches it as the bare parent or with a
  **leading dot** in front — so `us.aws.cdn.hf.co` is allowed and
  `cdn.hf.co.example.invalid` is not. This was a deliberate widening onto a
  second registrable domain, taken as a decision rather than absorbed as a
  matching accident. Exact match still governs every other host.

Both policy gates were extended to hold the module to the new property: they now
fail if the module calls `urlopen`, if the refusing handler is absent, or if a
parent domain is matched without the leading dot.

**What is still owed:** nothing for this pack. The other six fetchable packs have
never been downloaded, and the largest is a great deal bigger than 520 MB.

`gemma-4-12b-local-candidate` declared an eighth and no longer does. Its
`gemma-4-12B-it-Q4_K_M.gguf` answers **404** on `ggml-org/gemma-4-12B-it-GGUF`,
which now publishes Q4_0, Q8_0 and BF16 and no Q4_K_M at all — the repository
changed its published quantisations after the local copy was fetched. The
repository itself answers 200, so this is not a licence gate. The digest in the
manifest is genuine: the HuggingFace blob store names blobs by their digest and
the local file resolves to `1278394b…` at exactly the declared 7,381,382,048
bytes. What expired is availability, not honesty, and no correction to the name
can fix it. The `download` block is therefore gone and the pack is
discovery-only, which is what was already true of it.

## 2. Seven licences are owner-confirmed, not verified — posture CONFIRMED

The manifest distinguishes two things and the distinction is load-bearing:

* `"Apache-2.0 — official olmOCR-2 model card verified 2026-08-16"` — checked.
* `"Apache-2.0 (SmolVLM) — owner-confirmed 2026-08-23; the GGUF mirror on this
  system carries weights only, so the upstream model card was not verifiable
  offline"` — asserted by the owner.

The GGUF mirrors in `~/.cache/huggingface/hub` contain weights and nothing else:
no README, no LICENSE. Seven packs are approved on the owner's say-so with that
stated plainly rather than with a fabricated verification date.

The ambiguous instruction from the previous session — both "keep current
approvals only" *and* the three options that add approvals — was put back to the
owner and **read as approve-everything, confirmed 2026-08-23**. The two
restricted packs stay approved: `gemma-4-12b-local-candidate` (Gemma Terms of
Use, **not** OSI, terms travel with redistribution) and
`minicpm-v-4.6-local-candidate` (commercial use requires registration with the
publisher). Both are authorized for private local use and marked blocked from a
distributed release. The manifest's `policy` note now records the confirmation
and its date.

**What is still owed:** if Philon is ever distributed, each of the seven needs a
real model-card check. That obligation is now written into the manifest itself
rather than living only here.

## 3. Engine parity is now enforced by a gate — CLOSED

`tests/parity_policy.py` is a fourth shared file, byte-identical in both repos,
and it checks itself along with the other three. It runs in both
`verify-release` paths. Given the peer checkout it asserts that
`model_fetch.py`, `model-manifest.json` and the gate itself are byte-identical,
and that `philon_engine.py` differs by exactly one hunk which is *the documented
one* — one line of code on the port's side differing from the source's by the
single added exception type, plus a comment naming `docs/PARITY.md`. A second
divergence smuggled into that hunk fails. Each of those failure modes was
provoked against a mutated copy and confirmed to fail.

When the peer is not on the machine it **skips loudly** to stderr and exits 0,
because one repository alone is a legitimate way to work. `PHILON_PARITY_REQUIRE=1`
turns that skip into a failure, and the CI workflows set it — a gate that passes
without a peer to compare against is indistinguishable from one that compared
and was satisfied. `PHILON_PARITY_PEER` overrides the sibling-by-name search.

## 4. CI was never absent — it is blocked on billing

The first draft of this brief said CI never runs. That was half right.
`.github/workflows/verify.yml` and `release.yml` exist and are correctly wired.
Every run since 2026-08-21 failed in three to five seconds *at job start*, with:

> The job was not started because recent account payments have failed or your
> spending limit needs to be increased.

Both repositories are private and the existing job runs on `macos-15`, which
bills at ten times the clock. So what was needed was not CI but cheaper CI, and
an account that can start a job at all.

Added: an `engine-and-policy` job on `ubuntu-latest` in both repositories,
billed at 1x. It runs the gates, the engine, fuzz and benchmark suites, and —
uniquely — the parity gate, because that is the one check neither repository can
perform alone. The macOS job keeps everything that genuinely needs a Mac: the
Apple Vision helper, the Tauri build, the packaged bundle. On Linux the two
Vision integration tests skip, which is the honest result there.

Pushing the new workflow proved both halves of the diagnosis. In
`philon-python` the run was created, the job was reached, and it failed in three
seconds on the same billing message — so the workflow is wired correctly and
nothing but the account is stopping it. In `philon` **no run was created at
all**, because both of that repository's workflows are `disabled_manually`:

    gh api repos/tsevis/philon/actions/workflows --jq '.workflows[] | "\(.name) \(.state)"'
    Package Philon  disabled_manually
    Verify Philon   disabled_manually

They were switched off, presumably after the billing failures started. That is a
repository setting and was deliberately left alone.

**Three things were owed by the owner. The third is done; the other two are
scheduled for 2026-09-01**, so between now and then CI is expected to be dead
and a red or absent run means nothing.

1. **Outstanding.** Clear the GitHub billing failure or raise the spending
   limit. Until then no job of either kind starts.
2. **Outstanding.** Re-enable `philon`'s two workflows, which are currently
   disabled. Only after (1), or they will simply resume failing at job start.
3. **Superseded, 2026-09-06.** The two fine-grained PATs were replaced by two
   read-only **deploy keys**, and `PHILON_PEER_TOKEN` was deleted from both
   repositories.

   **Why the tokens went.** They expired on 2026-09-05 and took both peer
   checkouts down with them. A replacement pair written on 2026-09-06 was
   rejected the same way — GitHub answered `401 Bad credentials` to the new
   values as well — so the second red day cost as much as the first and told
   nobody anything about the code. The prediction in the paragraph this replaces
   ("Fine-grained tokens expire, and the same two steps are what will report it
   when these do") was correct, and the reporting worked exactly as designed.
   What it could not do was stop the expiry happening again a year later.

   **A deploy key does not expire.** That is the whole reason for the switch.
   `actions/checkout` takes one as `ssh-key:` in place of `token:`.

   **Two keys, one per repository, each installed on its PEER.** A single key
   with read on both would mean a compromise of either repository's Actions
   granting read to both; neither key is installed on the repository that holds
   it. Both are `ed25519`, **read-only** (write access unticked):

   | Public half is a deploy key on | Private half stored as `PHILON_PEER_SSH_KEY` in |
   |---|---|
   | `tsevis/philon-python` | `tsevis/philon` |
   | `tsevis/philon` | `tsevis/philon-python` |

   **What is set is still not the same as what is correct.** Actions secrets
   remain write-only: neither the owner nor a tool can read a value back, and
   `gh secret list` reports that a secret exists, not that it holds what it
   should — an earlier attempt left one holding the literal string
   `PASTE_TOKEN_HERE` and every count still read `1`. A CI run is the only thing
   that can tell you.

   **The crossed-keys mistake stays detectable, and this is why.** GitHub accepts
   a given deploy key on exactly one repository, so its SSH endpoint separates
   the two failures that need opposite fixes:

   | SSH says | Means | Fix |
   |---|---|---|
   | `Permission denied (publickey)` | the key is not installed anywhere | install the public half, or regenerate the pair |
   | `Repository not found` | the key is valid, but on the wrong repository | move it to the PEER |
   | *(succeeds)* | the key is fine | the checkout failed for another reason |

   Both workflows carry a step named *Explain a failed peer checkout* that runs
   `git ls-remote` over the key and prints whichever of those applies. A secret
   absent altogether is caught earlier by *Check the peer-repository key is
   present*. The key is written `0600` into a temp dir, removed on exit, and
   never echoed.

   To generate and install a pair — the public half goes on the repository being
   *read*, the private half on the repository doing the reading:

       ssh-keygen -t ed25519 -N "" -f /tmp/philon-peer   -C "philon-python reads philon"
       ssh-keygen -t ed25519 -N "" -f /tmp/philonpy-peer -C "philon reads philon-python"

       # /tmp/philon-peer.pub    -> deploy key on tsevis/philon,        read-only
       # /tmp/philonpy-peer.pub  -> deploy key on tsevis/philon-python, read-only

       gh secret set PHILON_PEER_SSH_KEY --repo tsevis/philon-python < /tmp/philon-peer
       gh secret set PHILON_PEER_SSH_KEY --repo tsevis/philon        < /tmp/philonpy-peer

       shred -u /tmp/philon-peer /tmp/philonpy-peer 2>/dev/null || rm -f /tmp/philon-peer /tmp/philonpy-peer

   The self-hosted runners need outbound SSH to `github.com` on port 22. If that
   is blocked, `ssh.github.com:443` is the documented alternative and needs a
   `Host github.com` block in the runner's SSH config.

Nothing is unprotected in the meantime, and no work needs to wait for it. Every
gate the Linux job would run — including the parity gate, the one that matters
most here — already runs inside both `verify-release` paths, which is where they
have always actually run. What CI adds on 1 September is that they run whether or
not someone remembered.

For the parity gate specifically, that gap is now covered by a pre-commit hook,
installed in both clones on 2026-08-23:

    zsh scripts/install-git-hooks.sh              # opt in
    zsh scripts/install-git-hooks.sh --uninstall  # opt back out

It sets `core.hooksPath` to the tracked `scripts/git-hooks`, refuses a commit
that would leave the two copies out of parity, and is silent otherwise. It reads
the working tree rather than the index, because the gate compares against the
other repository's checkout and a checkout has no index — so a partial `git add`
can still commit something the hook did not read, and `verify-release` remains
the check that covers that. `git commit --no-verify` skips it. It was tested by
introducing real drift and confirming that `git commit` refused and `HEAD` did
not move.

The hook and its installer are themselves on the byte-identical list, so a hook
enforcing one thing here and another thing there fails the gate it runs.

## 5. Marker 2.0 is Apache-2.0 — the constraint is kept, on new grounds

`~/AI/marker` is at **2.0.0** on `master`. The premise the standing constraint
rested on has expired, and this is now recorded in three places rather than one.

The history: Marker relicensed GPL → OpenRAIL → **Apache-2.0** (`65f73c9`,
2026-07-17, "Release prep: benchmarks harness, competitive results, Apache
2.0"), and released 2.0.0 on 2026-07-20. The 2.0 `LICENSE` is the Apache
License 2.0 with zero GPL references and `pyproject.toml` says
`license = { text = "Apache-2.0" }`. Apache-2.0 is compatible with an MIT
project subject to attribution and NOTICE, so the *legal* barrier is gone.

**Decision: keep the constraint, restate the reason.** Philon reuses no Marker
code, on clean-room and dependency-budget grounds — the whole conversion path
runs on four runtime dependencies against Marker's ML stack, and an engine that
must justify every rectangle it emits is easier to hold to that standard when
nothing in it was inherited. `README.md` now says exactly that, and says it of
the `page_tree` rename too. `documents/RULED_TABLE_RECOVERY_BRIEF.md` carries
the same restatement. `documents/Research/Claude_PhilonResearch.md` is a record
of a decision and was **not** rewritten; it carries a dated erratum at the top
saying which of its conclusions no longer follow from the licence. The Surya
weight-licence claims in that document are separate and were not re-checked.

The local checkout also moved: its local commits — fixes written *for* Marker,
unrelated to Philon — were rebased onto v2.0.0 on 2026-08-22. It is now 8 ahead
and 5 behind `datalab-to/marker`, on which there is still no push access.

## 6. Benchmarks: run against Marker 2.0, and still blocked — with one gap found

The heading-detection figures in the README were unlabelled as to what they were
measured against. They can be dated exactly, and are:

* Philon measured them on **2026-08-21** (`4989ebb`, `c8afdf1`).
* `~/AI/marker` was cloned on 2026-05-31 at upstream `6ae3889`, dated
  2026-05-05, and sat there until the rebase on 2026-08-22 — the day *after*.
* `6ae3889` is `v1.10.2-13-g6ae3889`: **marker-pdf 1.10.2**, `license =
  "GPL-3.0-or-later"`.

### The run

`bench/run.py` was run against **marker-pdf 2.0.0** on 2026-08-23, macOS 15.6
arm64, Balanced profile, two born-digital documents. Marker 2.0 runs here: its
models were already cached, so it needed no download.

| document | pages | Philon cold | Philon warm | Marker wall | Marker's own |
|---|---|---|---|---|---|
| qwen-philon-blueprint | 26 | 5.7s | 0.23s | 43.7s | 24.7s |
| attention-is-all-you-need | 15 | 4.3s | 0.15s | 28.3s | 10.3s |

Roughly 17 seconds of each Marker wall time is one-off model loading — the gap
between its wall time and the conversion time it reports itself. Both are worth
stating: the wall figure is what one document costs a person, the conversion
figure is what survives batching.

**This does not lift the gate.** Two documents on one machine is a measurement,
not a claim, and one of the two is an internal blueprint.

### The gap it found

**The harness never recorded which files it read.** A result named the machine,
the profile and the corpus *name*, and nothing about the documents — which is
why the 2026-08-15 run could not be reconciled with anything, and why the three
papers behind the heading figures are not recoverable from this repository. The
methodology gates a claim on "the same version-pinned corpus" and the tool
recorded no corpus.

Fixed: every result now carries each document's filename, byte count and
SHA-256, and never its path. The corpus stays private and two runs carrying the
same digests provably read the same bytes. Two tests cover it, including that a
failed document is still identified and that no absolute path reaches the file.

### What is still owed

The heading recall/precision figures **cannot** be restated against Marker 2.0
until someone says which three papers they were. Nothing in either repository
records it. Two further points about those figures, whenever they are re-run:

* `bench/run.py` does not measure heading recall or precision at all — it
  measures timing, counts, coverage, gates, and optionally word/formula/table
  accuracy against a gold file. The heading numbers were produced some other
  way, and that method is not recorded either.
* They treat **Marker's output as the reference, which is not gold.** Measured
  on the two documents above, Philon scores 100% recall at 72% precision on one
  and 85% at 100% on the other — but every one of the eight "false positives"
  on the first is a real heading Marker missed (`Recommended technologies`,
  `Phase 1 — Foundation`, and so on), so the precision figure understates. On
  the second, Philon genuinely misses `Abstract` and `References`. Those are not
  claims; they are what two documents showed.

## 7. Smaller decisions left open

Unchanged from the first draft. None of these was taken.

- **Markdown and merged cells — DECIDED 2026-08-23, collapse.** A column that
  is covered in *every* row is blank from top to bottom, says nothing, and is
  now dropped from the Markdown table: the reference paper's 14x6 grids render
  as the three logical columns they are. A column covered in only *some* rows
  is kept with its blanks, because dropping it would misalign the rows that do
  use it and filling it in would repeat a value the page wrote once — which is
  inventing, not recovering. HTML is unchanged and still says `colspan`; the
  IR, the CSV and the page-tree export stay square, since a consumer reading
  them by index is entitled to the grid the recovery found. `markdown_table_grid`
  in the engine, with a fixture whose middle column no row uses.
- **Formula semantics.** Philon recovers *typesetting* from measured baselines
  and sizes — it writes `x^{2}` — and makes no claim about what an equation
  means. Parsing structure (fractions, radicals, matrices) is unstarted and
  would need either a much larger geometric model or a VLM.
- **`layout-ocr` and `local-repair`** remain `approved: false` with
  `"UNRESOLVED - benchmark and legal review required before distribution"`.
- **The CCITT fax defect** in `documents/CACHE_BOUNDARY_AND_ASSET_COST.md`: one
  image fails to decode and produces different bytes on every extraction, so its
  `bytes_sha256` provenance changes run to run. Recorded, deliberately unfixed.
  Note that an image Philon cannot decode is also one it cannot re-encode, so
  such an image is exported as its own bytes and shows in the interface as a
  placeholder rather than a picture. That is the honest outcome and not the
  JPEG 2000 bug fixed on 2026-08-23.

---

## Standing constraints that still hold

- **Parity.** One documented hunk in `philon_engine.py`; `model_fetch.py`,
  `model-manifest.json` and `tests/parity_policy.py` byte-identical. This is now
  gated rather than remembered — but run `diff` anyway when something looks off,
  because the gate reports a count and `diff` shows you the lines.
- **Coordinate frame.** PDFium reports page size with `/Rotate` applied and text
  and path coordinates without it. Everything downstream is in the **displayed**
  frame. Put every recovered rectangle through `bbox_to_displayed_frame`, and
  classify rules *after* that conversion, not before.
- **IR version.** Adding a field means bumping it and saying what changed. The
  cache key carries the version, so old entries become unreachable rather than
  rejected — by design.
- **Prove or mark, never invent.** Ruled tables and measured script geometry are
  provable. Whitespace alignment is inference and is not emitted. Uncertainty
  becomes a warning. A benchmark number carries the version it was measured
  against, or it is not a number.
- **Local only.** Conversion opens no connection. `engine/model_fetch.py` is the
  single named exemption; the gate checks that the exemption is load-bearing,
  that the engine never imports the fetcher at module scope, and — since
  2026-08-23 — that the fetcher's own redirect check is actually reachable.
- **Tests.** Build PDF fixtures inline, byte by byte.
- **GUI tests must not open windows**, and must never be run without asking.
