#!/bin/zsh
# Build an unsigned, local-only Apple Silicon application bundle. Signing and
# notarization deliberately remain owner-controlled release actions.
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
APP_VERSION="0.2.1"
cd "${ROOT_DIR}"

python3 -m pip install --requirement requirements-build.txt
mkdir -p engine/dist
swiftc -O -framework Vision -framework AppKit engine/vision_ocr.swift -o engine/dist/philon-vision-ocr
# The bundle identifier is set here rather than in a checked-in spec file:
# this invocation regenerates Philon.spec on every run, so a hand-edited spec
# would be silently overwritten and the app would fall back to PyInstaller's
# bare "Philon" default.
pyinstaller --noconfirm --clean --windowed --name Philon \
  --osx-bundle-identifier com.tsevis.philon-python \
  --add-data "engine/model-manifest.json:engine" \
  --add-data "engine/dist/philon-vision-ocr:engine/dist" \
  --exclude-module PyQt6 \
  --collect-all pypdfium2 --collect-all pypdf --collect-all PIL \
  philon_launcher.py
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${APP_VERSION}" \
  -c "Add :CFBundleVersion string ${APP_VERSION}" \
  "dist/Philon.app/Contents/Info.plist"
codesign --force --sign - "dist/Philon.app"
echo "Unsigned app bundle: ${ROOT_DIR}/dist/Philon.app"
