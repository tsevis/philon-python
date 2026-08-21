#!/bin/zsh
set -euo pipefail
ROOT_DIR="${0:A:h:h}"
cd "${ROOT_DIR}"

# The same project environment packaging builds from, so what is verified here
# is the set of versions the application bundle actually carries.
VENV="${ROOT_DIR}/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
  "${VENV}/bin/python" -m pip install --requirement requirements.txt
fi
PYTHON="${VENV}/bin/python"

"${PYTHON}" -m unittest discover -s tests -v
# Build the Apple Vision helper before the engine suite runs, so the two
# integration tests execute rather than skipping. The source project does the
# same, and without it a local run is quietly weaker than the one it mirrors.
if [[ ! -x "${ROOT_DIR}/engine/dist/philon-vision-ocr" ]]; then
  mkdir -p "${ROOT_DIR}/engine/dist"
  swiftc -O -framework Vision -framework AppKit "${ROOT_DIR}/engine/vision_ocr.swift" \
    -o "${ROOT_DIR}/engine/dist/philon-vision-ocr"
fi
PHILON_VISION_INTEGRATION=1 "${PYTHON}" -m unittest engine/test_engine.py engine/test_fuzz.py bench/test_run.py -v
"${PYTHON}" tests/local_only_policy.py
"${PYTHON}" tests/license_policy.py
"${PYTHON}" tests/sbom_policy.py
# Confirm that the native shell can be constructed without entering its event
# loop. An isolated data root keeps this verification from touching user state.
PHILON_DATA_DIR="$(mktemp -d)" QT_QPA_PLATFORM=offscreen "${PYTHON}" -c 'from philon_desktop.app import MainWindow; from PySide6.QtWidgets import QApplication; app = QApplication([]); window = MainWindow(); assert window.stack.count() == 6; window.close()'
if [[ "${1:-}" == "--package" ]]; then
  zsh scripts/package-macos.sh
fi
