#!/bin/zsh
set -euo pipefail
ROOT_DIR="${0:A:h:h}"
cd "${ROOT_DIR}"
python3 -m unittest discover -s tests -v
python3 -m unittest engine/test_engine.py engine/test_fuzz.py bench/test_run.py -v
python3 tests/local_only_policy.py
python3 tests/license_policy.py
python3 tests/sbom_policy.py
# Confirm that the native shell can be constructed without entering its event
# loop. An isolated data root keeps this verification from touching user state.
PHILON_DATA_DIR="$(mktemp -d)" QT_QPA_PLATFORM=offscreen python3 -c 'from philon_desktop.app import MainWindow; from PySide6.QtWidgets import QApplication; app = QApplication([]); window = MainWindow(); assert window.stack.count() == 6; window.close()'
if [[ "${1:-}" == "--package" ]]; then
  zsh scripts/package-macos.sh
fi
