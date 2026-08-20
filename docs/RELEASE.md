# Python release checklist

1. Create a clean Python 3.10+ environment and install `requirements-build.txt`.
2. Run `zsh scripts/verify-release.sh`.
3. Run the private corpus benchmark; archive its manifest, result, hardware and local model fingerprints.
4. Build `zsh scripts/package-macos.sh` on an Apple Silicon Mac.
5. On a clean offline Apple Silicon test machine, exercise native PDF, scanned/image OCR, repair-candidate, pause/recovery, and export flows.
6. Sign and notarize only in the owner-controlled release environment.
7. Publish no Marker/Docling comparison until the archived corpus supports the specific claim.

