# Python release checklist

1. Create a clean Python 3.10+ environment and install `requirements-build.txt`.
2. Run `zsh scripts/verify-release.sh`. It runs the desktop suite, builds the Apple Vision helper so the two integration tests execute rather than skip, runs the engine, fuzz and benchmark suites, the local-only, licence and SBOM policies, and constructs the native shell off-screen.
3. Confirm `diff` over `engine/philon_engine.py` in this repository and the source project still reports the one documented divergence. Nothing else in that file may differ, and no suite checks it: each repository tests its own copy.
4. Confirm the SBOM still describes what the bundle carries, including anything vendored into source rather than installed as a package. The SBOM policy validates the components that are declared; it cannot see one that was never declared.
5. Run the private corpus benchmark; archive its manifest, result, hardware and local model fingerprints.
6. Build `zsh scripts/package-macos.sh` on an Apple Silicon Mac.
7. On a clean offline Apple Silicon test machine, exercise native PDF, scanned/image OCR, repair-candidate, pause/recovery, and export flows.
8. Sign and notarize only in the owner-controlled release environment.
9. Publish no Marker/Docling comparison until the archived corpus supports the specific claim.
