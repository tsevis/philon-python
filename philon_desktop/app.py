"""Qt application entry for the local Philon conversion workspace.

The interface is the GUI of the source macOS application, ported panel for
panel: header navigation with a Library count, the workspace command bar,
the three-column conversion grid, batch queue/report, secondary workspaces,
the first-launch splash, and the maker's mark at the foot of the window.
"""

from __future__ import annotations

import sys

from .gui import theme
from .gui.main_window import MainWindow
from .gui.qt import QApplication
from .gui.workers import WorkThread, stop_leftover_children, wait_for_workers

__all__ = ["MainWindow", "WorkThread", "stop_leftover_children", "wait_for_workers", "main"]


def follow_system_appearance(app: QApplication, holder: dict) -> None:
    """Rebuild the shell when macOS switches between light and dark.

    Token colors are applied at construction, so the faithful response to an
    appearance change is the one the source app gets for free from CSS: the
    whole surface re-renders. Working state carries across the rebuild.
    """
    hints = app.styleHints()
    if not hasattr(hints, "colorSchemeChanged"):  # Qt < 6.5
        return

    def rebuild() -> None:
        old = holder["window"]
        state = old.export_state()
        theme.reset_tokens()
        app.setStyleSheet(theme.build_qss())
        window = MainWindow(old.service)
        window.resize(old.size())
        window.move(old.pos())
        window.adopt_state(state)
        holder["window"] = window
        window.show()
        old.close()
        old.deleteLater()

    hints.colorSchemeChanged.connect(lambda _scheme: rebuild())


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Philon")
    app.setOrganizationName("Philon")
    theme.apply_application_font(app)
    theme.reset_tokens()
    app.setStyleSheet(theme.build_qss())
    holder = {"window": MainWindow()}
    follow_system_appearance(app, holder)
    holder["window"].show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
