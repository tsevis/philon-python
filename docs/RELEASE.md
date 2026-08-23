# Python release checklist

1. Create a clean Python 3.10+ environment and install `requirements-build.txt`.
   `scripts/verify-release.sh` builds `.venv` from `requirements.txt` if it is
   absent, so what is verified is the set of versions the application bundle
   actually carries rather than whatever is on `PATH`.
2. Run `PHILON_DATA_DIR=$(mktemp -d) zsh scripts/verify-release.sh`. It is one
   command and it runs the whole gate, in this order: the desktop suite; the
   Apple Vision helper is compiled so its two integration tests execute rather
   than skip; the engine, fuzz and benchmark suites; then the **local-only**,
   **model-fetch**, **engine-parity**, licence and SBOM policies — **five of
   them, not three** — and finally the native shell constructed off-screen.
   Running the individual scripts by hand is how a step gets skipped; this
   checklist used to name a subset of them, and so described a weaker gate than
   the one that actually runs. The isolated `PHILON_DATA_DIR` keeps the run from
   touching real user state.
3. Confirm the parity gate compared something rather than skipping. `diff` over
   `engine/philon_engine.py` is no longer a manual step — `tests/parity_policy.py`
   checks it inside `verify-release`, and `zsh scripts/install-git-hooks.sh`
   installs it as a pre-commit hook so it also runs on every commit. But the gate
   passes — loudly, on stderr — when the source project's checkout is absent,
   because one repository alone is a legitimate way to work. A release build must
   therefore be made with `philon` beside this repository, or with
   `PHILON_PARITY_PEER` pointing at it. Read the line it prints: it names how
   many files were byte-identical. `PHILON_PARITY_REQUIRE=1` turns the skip into
   a failure if you would rather not have to read it.

   What the gate holds: `engine/model_fetch.py`, `engine/model-manifest.json`,
   `tests/parity_policy.py`, `scripts/git-hooks/pre-commit` and
   `scripts/install-git-hooks.sh` byte-identical, and `engine/philon_engine.py`
   differing by exactly the one hunk recorded in `docs/PARITY.md`. The hook reads
   the working tree rather than the index, so a partial `git add` can still commit
   something it did not read; `verify-release` is what covers that.
4. Confirm the SBOM still describes what the bundle carries, including anything
   vendored into source rather than installed as a package. The SBOM policy
   validates the components that are declared; it cannot see one that was never
   declared.
5. Run the private corpus benchmark; archive its manifest, result, hardware and
   local model fingerprints. A recorded result names the documents it measured by
   filename, size and SHA-256, so "the same version-pinned corpus" is something a
   reader can check rather than something the runner asserts.
6. Build the ad-hoc signed bundle with `zsh scripts/package-macos.sh` on an Apple
   Silicon Mac. `zsh scripts/verify-release.sh --package` does steps 2 and 6
   together. Record what was built in `releases/README.md`.
7. On a clean offline Apple Silicon test machine, exercise native PDF,
   scanned/image OCR, repair-candidate, pause/recovery, and export flows. Install
   with `ditto`, never `cp -R`: `cp -R` does not preserve the bundle seal and
   `codesign --verify --deep --strict` then fails on a bundle that was fine
   before it was copied.
8. Sign and notarize only in the owner-controlled release environment. Packaging
   deliberately produces an ad-hoc signed, un-notarized bundle so that it needs
   nothing secret; see `releases/README.md` for what that costs the person who
   downloads one.
9. Publish no Marker/Docling comparison until the archived corpus supports the
   specific claim.
