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
"${PYTHON}" tests/model_fetch_policy.py
"${PYTHON}" tests/license_policy.py
"${PYTHON}" tests/sbom_policy.py
# Confirm that the native shell can be constructed without entering its event
# loop. An isolated data root keeps this verification from touching user state.
#
# This asserts a RELATIONSHIP rather than a page count. It used to require
# `window.stack.count() == 6`, and when the shell was refactored the attribute
# stopped existing: the release failed with a bare AttributeError that named
# neither the cause nor the fix, and the number would have needed changing on
# every legitimate page addition anyway. What is actually worth checking is
# that the stack, the ordered names and the header tabs still describe the same
# set of views -- three structures that are built separately and must agree, so
# a page added to one and forgotten in the others is caught. The floor catches
# a shell that collapsed to nothing.
PHILON_DATA_DIR="$(mktemp -d)" QT_QPA_PLATFORM=offscreen "${PYTHON}" - <<'PYTHON'
from PySide6.QtWidgets import QApplication

from philon_desktop.app import MainWindow
from philon_desktop.gui import theme

app = QApplication([])
# The same call `main()` makes. Without it the offscreen plugin's default
# family is resolved by walking the whole font database, which this step was
# paying for in silence.
theme.apply_application_font(app)
window = MainWindow()
views, names, tabs = window.views.count(), len(window.view_names), len(window.main_tabs)
if views < 3:
    raise SystemExit(f"The shell built only {views} view(s); it did not construct.")
if not views == names == tabs:
    raise SystemExit(
        f"The shell's views disagree: {views} in the stack, {names} ordered name(s), "
        f"{tabs} header tab(s). A view was added to one of the three and not the others."
    )
window.close()
print(f"Native shell constructed off-screen with {views} agreeing views.")
PYTHON
if [[ "${1:-}" == "--package" ]]; then
  zsh scripts/package-macos.sh
fi
