"""The application shell: header navigation, command bar, views, and state.

State flow mirrors the source application's `App.tsx`: one owner for the
conversion result, history, banners and task progress, with the panels as
renderers of that state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core import DEFAULT_OUTPUTS, OUTPUTS, PROFILES, PhilonService
from . import theme
from .batch_view import BatchView
from .evidence_panel import EvidencePanel
from .output_panel import OutputPanel
from .phosphor import Icon, pixmap
from .review_editor import ReviewEditor
from .secondary_pages import DiagnosticsView, LibraryView, ModelsView
from .settings_page import SettingsView
from .source_panel import SourcePanel
from .splash import Overlay, Splash
from .widgets import Banner, ElidedLabel, MainTab, Segmented, TaskProgressCard, hbox, make_button, vbox
from .workers import WorkThread, stop_leftover_children, wait_for_workers
from .qt import (
    QAction,
    QFileDialog,
    QFrame,
    QKeySequence,
    QLineEdit,
    QMainWindow,
    QPixmap,
    QPushButton,
    QShortcut,
    QSize,
    QStackedWidget,
    QToolButton,
    QUrl,
    QDesktopServices,
    QWidget,
    Qt,
)

FILE_FILTER = "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.webp)"
PROFILE_DESCRIPTIONS = {
    "Fast": "Native text only when trustworthy.",
    "Balanced": "Local evidence checks. No network.",
    "Verified": "Stricter checks and more review signals.",
}
ACTIVE_STATES = ("completed", "completed_with_warnings")


class MainWindow(QMainWindow):
    def __init__(self, service: PhilonService | None = None) -> None:
        super().__init__()
        self.service = service or PhilonService()
        self.preferences: dict[str, Any] = {
            "profile": "Balanced", "cache_policy": "use",
            "outputs": list(DEFAULT_OUTPUTS), "enabled_model_ids": [],
            **self.service.preferences(),
        }
        self.profile: str = str(self.preferences.get("profile", "Balanced"))
        self.single_path: str | None = None
        self.result: dict[str, Any] | None = None
        self.selected_block_id: str | None = None
        self.batch_id: str | None = self.service.store.latest_batch_id()
        self.batch_items: list[dict[str, Any]] = []
        self.batch_preflight: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.health: str | None = None
        self.running = False
        self.workers: dict[str, WorkThread] = {}
        self.overlay: Overlay | None = None

        self.setWindowTitle("Philon")
        self.resize(1540, 960)
        self.setMinimumSize(1024, 700)
        self._build()
        self._build_menu()
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._clear_banners)
        self._load_history()
        if self.batch_id:
            self._reload_batch()
        # Every launch, not once per version. The screen carries what Philon is,
        # what it refuses to do, and the sources behind it, and the owner asked
        # for it in front of them each time rather than once and then never
        # again. Nothing is recorded, because nothing is being decided.
        self._show_splash()

    # -- construction ------------------------------------------------------
    def _build(self) -> None:
        t = theme.tokens()
        shell = QWidget()
        shell.setObjectName("AppShell")
        shell_layout = vbox(shell, (0, 0, 0, 0), 0)

        header = QFrame()
        header.setObjectName("AppHeader")
        header.setFixedHeight(58)
        header_layout = hbox(header, (28, 0, 28, 0), 24)
        tabs_row = hbox(spacing=2)
        self.main_tabs: dict[str, MainTab] = {}
        for key, title in (("workspace", "Workspace"), ("library", "Library"), ("models", "Models"), ("diagnostics", "Diagnostics"), ("settings", "Settings")):
            tab = MainTab(title)
            tab.clicked.connect(lambda _=False, value=key: self.show_view(value))
            tabs_row.addWidget(tab)
            self.main_tabs[key] = tab
        header_layout.addLayout(tabs_row)
        header_layout.addStretch(1)
        chip = QFrame()
        chip.setObjectName("SystemStatusChip")
        chip_layout = hbox(chip, (9, 6, 9, 6), 7)
        chip_layout.addWidget(Icon("ShieldCheck", 15, t["green"], "fill"))
        chip_layout.addWidget(theme.label("Local only", size=11, color=t["text_strong"]))
        chip.setFixedHeight(27)
        header_layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        info = QToolButton()
        info.setObjectName("IconButton")
        info.setFixedSize(30, 30)
        info.setCursor(Qt.CursorShape.PointingHandCursor)
        info.setToolTip("About Philon")
        info.setIcon(pixmap("Info", 18, t["text_strong"]))
        info.setIconSize(QSize(18, 18))
        info.clicked.connect(lambda: self._show_splash())
        header_layout.addWidget(info, 0, Qt.AlignmentFlag.AlignVCenter)
        shell_layout.addWidget(header)

        content = QWidget()
        content_layout = vbox(content, (28, 20, 28, 28), 0)
        self.command_bar = self._build_command_bar()
        content_layout.addWidget(self.command_bar)
        self.error_banner = Banner("error")
        self.error_banner.dismissed.connect(lambda: self.error_banner.hide())
        content_layout.addWidget(self.error_banner)
        content_layout.addSpacing(0)
        self.notice_banner = Banner("notice")
        self.notice_banner.dismissed.connect(lambda: self.notice_banner.hide())
        content_layout.addWidget(self.notice_banner)
        self.task_card = TaskProgressCard()
        content_layout.addWidget(self.task_card)
        content_layout.addSpacing(12)

        self.views = QStackedWidget()
        self.workspace_view = self._build_workspace()
        self.library_view = LibraryView()
        self.library_view.refresh_requested.connect(lambda: self._load_history(show_progress=True))
        self.library_view.clean_requested.connect(self._clean_library)
        self.library_view.job_opened.connect(self._open_history_job)
        self.models_view = ModelsView()
        self.models_view.refresh_requested.connect(self._load_models)
        self.models_view.toggle_requested.connect(self._toggle_model)
        self.models_view.download_requested.connect(self._download_model)
        self.models_view.remove_requested.connect(self._remove_model)
        self.diagnostics_view = DiagnosticsView()
        self.diagnostics_view.check_requested.connect(self._inspect_engine)
        self.settings_view = SettingsView()
        self.settings_view.set_preferences(self.preferences)
        self.settings_view.preferences_changed.connect(self._save_preferences)
        self.settings_view.defaults_restored.connect(self._restore_defaults)
        for view in (self.workspace_view, self.library_view, self.models_view, self.diagnostics_view, self.settings_view):
            self.views.addWidget(view)
        self.view_names = ["workspace", "library", "models", "diagnostics", "settings"]
        content_layout.addWidget(self.views, 1)
        shell_layout.addWidget(content, 1)
        self.setCentralWidget(shell)

        # The maker's mark, at the foot of the window and on every view.
        self.makers_mark = QPushButton(shell)
        self.makers_mark.setCursor(Qt.CursorShape.PointingHandCursor)
        self.makers_mark.setToolTip("Made by Charis Tsevis — tsevis.com")
        self.makers_mark.setFixedSize(26, 26)
        mark_path = Path(__file__).resolve().parents[1] / "assets" / "makers-mark.png"
        mark_pixmap = QPixmap(str(mark_path))
        self.makers_mark.setIcon(mark_pixmap if not mark_pixmap.isNull() else pixmap("Info", 20, t["text_tertiary"]))
        self.makers_mark.setIconSize(QSize(20, 20))
        hover_bg = "rgba(235,235,245,.12)" if theme.system_is_dark() else "rgba(60,60,67,.1)"
        self.makers_mark.setStyleSheet(f"QPushButton {{ background: transparent; border: 0; border-radius: 6px; }} QPushButton:hover {{ background: {hover_bg}; }}")
        self.makers_mark.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://tsevis.com")))
        self.show_view("workspace")

    def _build_command_bar(self) -> QWidget:
        t = theme.tokens()
        bar = QWidget()
        layout = hbox(bar, spacing=10)
        self.job_tabs = Segmented([("Single Job", "FileArrowUp"), ("Batch", "ListChecks")], font_size=12)
        self.job_tabs.select("Single Job", announce=False)
        self.job_tabs.changed.connect(lambda _: self._refresh_command_bar() or self._refresh_workspace())
        layout.addWidget(self.job_tabs)
        self.open_button = make_button("Open a document", "OpenDocumentButton", "FolderOpen", 17, t["accent_text"], font_size=12)
        self.open_button.clicked.connect(self.choose_single)
        layout.addWidget(self.open_button)
        self.selected_name = ElidedLabel("")
        theme.font(self.selected_name, 12, 600)
        self.selected_name.setStyleSheet(f"color: {t['text_strong']}; background: transparent;")
        self.selected_name.setMaximumWidth(420)
        layout.addWidget(self.selected_name, 1)
        layout.addStretch(1)
        self.pages_field = QLineEdit()
        self.pages_field.setObjectName("PagesField")
        self.pages_field.setPlaceholderText("All pages")
        self.pages_field.setToolTip(
            "Pages to convert, counted from one: 1-5,8. Leave empty for the whole document.")
        self.pages_field.setMaxLength(64)
        self.pages_field.setFixedWidth(112)
        theme.font(self.pages_field, 12, 600)
        self.pages_field.returnPressed.connect(self.convert)
        layout.addWidget(self.pages_field)
        self.profile_picker = Segmented(
            [(name, None) for name in PROFILES], padding=2, font_size=12, font_weight=600, button_padding="7px 10px",
            tooltips=PROFILE_DESCRIPTIONS)
        self.profile_picker.select(self.profile, announce=False)
        self.profile_picker.changed.connect(self._set_profile)
        layout.addWidget(self.profile_picker)
        self.export_button = make_button("Export…", "SecondaryButton", "DownloadSimple", 17)
        self.export_button.clicked.connect(self._export_active)
        layout.addWidget(self.export_button)
        self.convert_button = make_button("Convert", "PrimaryButton", "Play", 17, t["accent_ink"], "fill", font_weight=750)
        self.convert_button.clicked.connect(self.convert)
        layout.addWidget(self.convert_button)
        return bar

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        layout = vbox(workspace, spacing=0)
        self.workspace_stack = QStackedWidget()

        grid = QFrame()
        grid.setObjectName("ConversionGrid")
        theme.drop_shadow(grid, 24, 8, "rgba(0,0,0,.06)")
        grid_layout = hbox(grid, (0, 0, 0, 0), 0)
        self.source_panel = SourcePanel()
        self.source_panel.page_selected.connect(self._select_page)
        self.output_panel = OutputPanel()
        self.output_panel.block_selected.connect(self._select_block)
        self.evidence_panel = EvidencePanel()
        self.evidence_panel.block_chosen.connect(self._select_block)
        self.evidence_panel.review_requested.connect(self._review_block)
        self.evidence_panel.candidate_restored.connect(lambda block_id, index: self._apply_review(block_id, "restore_candidate", candidate_index=index))
        self.evidence_panel.repair_requested.connect(self._request_repair)
        grid_layout.addWidget(self.source_panel, 90)
        grid_layout.addWidget(self.output_panel, 150)
        grid_layout.addWidget(self.evidence_panel, 84)
        self.workspace_stack.addWidget(grid)

        self.batch_view = BatchView()
        self.batch_view.add_requested.connect(self._add_batch_documents)
        self.batch_view.clear_requested.connect(self._clear_pending)
        self.batch_view.item_state_requested.connect(self._set_item_state)
        self.batch_view.export_requested.connect(self._export_batch)
        self.workspace_stack.addWidget(self.batch_view)
        layout.addWidget(self.workspace_stack, 1)
        return workspace

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open Document…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(lambda: (self.show_view("workspace"), self.job_tabs.select("Single Job"), self.choose_single()))
        file_menu.addAction(open_action)
        add_action = QAction("Add to Batch…", self)
        add_action.setShortcut("Ctrl+Shift+O")
        add_action.triggered.connect(lambda: (self.show_view("workspace"), self.job_tabs.select("Batch"), self._add_batch_documents()))
        file_menu.addAction(add_action)
        export_action = QAction("Export Conversion…", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self._export_active)
        file_menu.addAction(export_action)
        view_menu = self.menuBar().addMenu("View")
        for title, shortcut, handler in (
            ("Single Job", "Ctrl+1", lambda: (self.show_view("workspace"), self.job_tabs.select("Single Job"))),
            ("Batch", "Ctrl+2", lambda: (self.show_view("workspace"), self.job_tabs.select("Batch"))),
            ("Library", "Ctrl+3", lambda: self.show_view("library")),
            ("Models", "Ctrl+4", lambda: self.show_view("models")),
            ("Diagnostics", "Ctrl+5", lambda: self.show_view("diagnostics")),
            ("Settings", "Ctrl+,", lambda: self.show_view("settings")),
        ):
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(handler)
            view_menu.addAction(action)
        help_menu = self.menuBar().addMenu("Help")
        about_action = QAction("About Philon", self)
        about_action.triggered.connect(lambda: self._show_splash())
        help_menu.addAction(about_action)

    # -- navigation --------------------------------------------------------
    def show_view(self, name: str) -> None:
        self.views.setCurrentIndex(self.view_names.index(name))
        for key, tab in self.main_tabs.items():
            tab.setChecked(key == name)
        self.command_bar.setVisible(name == "workspace")
        if name == "library":
            self._load_history(show_progress=True)
        if name == "models":
            self._load_models()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "makers_mark"):
            self.makers_mark.move(13, self.centralWidget().height() - self.makers_mark.height() - 9)
            self.makers_mark.raise_()

    def closeEvent(self, event: Any) -> None:
        if not wait_for_workers():
            # A worker that will not stop is one blocked on a helper process
            # the engine started, and this port runs the engine in-process, so
            # that helper is a child of the application rather than of a
            # bridge that would take it down. Quitting anyway leaves an OCR or
            # llama.cpp run holding a core with no window left to cancel it
            # from, and destroying the thread makes Qt abort the whole
            # application. Ending the child releases both: the worker was
            # waiting on it, so it returns, which is why the wait is repeated
            # rather than skipped.
            stop_leftover_children()
            wait_for_workers(2000)
        super().closeEvent(event)

    # -- shared state helpers ----------------------------------------------
    def active_document(self) -> dict[str, Any] | None:
        results = (self.result or {}).get("results") or []
        return results[0] if results else None

    def _clear_banners(self) -> None:
        self.error_banner.hide()
        self.notice_banner.hide()
        self._close_overlay()

    def show_error(self, message: str) -> None:
        self.error_banner.show_message(message)

    def show_notice(self, message: str) -> None:
        self.notice_banner.show_message(message)

    def _set_profile(self, profile: str) -> None:
        self.profile = profile

    def _refresh_command_bar(self) -> None:
        single = self.job_tabs.value() == "Single Job"
        self.open_button.setVisible(single)
        self.selected_name.setVisible(single and bool(self.single_path))
        self.selected_name.setText(Path(self.single_path).name if self.single_path else "")
        self.export_button.setVisible(single and self.active_document() is not None)
        # A batch is a queue of documents; one page range across all of them is
        # not a thing the engine is asked for, so the field is Single Job only.
        self.pages_field.setVisible(single)
        blocked = any(item.get("status") == "blocked" for item in self.batch_preflight.values())
        pending = self.batch_view.pending_paths()
        can_convert = (bool(self.single_path) if single else bool(pending) and not blocked) and not self.running
        self.convert_button.setEnabled(can_convert)
        self.convert_button.setText("Converting…" if self.running else " Convert")

    def _refresh_workspace(self) -> None:
        single = self.job_tabs.value() == "Single Job"
        self.workspace_stack.setCurrentIndex(0 if single else 1)
        document = self.active_document()
        self.source_panel.set_source(self.single_path, document, self._selected_block())
        self.output_panel.set_document(document, self.selected_block_id)
        self.evidence_panel.set_document(document, self.selected_block_id)
        self.batch_view.set_state(self.batch_items, self.batch_preflight, self.result if self.result and self.result.get("batch_id") else None)
        self.diagnostics_view.set_state(self.health, document)
        self._refresh_command_bar()

    def _selected_block(self) -> dict[str, Any] | None:
        document = self.active_document()
        blocks = (document or {}).get("blocks") or []
        return next((block for block in blocks if str(block.get("id")) == self.selected_block_id), blocks[0] if blocks else None)

    def _select_block(self, block_id: str) -> None:
        self.selected_block_id = block_id
        self._refresh_workspace()

    def _select_page(self, page_number: int) -> None:
        document = self.active_document()
        if not document:
            return
        target = next((block for block in document.get("blocks", []) if str(block.get("page")) == f"page-{page_number}"), None)
        self.selected_block_id = str(target.get("id")) if target else None
        self._refresh_workspace()

    # -- workers -----------------------------------------------------------
    def _spawn(self, name: str, kind: str, message: str, job: Callable[..., Any], on_done: Callable[[Any], None], total: int | None = None) -> WorkThread | None:
        if name in self.workers:
            return None
        self.task_card.show_progress({"kind": kind, "message": message, "percent": 0, "current": 1 if total else None, "total": total})
        worker = WorkThread(job)
        worker.progress.connect(lambda update: self.task_card.show_progress({"kind": kind, **update}))
        worker.completed.connect(on_done)
        worker.failed.connect(self.show_error)
        worker.finished.connect(lambda: (self.workers.pop(name, None), self.task_card.hide_progress(), self._finish_running(name)))
        self.workers[name] = worker
        worker.start()
        return worker

    def _finish_running(self, name: str) -> None:
        if name == "convert":
            self.running = False
        self._refresh_workspace()

    # -- single job --------------------------------------------------------
    def choose_single(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open local document", str(Path.home()), FILE_FILTER)
        if path:
            self.single_path = path
            self.result = None
            self.selected_block_id = None
            self.error_banner.hide()
            self._refresh_workspace()

    def convert(self) -> None:
        single = self.job_tabs.value() == "Single Job"
        if self.running:
            return
        self.running = True
        self.error_banner.hide()
        self._refresh_command_bar()
        preferences = self.preferences
        if single:
            paths = [self.single_path] if self.single_path else []
            if not paths:
                self.running = False
                return
            try:
                pages = self.service.parse_pages(self.pages_field.text())
            except ValueError as refusal:
                # Said here rather than after a job has been started, since the
                # engine would refuse the same text for the same reason.
                self.running = False
                self.error_banner.show_message(str(refusal))
                self._refresh_command_bar()
                return
            self._spawn("convert", "conversion", "Preparing the local converter",
                        lambda progress: self.service.convert(paths, self.profile, preferences.get("cache_policy", "use"), preferences.get("outputs", list(OUTPUTS)), progress, pages),
                        self._conversion_finished, total=1)
        else:
            if not self.batch_id:
                self.running = False
                return
            self.service.store.request_batch_state(self.batch_id, None)
            worker = self._spawn("convert", "conversion", "Preparing the batch",
                                 lambda progress: self.service.run_batch(self.batch_id, progress),
                                 self._conversion_finished, total=len(self.batch_view.pending_paths()) or None)
            if worker:
                worker.progress.connect(self._batch_run_tick)

    def _conversion_finished(self, payload: dict[str, Any]) -> None:
        self.result = payload
        self.selected_block_id = None
        document = self.active_document()
        if document and self.job_tabs.value() == "Single Job":
            self.single_path = document.get("source_path", self.single_path)
        if not document and payload.get("failures"):
            self.show_error(str(payload["failures"][0].get("error", "No conversion was produced.")))
        if self.batch_id:
            self._reload_batch()
        self._load_history()
        self._refresh_workspace()

    # -- review and repair -------------------------------------------------
    def _review_block(self, block_id: str, action: str) -> None:
        if action == "edit":
            document = self.active_document()
            block = next((item for item in (document or {}).get("blocks", []) if str(item.get("id")) == block_id), None)
            if block is None:
                return
            editor = ReviewEditor(self.centralWidget(), block)
            editor.dismissed.connect(self._close_overlay)
            editor.saved.connect(lambda value, text: (self._close_overlay(), self._apply_review(value, "edit", text=text)))
            self._show_overlay(editor)
            return
        self._apply_review(block_id, action)

    def _apply_review(self, block_id: str, action: str, text: str | None = None, candidate_index: int | None = None) -> None:
        document = self.active_document()
        if not document:
            return
        try:
            reply = self.service.apply_review(document["outputs"]["ir"], block_id, action, text, candidate_index)
        except Exception as exc:
            self.show_error(str(exc))
            return
        self._replace_block(reply.get("block"))
        self.show_notice(
            "Edit recorded locally. The native candidate remains retained." if action == "edit"
            else "Selected candidate applied locally. Its provenance remains retained." if action == "restore_candidate"
            else "Review decision recorded locally.")

    def _request_repair(self, block_id: str, repair_mode: str) -> None:
        document = self.active_document()
        if not document:
            return
        enabled = list(self.preferences.get("enabled_model_ids") or [])
        self._spawn("repair", "repair", "Preparing the selected source region",
                    lambda progress: self.service.request_repair(document["outputs"]["ir"], block_id, repair_mode, progress, enabled_model_ids=enabled),
                    self._repair_finished)

    def _repair_finished(self, reply: dict[str, Any]) -> None:
        self._replace_block(reply.get("block"))
        if reply.get("message"):
            self.show_notice(str(reply["message"]))

    def _replace_block(self, replacement: dict[str, Any] | None) -> None:
        document = self.active_document()
        if not replacement or not document or not self.result:
            self._refresh_workspace()
            return
        blocks = [replacement if str(item.get("id")) == str(replacement.get("id")) else item for item in document["blocks"]]
        results = [{**item, "blocks": blocks} if item is document else item for item in self.result["results"]]
        self.result = {**self.result, "results": results}
        self._refresh_workspace()

    # -- batch -------------------------------------------------------------
    def _batch_run_tick(self, update: dict[str, Any]) -> None:
        if update.get("stage") in ("starting", "complete"):
            self._reload_batch()

    def _reload_batch(self) -> None:
        if not self.batch_id:
            return
        try:
            self.batch_items = self.service.store.batch_items(self.batch_id)
        except Exception as exc:
            self.show_error(str(exc))
            return
        self._refresh_workspace()

    def _add_batch_documents(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add documents to batch", str(Path.home()), FILE_FILTER)
        if not paths:
            return
        try:
            if self.batch_id:
                self.service.store.append_batch_items(self.batch_id, paths)
            else:
                self.batch_id = self.service.create_batch(paths, self.profile, self.preferences.get("cache_policy", "use"), self.preferences.get("outputs", list(OUTPUTS)))
        except Exception as exc:
            self.show_error(str(exc))
            return
        self._reload_batch()
        pending = self.batch_view.pending_paths()
        if pending:
            self._spawn("preflight", "preflight", "Preparing local file inspection",
                        lambda progress: self.service.preflight(pending, progress),
                        self._preflight_finished, total=len(pending))

    def _preflight_finished(self, reply: dict[str, Any]) -> None:
        self.batch_preflight = {str(item.get("source_path")): item for item in reply.get("items", [])}
        self._refresh_workspace()

    def _set_item_state(self, path: str, state: str) -> None:
        item = next((candidate for candidate in self.batch_items if candidate["source_path"] == path), None)
        if not item or not self.batch_id:
            return
        try:
            self.service.store.set_item_state(item["id"], state)
        except Exception as exc:
            self.show_error(str(exc))
        self._reload_batch()

    def _clear_pending(self) -> None:
        for item in self.batch_items:
            if item["status"] in ("queued", "paused"):
                try:
                    self.service.store.set_item_state(item["id"], "cancelled")
                except Exception as exc:
                    self.show_error(str(exc))
                    break
        self.batch_preflight = {}
        self._reload_batch()

    def _export_batch(self) -> None:
        documents = [item for item in ((self.result or {}).get("results") or []) if item.get("outputs", {}).get("ir")]
        self._export_documents(documents)

    def _export_active(self) -> None:
        document = self.active_document()
        self._export_documents([document] if document else [])

    def _export_documents(self, documents: list[dict[str, Any]]) -> None:
        if not documents:
            return
        destination = QFileDialog.getExistingDirectory(self, "Export Philon conversion to…")
        if not destination:
            return

        def export_all(progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            total = len(documents)
            exports = []
            for index, document in enumerate(documents):
                progress({"stage": "copying", "message": f"Copying {Path(document.get('source_path', '')).name}",
                          "percent": round(index / total * 100), "current": index + 1, "total": total})
                exports.append(self.service.export_conversion(document["outputs"]["ir"], destination))
            return {"exports": exports, "destination": destination}

        self._spawn("export", "export", "Preparing export", export_all, self._export_finished, total=len(documents))

    def _export_finished(self, reply: dict[str, Any]) -> None:
        exports = reply["exports"]
        files = sum(item["files"] for item in exports)
        self.show_notice(f"Exported {files} files from {len(exports)} conversion{'' if len(exports) == 1 else 's'} to {reply['destination']}.")

    # -- library, models, diagnostics, settings ----------------------------
    def _load_history(self, show_progress: bool = False) -> None:
        def job(_progress: Callable[[dict[str, Any]], None]) -> list[dict[str, Any]]:
            return self.service.store.list_jobs()

        if show_progress:
            self._spawn("library", "library", "Reading locally retained conversion history", job, self._history_loaded)
        elif "library" not in self.workers:
            worker = WorkThread(job)
            worker.completed.connect(self._history_loaded)
            worker.failed.connect(lambda _message: None)
            worker.finished.connect(lambda: self.workers.pop("library", None))
            self.workers["library"] = worker
            worker.start()

    def _history_loaded(self, jobs: list[dict[str, Any]]) -> None:
        self.history = jobs
        self.main_tabs["library"].set_count(len(jobs))
        self.library_view.set_history(jobs)

    def _clean_library(self) -> None:
        self._spawn("library", "library", "Removing local library records",
                    lambda _progress: self.service.store.clear_jobs(), self._library_cleaned)

    def _library_cleaned(self, removed: int) -> None:
        self._history_loaded([])
        self.show_notice(f"Removed {removed} library record{'' if removed == 1 else 's'}. Exported files were kept.")

    def _open_history_job(self, job_id: str) -> None:
        self._spawn("open-job", "library", "Opening the selected local conversion",
                    lambda _progress: self.service.store.job_payload(job_id), self._history_job_opened)

    def _history_job_opened(self, payload: dict[str, Any]) -> None:
        self.result = payload
        self.selected_block_id = None
        document = self.active_document()
        self.single_path = (document or {}).get("source_path")
        self.show_view("workspace")
        self.job_tabs.select("Single Job", announce=False)
        self._refresh_workspace()

    def _load_models(self) -> None:
        self._spawn("models", "models", "Checking approved local model packs",
                    lambda _progress: self.service.models(), self._models_loaded)

    def _models_loaded(self, reply: dict[str, Any]) -> None:
        self.models_view.set_packs(reply.get("packs", []), list(self.preferences.get("enabled_model_ids") or []))

    def _download_model(self, pack_id: str) -> None:
        """Fetch one approved pack, reporting progress like any other local job."""
        self._spawn("models", "download", f"Downloading {pack_id.replace('-', ' ')}",
                    lambda progress: self.service.fetch_model(pack_id, progress), self._model_fetched)

    def _model_fetched(self, reply: dict[str, Any]) -> None:
        if reply.get("models", {}).get("packs"):
            self._models_loaded(reply["models"])
        if reply.get("status") != "installed":
            self.show_error(reply.get("message") or f"{reply.get('pack_id')} was not installed.")

    def _remove_model(self, pack_id: str) -> None:
        self._spawn("models", "models", f"Removing {pack_id.replace('-', ' ')}",
                    lambda _progress: self.service.remove_model(pack_id), self._model_fetched)

    def _toggle_model(self, pack_id: str, enabled: bool) -> None:
        ids = [value for value in (self.preferences.get("enabled_model_ids") or []) if value != pack_id]
        if enabled:
            ids.append(pack_id)
        self._save_preferences({**self.preferences, "enabled_model_ids": ids})
        self.models_view.set_packs(self.models_view.packs, ids)

    def _inspect_engine(self) -> None:
        try:
            reply = self.service.diagnostics()
        except Exception as exc:
            self.show_error(str(exc))
            return
        actions = reply.get("review_actions", [])
        self.health = f"{reply.get('engine', 'engine')} is ready. Local-only: {'yes' if reply.get('local_only') else 'no'}. {len(actions)} review actions available."
        self.diagnostics_view.set_state(self.health, self.active_document())

    def _save_preferences(self, values: dict[str, Any]) -> None:
        merged = {**self.preferences, **values}
        try:
            self.service.save_preferences(merged)
        except Exception as exc:
            self.show_error(str(exc))
            self.settings_view.set_preferences(self.preferences)
            return
        profile_changed = merged.get("profile") != self.preferences.get("profile")
        self.preferences = merged
        if profile_changed:
            self.profile = str(merged.get("profile", "Balanced"))
            self.profile_picker.select(self.profile, announce=False)

    def _restore_defaults(self) -> None:
        defaults = {"profile": "Balanced", "cache_policy": "use", "outputs": list(DEFAULT_OUTPUTS), "enabled_model_ids": []}
        self._save_preferences(defaults)
        self.settings_view.set_preferences(defaults)

    # -- appearance rebuild --------------------------------------------------
    def export_state(self) -> dict[str, Any]:
        return {
            "single_path": self.single_path,
            "result": self.result,
            "selected_block_id": self.selected_block_id,
            "batch_preflight": self.batch_preflight,
            "health": self.health,
            "profile": self.profile,
            "view": self.view_names[self.views.currentIndex()],
            "job_tab": self.job_tabs.value(),
            "output_format": self.output_panel.format,
        }

    def adopt_state(self, state: dict[str, Any]) -> None:
        """Carry the working state into a window rebuilt for a new appearance."""
        self.single_path = state.get("single_path")
        self.result = state.get("result")
        self.selected_block_id = state.get("selected_block_id")
        self.batch_preflight = dict(state.get("batch_preflight") or {})
        self.health = state.get("health")
        self.profile = str(state.get("profile") or self.profile)
        self.profile_picker.select(self.profile, announce=False)
        self.job_tabs.select(str(state.get("job_tab") or "Single Job"), announce=False)
        self.output_panel.set_format(str(state.get("output_format") or "Preview"))
        self.show_view(str(state.get("view") or "workspace"))
        self._refresh_workspace()

    # -- overlays ----------------------------------------------------------
    def _show_overlay(self, overlay: Overlay) -> None:
        self._close_overlay()
        self.overlay = overlay
        overlay.show()
        overlay.raise_()
        overlay.setFocus()

    def _close_overlay(self) -> None:
        if self.overlay:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None

    def _show_splash(self) -> None:
        splash = Splash(self.centralWidget())
        splash.dismissed.connect(self._close_overlay)
        self._show_overlay(splash)
