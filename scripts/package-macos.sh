#!/bin/zsh
# Build an ad-hoc signed, local-only Apple Silicon application bundle. The
# ad-hoc seal is what lets `codesign --verify` pass; Developer ID signing and
# notarization deliberately remain owner-controlled release actions.
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
APP_VERSION="0.2.6"
cd "${ROOT_DIR}"

# Build from a project virtual environment rather than whatever interpreter is
# on PATH. PyInstaller collects what it can import, so packaging from a general
# purpose environment quietly ships it: an earlier build carried numpy, IPython,
# matplotlib and a compiler toolchain, none of which Philon uses, and produced a
# 216MB bundle whose SBOM could not honestly describe it.
VENV="${ROOT_DIR}/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
fi
"${VENV}/bin/python" -m pip install --upgrade pip
"${VENV}/bin/python" -m pip install --requirement requirements-build.txt
mkdir -p engine/dist
swiftc -O -framework Vision -framework AppKit engine/vision_ocr.swift -o engine/dist/philon-vision-ocr
# The bundle identifier is set here rather than in a checked-in spec file:
# this invocation regenerates Philon.spec on every run, so a hand-edited spec
# would be silently overwritten and the app would fall back to PyInstaller's
# bare "Philon" default.
"${VENV}/bin/python" -m PyInstaller --noconfirm --clean --windowed --name Philon \
  --osx-bundle-identifier com.tsevis.philon-python \
  --add-data "engine/model-manifest.json:engine" \
  --add-data "engine/dist/philon-vision-ocr:engine/dist" \
  --add-data "philon_desktop/assets:philon_desktop/assets" \
  --exclude-module PyQt6 \
  --collect-all pypdfium2 --collect-all pypdf --collect-all PIL \
  philon_launcher.py
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${APP_VERSION}" \
  -c "Add :CFBundleVersion string ${APP_VERSION}" \
  "dist/Philon.app/Contents/Info.plist"
codesign --force --sign - "dist/Philon.app"
echo "Ad-hoc signed, un-notarized app bundle: ${ROOT_DIR}/dist/Philon.app"

# A .app is a directory, and a directory is not something a person downloads:
# copying one over the network loses the bundle seal that the ad-hoc signature
# just established, which is the same reason `ditto` exists and `cp -R` is
# refused in the release checklist. The disk image is the unit that survives
# being moved, so packaging produces one rather than stopping at the bundle and
# leaving the last step to whoever is shipping.
DMG="dist/Philon_${APP_VERSION}_aarch64.dmg"
rm -f "${DMG}" "${DMG}.sha256"
hdiutil create -volname "Philon ${APP_VERSION}" -srcfolder "dist/Philon.app" \
  -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "${DMG}"
hdiutil verify "${DMG}"
# Recorded from the same file `hdiutil verify` just accepted, and with the bare
# filename rather than the build path, so `shasum -c` works wherever it lands.
( cd dist && shasum -a 256 "$(basename "${DMG}")" > "$(basename "${DMG}").sha256" )
echo "Disk image: ${ROOT_DIR}/${DMG}"
cat "${DMG}.sha256"
