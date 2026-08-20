"""PyQt6 application for the local Philon conversion workspace."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

# PySide6 is the distributable runtime. The small PyQt6 fallback preserves
# development/test usability on a Mac that already has only that binding; it
# is never declared as a production or packaged dependency.
try:  # pragma: no cover - exercised in packaged PySide6 releases
    from PySide6.QtCore import QThread, Qt, QUrl, Signal
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QProgressBar, QProgressDialog, QSizePolicy, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover - local developer fallback only
    from PyQt6.QtCore import QThread, Qt, QUrl, pyqtSignal as Signal
    from PyQt6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QProgressBar, QProgressDialog, QSizePolicy, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .core import DEFAULT_OUTPUTS, OUTPUTS, PROFILES, PhilonService

FILTER = "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.webp)"


def label_path(path: str) -> str:
    return Path(path).name if path else "No source selected"


class ElidedLabel(QLabel):
    """A single-line label that preserves the full value in its tooltip."""

    def __init__(self, text: str = "") -> None:
        self._full_text = text
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setToolTip(text)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._refresh_text()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        available_width = max(0, self.contentsRect().width())
        visible_text = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, available_width)
        super().setText(visible_text)


def warning_text(record: dict[str, Any]) -> str:
    page = f" · page {record['page']}" if record.get("page") else ""
    return f"{record.get('severity', 'warning').upper()} · {record.get('code', 'UNKNOWN')}{page}\n{record.get('message', '')}"


class WorkThread(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    # Every worker that has been started and has not finished. Qt aborts the
    # process when a running QThread is destroyed, so shutdown needs to know
    # what is still in flight rather than trusting each page to remember.
    _live: set["WorkThread"] = set()

    def __init__(self, job: Callable[[Callable[[dict[str, Any]], None]], Any]) -> None:
        super().__init__()
        self.job = job
        self.finished.connect(lambda: WorkThread._live.discard(self))

    def start(self, *args: Any, **kwargs: Any) -> None:
        WorkThread._live.add(self)
        super().start(*args, **kwargs)

    def run(self) -> None:
        try:
            self.completed.emit(self.job(self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


def wait_for_workers(timeout_ms: int = 5000) -> bool:
    """Let background work finish before the threads running it are destroyed.

    Returns whether everything stopped in time. The wait is bounded rather than
    indefinite: a quit that hangs on a stuck worker would be worse than the
    abort it is avoiding, and conversion work is written to be interruptible at
    a document boundary rather than mid-write.
    """
    stopped = True
    for worker in list(WorkThread._live):
        if worker.isRunning() and not worker.wait(timeout_ms):
            stopped = False
    return stopped


class SourcePreview(QGraphicsView):
    """Page raster preview with source-coordinate rectangle overlays."""

    page_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.document: dict[str, Any] | None = None
        self.page_number = 1
        self.zoom = 1.0
        self.fit_enabled = True
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._selected_block: dict[str, Any] | None = None
        self.setMinimumWidth(260)

    def set_document(self, document: dict[str, Any] | None) -> None:
        self.document, self.page_number, self._selected_block = document, 1, None
        self._draw()

    def select_block(self, block: dict[str, Any] | None) -> None:
        self._selected_block = block
        if block and str(block.get("page", "")).startswith("page-"):
            self.page_number = int(str(block["page"]).split("-")[-1])
        self._draw()
        self.page_changed.emit(self.page_number)

    def set_page(self, page: int) -> None:
        count = len(self.document.get("pages", [])) if self.document else 1
        self.page_number = max(1, min(page, max(1, count)))
        self._draw()
        self.page_changed.emit(self.page_number)

    def set_zoom(self, zoom: float, fit: bool = False) -> None:
        self.zoom, self.fit_enabled = zoom, fit
        self._apply_transform()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self.fit_enabled:
            self._apply_transform()

    def _asset_for_page(self) -> Path | None:
        if not self.document:
            return None
        assets = self.document.get("outputs", {}).get("assets", [])
        if isinstance(assets, list) and len(assets) >= self.page_number:
            candidate = Path(assets[self.page_number - 1])
            if candidate.exists():
                return candidate
        return None

    def _draw(self) -> None:
        self.scene.clear()
        self._pixmap_item = None
        source = self._asset_for_page()
        if not source:
            placeholder = self.scene.addText("Source preview appears after a conversion exports local page rasters.")
            placeholder.setDefaultTextColor(QColor("#6c6a64"))
            self.scene.setSceneRect(placeholder.boundingRect())
            return
        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            self.scene.addText("Philon could not render this local preview.")
            return
        self._pixmap_item = self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(self._pixmap_item.boundingRect())
        block = self._selected_block
        bbox = block.get("bbox") if block else None
        page = next((item for item in self.document.get("pages", []) if item.get("number") == self.page_number), {}) if self.document else {}
        if bbox and page and bbox.get("coordinate_space") in {"pdf-page-points", "normalized-image"}:
            scale_x = pixmap.width() / max(1, float(page.get("width", pixmap.width())))
            scale_y = pixmap.height() / max(1, float(page.get("height", pixmap.height())))
            if bbox["coordinate_space"] == "normalized-image":
                x, y = float(bbox["x0"]) * pixmap.width(), (1 - float(bbox["y1"])) * pixmap.height()
                width, height = (float(bbox["x1"]) - float(bbox["x0"])) * pixmap.width(), (float(bbox["y1"]) - float(bbox["y0"])) * pixmap.height()
            else:
                x = float(bbox["x0"]) * scale_x
                y = pixmap.height() - float(bbox["y1"]) * scale_y
                width = (float(bbox["x1"]) - float(bbox["x0"])) * scale_x
                height = (float(bbox["y1"]) - float(bbox["y0"])) * scale_y
            rect = QGraphicsRectItem(x, y, width, height)
            rect.setPen(QPen(QColor("#b45309"), 3))
            rect.setBrush(QColor(245, 158, 11, 38))
            self.scene.addItem(rect)
        self._apply_transform()

    def _apply_transform(self) -> None:
        if not self._pixmap_item:
            return
        self.resetTransform()
        if self.fit_enabled:
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.scale(self.zoom, self.zoom)


class SingleJobPage(QWidget):
    conversion_loaded = Signal(object)

    def __init__(self, service: PhilonService) -> None:
        super().__init__()
        self.service, self.source_path, self.conversion, self.worker = service, "", None, None
        self.progress_dialog: QProgressDialog | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 20)
        controls = QHBoxLayout()
        self.open_button = QPushButton("Open document…")
        self.open_button.clicked.connect(self.choose_source)
        controls.addWidget(self.open_button)
        self.source_label = ElidedLabel("Choose a local PDF or image to begin.")
        self.source_label.setObjectName("muted")
        controls.addWidget(self.source_label, 1)
        controls.addWidget(QLabel("Profile"))
        self.profile = QComboBox(); self.profile.addItems(PROFILES)
        self.profile.setCurrentText(self.service.preferences().get("profile", "Balanced"))
        controls.addWidget(self.profile)
        self.preflight_button = QPushButton("Preflight")
        self.preflight_button.clicked.connect(self.preflight)
        controls.addWidget(self.preflight_button)
        self.convert_button = QPushButton("Convert")
        self.convert_button.setObjectName("primary")
        self.convert_button.clicked.connect(self.convert)
        controls.addWidget(self.convert_button)
        root.addLayout(controls)
        self.preflight_label = QLabel("Local-only intake preflight has not run.")
        self.preflight_label.setWordWrap(True)
        self.preflight_label.setObjectName("note")
        root.addWidget(self.preflight_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        source_box = QWidget(); source_layout = QVBoxLayout(source_box)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_header = QHBoxLayout(); source_header.addWidget(QLabel("Source preview")); source_header.addStretch(1)
        self.previous_page = QPushButton("‹"); self.next_page = QPushButton("›")
        self.previous_page.clicked.connect(lambda: self.preview.set_page(self.preview.page_number - 1))
        self.next_page.clicked.connect(lambda: self.preview.set_page(self.preview.page_number + 1))
        source_header.addWidget(self.previous_page); source_header.addWidget(self.next_page)
        self.fit_button = QPushButton("Fit"); self.actual_button = QPushButton("Actual")
        self.fit_button.clicked.connect(lambda: self.preview.set_zoom(1, True))
        self.actual_button.clicked.connect(lambda: self.preview.set_zoom(1, False))
        source_header.addWidget(self.fit_button); source_header.addWidget(self.actual_button)
        source_layout.addLayout(source_header)
        self.preview = SourcePreview(); source_layout.addWidget(self.preview, 1)
        self.preview.page_changed.connect(self.page_changed)
        self.page_label = QLabel("Page 0 of 0 · source is never altered")
        self.page_label.setObjectName("muted"); source_layout.addWidget(self.page_label)
        split.addWidget(source_box)

        output_box = QWidget(); output_layout = QVBoxLayout(output_box); output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(QLabel("Readable output and block review"))
        self.format = QComboBox(); self.format.addItems(["Readable", "Markdown", "Philon IR"])
        self.format.currentTextChanged.connect(self.refresh_output)
        output_layout.addWidget(self.format)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True); self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        output_layout.addWidget(self.output, 1)
        self.blocks = QTreeWidget(); self.blocks.setHeaderLabels(["Block", "Type", "Confidence"]); self.blocks.setRootIsDecorated(False)
        self.blocks.itemSelectionChanged.connect(self.select_block)
        output_layout.addWidget(self.blocks, 1)
        split.addWidget(output_box)

        evidence_box = QWidget(); evidence_layout = QVBoxLayout(evidence_box); evidence_layout.setContentsMargins(0, 0, 0, 0)
        evidence_layout.addWidget(QLabel("Evidence and repair"))
        self.evidence = QPlainTextEdit(); self.evidence.setReadOnly(True)
        evidence_layout.addWidget(self.evidence, 1)
        action_row = QHBoxLayout()
        self.accept_button = QPushButton("Accept")
        self.edit_button = QPushButton("Edit")
        self.restore_button = QPushButton("Restore candidate")
        action_row.addWidget(self.accept_button); action_row.addWidget(self.edit_button); action_row.addWidget(self.restore_button)
        evidence_layout.addLayout(action_row)
        repair_row = QHBoxLayout(); self.repair_mode = QComboBox(); self.repair_mode.addItems(["transcription", "table", "formula"])
        self.repair_button = QPushButton("Request local repair")
        repair_row.addWidget(self.repair_mode); repair_row.addWidget(self.repair_button); evidence_layout.addLayout(repair_row)
        self.crop_button = QPushButton("Open selected source crop")
        self.crop_button.clicked.connect(self.open_crop)
        evidence_layout.addWidget(self.crop_button)
        self.edit_text = QTextEdit(); self.edit_text.setPlaceholderText("Selected block text — edits create a retained alternative, never overwrite source evidence.")
        evidence_layout.addWidget(self.edit_text, 1)
        self.accept_button.clicked.connect(lambda: self.apply_review("accept"))
        self.edit_button.clicked.connect(lambda: self.apply_review("edit"))
        self.restore_button.clicked.connect(lambda: self.apply_review("restore_candidate"))
        self.repair_button.clicked.connect(self.repair)
        split.addWidget(evidence_box)
        split.setSizes([430, 430, 360])
        root.addWidget(split, 1)
        self.progress = QProgressBar(); self.progress.setTextVisible(True); self.progress.hide(); root.addWidget(self.progress)

    def choose_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open local document", str(Path.home()), FILTER)
        if path:
            self.source_path, self.conversion = path, None
            self.source_label.setText(label_path(path))
            self.preflight_label.setText("Ready for local preflight. No source has been sent anywhere.")
            self.preview.set_document(None); self.output.clear(); self.blocks.clear(); self.evidence.clear()

    def preflight(self) -> None:
        if not self.source_path or self.worker:
            return
        self.set_busy(True, "Inspecting local source…")
        self.worker = WorkThread(lambda progress: self.service.preflight([self.source_path], progress))
        self.worker.progress.connect(self.update_progress)
        self.worker.completed.connect(self.preflight_finished); self.worker.failed.connect(self.error); self.worker.finished.connect(lambda: self.set_busy(False))
        self.worker.start()

    def preflight_finished(self, reply: dict[str, Any]) -> None:
        self.worker = None
        try:
            item = reply["items"][0]
            if item["status"] == "ready":
                details = item["preflight"]
                self.preflight_label.setText(f"Ready · {details['kind'].upper()} · {details.get('declared_page_count') or '?'} page(s) · {details['bytes']:,} bytes · route: {item['route']}")
            else:
                self.preflight_label.setText(f"Blocked · {item['error']}")
        except Exception as exc:
            self.error(str(exc))

    def convert(self) -> None:
        if not self.source_path or self.worker:
            return
        preferences = self.service.preferences()
        self.set_busy(True, "Converting locally…")
        self.worker = WorkThread(lambda progress: self.service.convert([self.source_path], self.profile.currentText(), preferences.get("cache_policy", "use"), preferences.get("outputs", list(OUTPUTS)), progress))
        self.worker.progress.connect(self.update_progress)
        self.worker.completed.connect(self.conversion_finished); self.worker.failed.connect(self.error); self.worker.finished.connect(lambda: self.set_busy(False))
        self.worker.start()

    def conversion_finished(self, payload: dict[str, Any]) -> None:
        self.worker = None
        if not payload.get("results"):
            self.error(payload.get("failures", [{"error": "No conversion was produced."}])[0]["error"]); return
        self.load_document(payload["results"][0]); self.conversion_loaded.emit(payload)

    def load_document(self, document: dict[str, Any]) -> None:
        self.conversion = document; self.source_path = document.get("source_path", self.source_path)
        self.source_label.setText(label_path(self.source_path))
        self.preview.set_document(document)
        self.page_label.setText(f"Page 1 of {len(document.get('pages', []))} · source is never altered")
        self.blocks.clear()
        for block in document.get("blocks", []):
            item = QTreeWidgetItem([block.get("text", "").replace("\n", " ")[:75], block.get("type", ""), f"{float(block.get('source', {}).get('confidence', 0)):.0%}"])
            item.setData(0, Qt.ItemDataRole.UserRole, block)
            self.blocks.addTopLevelItem(item)
        self.refresh_output()
        self.evidence.setPlainText("\n\n".join(warning_text(item) for item in document.get("warnings", [])) or "No warnings were emitted. Select a block to inspect source evidence, alternatives, and review history.")

    def refresh_output(self) -> None:
        if not self.conversion:
            self.output.setPlainText("Evidence is retained before a readable export is presented."); return
        format_name = self.format.currentText()
        if format_name == "Philon IR":
            self.output.setPlainText(json.dumps({"pages": self.conversion.get("pages", []), "blocks": self.conversion.get("blocks", [])}, indent=2, ensure_ascii=False))
        elif format_name == "Markdown":
            path = Path(self.conversion.get("outputs", {}).get("markdown", ""))
            self.output.setPlainText(path.read_text(encoding="utf-8") if path.exists() else "Markdown was not selected for this conversion.")
        else:
            self.output.setPlainText("\n\n".join(("#" * (item.get("level") or 2) + " " if item.get("type") == "heading" else "") + item.get("text", "") for item in self.conversion.get("blocks", [])))

    def selected_block(self) -> dict[str, Any] | None:
        items = self.blocks.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def select_block(self) -> None:
        block = self.selected_block()
        if not block:
            return
        self.preview.select_block(block)
        page = int(str(block.get("page", "page-1")).split("-")[-1])
        self.page_label.setText(f"Page {page} of {len(self.conversion.get('pages', []))} · selected source region synchronized")
        evidence = block.get("evidence", {})
        self.evidence.setPlainText(json.dumps({"source": block.get("source"), "bbox": block.get("bbox"), "findings": evidence.get("findings", {}), "validation": evidence.get("validation", []), "alternatives": evidence.get("alternatives", []), "repair_history": evidence.get("repair_history", []), "review": block.get("review")}, indent=2, ensure_ascii=False))
        self.edit_text.setPlainText(block.get("text", ""))

    def page_changed(self, page: int) -> None:
        total = len(self.conversion.get("pages", [])) if self.conversion else 0
        self.page_label.setText(f"Page {page} of {total} · source is never altered")

    def open_crop(self) -> None:
        block = self.selected_block()
        alternatives = block.get("evidence", {}).get("alternatives", []) if block else []
        crop = next((item.get("source_crop") for item in reversed(alternatives) if isinstance(item, dict) and item.get("source_crop")), None)
        if not crop or not Path(crop).is_file():
            self.error("No retained source crop exists for this block. Request a supported local repair first."); return
        QDesktopServices.openUrl(QUrl.fromLocalFile(crop))

    def apply_review(self, action: str) -> None:
        block = self.selected_block()
        if not block or not self.conversion:
            self.error("Select one output block before recording a review."); return
        try:
            reply = self.service.apply_review(self.conversion["outputs"]["ir"], block["id"], action, self.edit_text.toPlainText() if action == "edit" else None)
            self.replace_block(reply.get("block")); self.status_message("Review recorded locally; alternatives and repair history remain retained.")
        except Exception as exc:
            self.error(str(exc))

    def repair(self) -> None:
        block = self.selected_block()
        if not block or not self.conversion or self.worker:
            self.error("Select one evidence block before requesting a bounded local repair."); return
        self.set_busy(True, "Preparing local repair…")
        self.worker = WorkThread(lambda progress: self.service.request_repair(self.conversion["outputs"]["ir"], block["id"], self.repair_mode.currentText(), progress))
        self.worker.progress.connect(self.update_progress); self.worker.completed.connect(self.repair_finished); self.worker.failed.connect(self.error); self.worker.finished.connect(lambda: self.set_busy(False)); self.worker.start()

    def repair_finished(self, reply: dict[str, Any]) -> None:
        self.worker = None
        self.replace_block(reply.get("block")); self.status_message(reply.get("message", "Local repair request completed."))

    def replace_block(self, replacement: dict[str, Any] | None) -> None:
        if not replacement or not self.conversion:
            return
        self.conversion["blocks"] = [replacement if item.get("id") == replacement.get("id") else item for item in self.conversion["blocks"]]
        self.load_document(self.conversion)

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.open_button.setDisabled(busy); self.convert_button.setDisabled(busy); self.preflight_button.setDisabled(busy); self.repair_button.setDisabled(busy)
        self.progress.setVisible(busy); self.progress.setRange(0, 0 if busy else 1); self.progress.setFormat(text)
        if busy:
            self.progress_dialog = QProgressDialog(text, None, 0, 0, self)
            self.progress_dialog.setWindowTitle("Philon is working locally")
            self.progress_dialog.setCancelButton(None)
            self.progress_dialog.setMinimumDuration(0)
            self.progress_dialog.setAutoClose(False)
            self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress_dialog.show()
        elif self.progress_dialog:
            self.progress_dialog.close(); self.progress_dialog = None

    def update_progress(self, update: dict[str, Any]) -> None:
        message = update.get("message", "Working locally…")
        if update.get("indeterminate"):
            self.progress.setRange(0, 0); self.progress.setFormat(message)
            if self.progress_dialog: self.progress_dialog.setRange(0, 0); self.progress_dialog.setLabelText(message)
            return
        percent = int(update.get("percent", 0))
        self.progress.setRange(0, 100); self.progress.setValue(percent)
        count = f" · {update['current']} of {update['total']}" if update.get("current") and update.get("total") else ""
        self.progress.setFormat(f"{percent}% · {message}{count}")
        if self.progress_dialog:
            self.progress_dialog.setRange(0, 100); self.progress_dialog.setValue(percent); self.progress_dialog.setLabelText(f"{message}{count}")

    def error(self, message: str) -> None:
        self.worker = None; self.set_busy(False); QMessageBox.critical(self, "Philon", message)

    def status_message(self, message: str) -> None:
        QMessageBox.information(self, "Philon", message)


class BatchPage(QWidget):
    conversion_loaded = Signal(object)

    def __init__(self, service: PhilonService) -> None:
        super().__init__()
        self.service, self.batch_id, self.worker, self.latest_payload = service, service.store.latest_batch_id(), None, None
        self._build(); self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(20, 18, 20, 20)
        actions = QHBoxLayout()
        self.add_button = QPushButton("Add documents…"); self.add_button.clicked.connect(self.add_documents)
        self.run_button = QPushButton("Run / resume queue"); self.run_button.setObjectName("primary"); self.run_button.clicked.connect(self.run_batch)
        self.pause_button = QPushButton("Pause after current"); self.pause_button.clicked.connect(lambda: self.request_state("paused"))
        self.cancel_button = QPushButton("Cancel pending"); self.cancel_button.clicked.connect(lambda: self.request_state("cancelled"))
        self.retry_button = QPushButton("Retry selected"); self.retry_button.clicked.connect(self.retry_selected)
        self.export_button = QPushButton("Export completed…"); self.export_button.clicked.connect(self.export_completed)
        for button in (self.add_button, self.run_button, self.pause_button, self.cancel_button, self.retry_button, self.export_button): actions.addWidget(button)
        actions.addStretch(1); root.addLayout(actions)
        self.summary = QLabel("Persistent local queue · no batch selected")
        self.summary.setObjectName("note"); root.addWidget(self.summary)
        self.table = QTableWidget(0, 5); self.table.setHorizontalHeaderLabels(["Source", "State", "Preflight / error", "Warnings", "Updated"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.table, 1)
        self.progress = QProgressBar(); self.progress.hide(); root.addWidget(self.progress)

    def add_documents(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add documents to batch", str(Path.home()), FILTER)
        if not paths: return
        pref = self.service.preferences()
        try:
            if self.batch_id: self.service.store.append_batch_items(self.batch_id, paths)
            else: self.batch_id = self.service.create_batch(paths, pref.get("profile", "Balanced"), pref.get("cache_policy", "use"), pref.get("outputs", list(OUTPUTS)))
            self.refresh()
        except Exception as exc: QMessageBox.critical(self, "Philon", str(exc))

    def refresh(self) -> None:
        self.table.setRowCount(0)
        if not self.batch_id:
            return
        try:
            batch, items = self.service.store.batch(self.batch_id), self.service.store.batch_items(self.batch_id)
            counts = {state: sum(item["status"] == state for item in items) for state in {item["status"] for item in items}}
            self.summary.setText(f"{len(items)} documents · {batch['status']} · " + " · ".join(f"{count} {state}" for state, count in sorted(counts.items())))
            for index, item in enumerate(items):
                self.table.insertRow(index); result = item.get("result") or {}
                values = [label_path(item["source_path"]), item["status"], item.get("error") or "Ready for local conversion", str(len(result.get("warnings", []))) if result else "", item["updated_at"]]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value); cell.setData(Qt.ItemDataRole.UserRole, item); self.table.setItem(index, column, cell)
        except Exception as exc: QMessageBox.critical(self, "Philon", str(exc))

    def run_batch(self) -> None:
        if not self.batch_id or self.worker: return
        self.service.store.request_batch_state(self.batch_id, None)
        self.progress.show(); self.progress.setRange(0, 0); self.progress.setFormat("Preparing local batch…")
        self.worker = WorkThread(lambda progress: self.service.run_batch(self.batch_id, progress))
        self.worker.progress.connect(self.update_progress)
        self.worker.completed.connect(self.done); self.worker.failed.connect(lambda message: QMessageBox.critical(self, "Philon", message)); self.worker.finished.connect(self.work_finished); self.worker.start()

    def done(self, payload: dict[str, Any]) -> None:
        self.latest_payload = payload; self.conversion_loaded.emit(payload); self.refresh()

    def work_finished(self) -> None:
        self.worker = None; self.progress.hide(); self.refresh()

    def update_progress(self, update: dict[str, Any]) -> None:
        if update.get("indeterminate"):
            self.progress.setRange(0, 0); self.progress.setFormat(update.get("message", "Working locally…")); return
        percent = int(update.get("percent", 0))
        self.progress.setRange(0, 100); self.progress.setValue(percent)
        count = f" · {update['current']} of {update['total']}" if update.get("current") and update.get("total") else ""
        self.progress.setFormat(f"{percent}% · {update.get('message', 'Working locally…')}{count}")

    def request_state(self, state: str) -> None:
        if not self.batch_id: return
        self.service.store.request_batch_state(self.batch_id, state); self.refresh()

    def retry_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0: return
        item = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        try: self.service.store.set_item_state(item["id"], "queued"); self.refresh()
        except Exception as exc: QMessageBox.critical(self, "Philon", str(exc))

    def export_completed(self) -> None:
        if not self.batch_id or self.worker: return
        destination = QFileDialog.getExistingDirectory(self, "Export completed conversion bundles")
        if not destination: return
        conversions = [item.get("result") or {} for item in self.service.store.batch_items(self.batch_id)]
        documents = [item for item in conversions if item.get("outputs", {}).get("ir")]
        if not documents: return
        self.progress.show(); self.progress.setRange(0, 0); self.progress.setFormat("Preparing completed exports…")
        def export_all(progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            exported, failures = 0, []
            total = len(documents)
            for index, document in enumerate(documents):
                def report_file(update: dict[str, Any], *, index: int = index) -> None:
                    file_percent = int(update.get("percent", 0))
                    progress({**update, "current": index + 1, "total": total, "percent": round((index + file_percent / 100) / total * 100)})
                try: self.service.export_conversion(document["outputs"]["ir"], destination, report_file); exported += 1
                except Exception as exc: failures.append(str(exc))
            return {"exported": exported, "failures": failures}
        self.worker = WorkThread(export_all); self.worker.progress.connect(self.update_progress); self.worker.completed.connect(self.exports_finished); self.worker.failed.connect(lambda message: QMessageBox.critical(self, "Philon", message)); self.worker.finished.connect(self.work_finished); self.worker.start()

    def exports_finished(self, result: dict[str, Any]) -> None:
        QMessageBox.information(self, "Philon", f"Exported {result['exported']} completed conversion bundle(s)." + (f"\n{len(result['failures'])} failed." if result["failures"] else ""))


class SecondaryPage(QWidget):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__(); layout = QVBoxLayout(self); layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel(title); heading.setObjectName("heading"); layout.addWidget(heading)
        note = QLabel(subtitle); note.setObjectName("note"); note.setWordWrap(True); layout.addWidget(note)
        self.body = QVBoxLayout(); layout.addLayout(self.body)
        self.progress = QProgressBar(); self.progress.setTextVisible(True); self.progress.hide(); layout.addWidget(self.progress); layout.addStretch(1)

    def clear(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def show_progress(self, message: str) -> None:
        self.progress.setRange(0, 0); self.progress.setFormat(message); self.progress.show()

    def hide_progress(self) -> None:
        self.progress.hide()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.service = PhilonService()
        self.library_worker: WorkThread | None = None
        self.models_worker: WorkThread | None = None
        self.diagnostics_worker: WorkThread | None = None
        self.history_worker: WorkThread | None = None
        self.export_worker: WorkThread | None = None
        self.export_progress: QProgressDialog | None = None
        self.setWindowTitle("Philon — local document workspace")
        self.resize(1540, 960); self.setMinimumSize(1100, 700)
        self._build(); self._menu(); self._refresh_library()

    def closeEvent(self, event: Any) -> None:
        """Close only once nothing is still running in a worker thread."""
        wait_for_workers()
        super().closeEvent(event)

    def _build(self) -> None:
        root = QWidget(); layout = QHBoxLayout(root); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); side = QVBoxLayout(sidebar); side.setContentsMargins(14, 20, 14, 16)
        brand = QLabel("PHILON<br/><span style='font-size:11px;color:#9d998f'>DOCUMENT WORKSPACE</span>"); brand.setTextFormat(Qt.TextFormat.RichText); brand.setObjectName("brand"); side.addWidget(brand); side.addSpacing(20)
        self.nav: dict[str, QPushButton] = {}
        for index, (key, text) in enumerate((("single", "Single Job"), ("batch", "Batch"), ("library", "Library"), ("models", "Models"), ("diagnostics", "Diagnostics"), ("settings", "Settings"))):
            button = QPushButton(text); button.setCheckable(True); button.clicked.connect(lambda _, k=key: self.show_page(k)); side.addWidget(button); self.nav[key] = button
            if index == 1: side.addSpacing(12)
        side.addStretch(1); local = QLabel("● Local-only\nNo cloud providers\nNo automatic model downloads"); local.setObjectName("local"); side.addWidget(local)
        layout.addWidget(sidebar)
        self.stack = QStackedWidget(); layout.addWidget(self.stack, 1); self.setCentralWidget(root)
        self.single = SingleJobPage(self.service); self.batch = BatchPage(self.service)
        self.single.conversion_loaded.connect(lambda _: self._refresh_library()); self.batch.conversion_loaded.connect(lambda _: self._refresh_library())
        self.library = SecondaryPage("Local library", "Completed conversions and their warnings remain in SQLite on this Mac.")
        self.models = SecondaryPage("Model governance", "Readiness diagnostics inspect only declared local paths. They never download or activate a model.")
        self.diagnostics = SecondaryPage("Diagnostics", "This report describes the local runtime, local database, and evidence-preserving actions.")
        self.settings = SecondaryPage("Settings", "Preferences are persisted locally and apply only to future conversions.")
        for page in (self.single, self.batch, self.library, self.models, self.diagnostics, self.settings): self.stack.addWidget(page)
        self.indices = {"single": 0, "batch": 1, "library": 2, "models": 3, "diagnostics": 4, "settings": 5}
        self.show_page("single")

    def _menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        open_file = QAction("Open document…", self, shortcut=QKeySequence.StandardKey.Open); open_file.triggered.connect(self.single.choose_source); file_menu.addAction(open_file)
        add_batch = QAction("Add to batch…", self, shortcut="Ctrl+Shift+O"); add_batch.triggered.connect(lambda: (self.show_page("batch"), self.batch.add_documents())); file_menu.addAction(add_batch)
        export = QAction("Export active conversion…", self, shortcut="Ctrl+E"); export.triggered.connect(self.export_active); file_menu.addAction(export)
        file_menu.addSeparator(); file_menu.addAction(QAction("Quit", self, shortcut=QKeySequence.StandardKey.Quit, triggered=self.close))
        view = self.menuBar().addMenu("View")
        for key, title, shortcut in (("single", "Single Job", "Ctrl+1"), ("batch", "Batch", "Ctrl+2"), ("library", "Library", "Ctrl+3"), ("models", "Models", "Ctrl+4"), ("diagnostics", "Diagnostics", "Ctrl+5"), ("settings", "Settings", "Ctrl+,")):
            action = QAction(title, self, shortcut=shortcut); action.triggered.connect(lambda _, value=key: self.show_page(value)); view.addAction(action)

    def show_page(self, key: str) -> None:
        self.stack.setCurrentIndex(self.indices[key])
        for name, button in self.nav.items(): button.setChecked(name == key)
        if key == "library": self._refresh_library()
        if key == "models": self._refresh_models()
        if key == "diagnostics": self._refresh_diagnostics()
        if key == "settings": self._refresh_settings()

    def _refresh_library(self) -> None:
        self.library.clear()
        self.library.show_progress("Reading locally retained conversion history…")
        self.library.body.addWidget(QLabel("Loading local conversion history…"))
        if self.library_worker:
            return
        self.library_worker = WorkThread(lambda _progress: self.service.store.list_jobs())
        self.library_worker.completed.connect(self._library_loaded); self.library_worker.failed.connect(self._secondary_error); self.library_worker.finished.connect(lambda: setattr(self, "library_worker", None)); self.library_worker.start()

    def _library_loaded(self, jobs: list[dict[str, Any]]) -> None:
        self.library.hide_progress(); self.library.clear()
        if not jobs: self.library.body.addWidget(QLabel("No local conversion history yet.")); return
        for job in jobs:
            button = QPushButton(f"{job['documents']} document(s) · {job['profile']} · {job['warnings']} warning(s)\n{job['created_at']}")
            button.setObjectName("libraryItem"); button.clicked.connect(lambda _, job_id=job["id"]: self.open_history(job_id)); self.library.body.addWidget(button)

    def open_history(self, job_id: str) -> None:
        if self.history_worker:
            return
        self.statusBar().showMessage("Opening locally retained conversion…"); self.library.show_progress("Opening locally retained conversion…")
        self.history_worker = WorkThread(lambda _progress: self.service.store.job_payload(job_id))
        self.history_worker.completed.connect(self._history_loaded); self.history_worker.failed.connect(self._secondary_error); self.history_worker.finished.connect(lambda: setattr(self, "history_worker", None)); self.history_worker.start()

    def _history_loaded(self, payload: dict[str, Any]) -> None:
        self.statusBar().clearMessage(); self.library.hide_progress()
        if payload.get("results"): self.single.load_document(payload["results"][0]); self.show_page("single")

    def _refresh_models(self) -> None:
        self.models.clear()
        self.models.show_progress("Inspecting approved local model packs…")
        self.models.body.addWidget(QLabel("Inspecting approved local model packs…"))
        if self.models_worker:
            return
        self.models_worker = WorkThread(lambda _progress: self.service.models())
        self.models_worker.completed.connect(self._models_loaded); self.models_worker.failed.connect(self._secondary_error); self.models_worker.finished.connect(lambda: setattr(self, "models_worker", None)); self.models_worker.start()

    def _models_loaded(self, reply: dict[str, Any]) -> None:
        self.models.hide_progress(); self.models.clear()
        for pack in reply["packs"]:
            card = QLabel(f"<b>{pack['id']}</b> — {pack['readiness']}<br>{pack['role']}<br><span style='color:#6c6a64'>{pack['runtime']} · {pack['license']}<br>{' '.join(pack.get('diagnostics', []))}</span>")
            card.setObjectName("card"); card.setWordWrap(True); self.models.body.addWidget(card)

    def _refresh_diagnostics(self) -> None:
        self.diagnostics.clear(); self.diagnostics.show_progress("Inspecting the local runtime…"); self.diagnostics.body.addWidget(QLabel("Inspecting the local runtime…"))
        if self.diagnostics_worker:
            return
        self.diagnostics_worker = WorkThread(lambda _progress: self.service.diagnostics())
        self.diagnostics_worker.completed.connect(self._diagnostics_loaded); self.diagnostics_worker.failed.connect(self._secondary_error); self.diagnostics_worker.finished.connect(lambda: setattr(self, "diagnostics_worker", None)); self.diagnostics_worker.start()

    def _diagnostics_loaded(self, reply: dict[str, Any]) -> None:
        self.diagnostics.hide_progress(); self.diagnostics.clear()
        text = QPlainTextEdit(); text.setReadOnly(True); text.setPlainText(json.dumps(reply, indent=2, ensure_ascii=False)); self.diagnostics.body.addWidget(text)

    def _secondary_error(self, message: str) -> None:
        self.statusBar().clearMessage(); self.library.hide_progress(); self.models.hide_progress(); self.diagnostics.hide_progress(); QMessageBox.critical(self, "Philon", message)

    def _refresh_settings(self) -> None:
        self.settings.clear(); values = self.service.preferences(); form_box = QGroupBox("New conversion preferences"); form = QFormLayout(form_box)
        profile = QComboBox(); profile.addItems(PROFILES); profile.setCurrentText(values.get("profile", "Balanced"))
        cache = QComboBox(); cache.addItems(["use", "refresh", "bypass"]); cache.setCurrentText(values.get("cache_policy", "use"))
        form.addRow("Default profile", profile); form.addRow("Cache policy", cache)
        checks: dict[str, QCheckBox] = {}
        labels = {"machine": "Machine-ready folder", "markdown": "Clean reading Markdown", "html": "Presentation HTML", "marker_json": "Marker JSON (compatibility)", "assets": "Source images and previews"}
        for output in OUTPUTS:
            check = QCheckBox(labels.get(output, output.replace("_", " ").title())); check.setChecked(output in values.get("outputs", DEFAULT_OUTPUTS)); checks[output] = check; form.addRow("", check)
        save = QPushButton("Save local preferences"); save.setObjectName("primary")
        def persist() -> None:
            try: self.service.save_preferences({"profile": profile.currentText(), "cache_policy": cache.currentText(), "outputs": [key for key, check in checks.items() if check.isChecked()]}); QMessageBox.information(self, "Philon", "Preferences saved locally.")
            except Exception as exc: QMessageBox.critical(self, "Philon", str(exc))
        save.clicked.connect(persist); form.addRow("", save); self.settings.body.addWidget(form_box)

    def export_active(self) -> None:
        document = self.single.conversion
        if not document or self.export_worker: QMessageBox.information(self, "Philon", "Convert or open a historical document first."); return
        destination = QFileDialog.getExistingDirectory(self, "Export conversion bundle")
        if not destination: return
        self.export_progress = QProgressDialog("Preparing export…", None, 0, 0, self); self.export_progress.setWindowTitle("Philon export"); self.export_progress.setCancelButton(None); self.export_progress.setWindowModality(Qt.WindowModality.WindowModal); self.export_progress.show()
        self.export_worker = WorkThread(lambda progress: self.service.export_conversion(document["outputs"]["ir"], destination, progress))
        self.export_worker.progress.connect(self._update_export_progress); self.export_worker.completed.connect(self._export_finished); self.export_worker.failed.connect(self._secondary_error); self.export_worker.finished.connect(self._export_work_finished); self.export_worker.start()

    def _update_export_progress(self, update: dict[str, Any]) -> None:
        if not self.export_progress:
            return
        if update.get("indeterminate"):
            self.export_progress.setRange(0, 0); self.export_progress.setLabelText(update.get("message", "Exporting locally…")); return
        self.export_progress.setRange(0, 100); self.export_progress.setValue(int(update.get("percent", 0))); self.export_progress.setLabelText(update.get("message", "Exporting locally…"))

    def _export_finished(self, result: dict[str, Any]) -> None:
        QMessageBox.information(self, "Philon", f"Exported {result['files']} files to\n{result['export_path']}")

    def _export_work_finished(self) -> None:
        self.export_worker = None
        if self.export_progress: self.export_progress.close(); self.export_progress = None


STYLESHEET = """
QWidget { background: #f7f5f0; color: #292724; font-family: 'SF Pro Text', 'Helvetica Neue', sans-serif; font-size: 13px; }
QFrame#sidebar { background: #272622; min-width: 178px; max-width: 220px; }
QLabel#brand { color: #f1ede4; font-weight: 700; font-size: 20px; letter-spacing: 2px; }
QPushButton { background: #ece9e2; border: 1px solid #d9d3c9; border-radius: 6px; padding: 8px 10px; text-align: left; }
QPushButton:hover { background: #e2ddd4; } QPushButton:checked { background: #514d45; border-color: #514d45; color: white; }
QPushButton#primary { background: #a84f18; border-color: #a84f18; color: white; font-weight: 650; text-align: center; } QPushButton#primary:hover { background: #8e4011; }
QLabel#local { color: #c7c0b3; padding: 12px; background: #37342e; border-radius: 6px; line-height: 1.5; }
QLabel#heading { font-size: 27px; font-weight: 700; } QLabel#note, QLabel#muted { color: #6c6a64; } QLabel#note { padding-bottom: 12px; }
QPlainTextEdit, QTextEdit, QTreeWidget, QTableWidget { background: #fffdfa; border: 1px solid #d9d3c9; border-radius: 6px; selection-background-color: #dba872; }
QHeaderView::section { background: #ede9e1; border: 0; border-bottom: 1px solid #d9d3c9; padding: 7px; font-weight: 650; }
QLabel#card, QPushButton#libraryItem { background: #fffdfa; border: 1px solid #ded8cf; border-radius: 8px; padding: 12px; text-align: left; } QPushButton#libraryItem { min-height: 46px; }
QGroupBox { border: 1px solid #d9d3c9; border-radius: 8px; margin-top: 12px; padding: 16px; font-weight: 650; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QMenuBar { background: #f0ede7; } QMenuBar::item:selected, QMenu::item:selected { background: #e0d7c9; }
"""


def main() -> None:
    app = QApplication(sys.argv); app.setApplicationName("Philon"); app.setOrganizationName("Philon")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow(); window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
    page_changed = pyqtSignal(int)
