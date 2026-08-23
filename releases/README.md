# Releases

What was built, how to check it, and what it costs the person who opens it.

A build is produced by `zsh scripts/verify-release.sh --package`, which runs the
whole gate before it packages anything: the desktop suite, the engine, fuzz and
benchmark suites with the Apple Vision integration tests enabled, the five
policy gates — local-only, model-fetch, engine-parity, licence and SBOM — and
the native shell constructed off-screen. A disk image that exists passed all of
it. `zsh scripts/package-macos.sh` alone builds the same image without the gate,
which is the faster thing to do while iterating and the wrong thing to ship.

## Why the images are not committed here

The source project keeps its built DMG in `releases/` beside the source it was
built from. This repository deliberately does not, and the reason is size rather
than principle.

Measured on 2026-08-23, version 0.2.6, arm64:

| | source project | this port |
|---|---|---|
| bundle | Tauri app around a system webview | PyInstaller directory carrying CPython and Qt |
| `.app` on disk | — | 127 MB |
| compressed DMG | 24 MB | **48 MB** |

Twice the size, and the whole difference is the runtime: the port ships its own
interpreter and the PySide6 Qt frameworks where the source project borrows the
webview macOS already has. Two versions of a 24 MB image is a repository anyone
can clone; a history of 48 MB images is not, and git keeps every one of them
forever. So what lives here is the record, and `dist/` — which `.gitignore`
already covers — is where the image itself is written.

The GitHub release workflow uploads the image as a build artifact on a `v*` tag,
so a build is retrievable without being resident in the history.

## Verifying a download

    shasum -a 256 -c Philon_0.2.6_aarch64.dmg.sha256

`scripts/package-macos.sh` records the checksum from the same file `hdiutil
verify` accepted, and records it against the bare filename rather than the build
path, so the check runs wherever the two files are put down together.

## Signing

Images are signed ad-hoc (`codesign --sign -`) and **not** notarized: Apple
Developer signing and notarization need credentials that are deliberately kept
out of the build, so packaging stays reproducible by anyone with the source and
requires nothing secret. `codesign --verify --strict` passes on the bundle — the
ad-hoc seal is what makes that true — but it carries no Developer ID:

    Identifier=com.tsevis.philon-python
    Format=app bundle with Mach-O thin (arm64)
    Signature=adhoc
    TeamIdentifier=not set

macOS will therefore refuse the first launch of a downloaded copy. Open it from
the shortcut menu — Control-click the app, choose Open, then confirm — which
asks Gatekeeper about this one application rather than turning the check off. A
copy built on your own machine carries no quarantine attribute and opens
normally.

Install with `ditto`, never `cp -R`. `cp -R` does not preserve the bundle seal,
and `codesign --verify --deep --strict` then fails on a bundle that was fine
inside the image.

## Which architecture

`aarch64` is Apple silicon. There is no Intel build, for the same reason the
source project has none and one more of its own: the bundle carries a Swift
Vision helper compiled for the host, and PyInstaller freezes the interpreter and
every binary wheel — pypdfium2, Pillow — for the architecture it is run on. A
universal build would have to be produced on, or cross-built for, both.

## What has been built

| version | date | `.app` | DMG | verified |
|---|---|---|---|---|
| 0.2.6 | 2026-08-23 | 127 MB | 48 MB | `hdiutil verify` CRC32 `$C00DD2AE`; `codesign --verify --strict` valid on disk |

Earlier versions were built before packaging produced a disk image, so no image
and no checksum exists for them. That is a gap in the record and not a claim
that none were built.
