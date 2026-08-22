"""The Batch workspace: queue panel with item controls, and the batch report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import theme
from .phosphor import Icon, pixmap
from .widgets import clear_layout, eyebrow, hbox, make_button, transparent, vbox
from .qt import (
    QDesktopServices,
    QSizePolicy,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSize,
    QUrl,
    QWidget,
    Qt,
    Signal,
)


def bytes_label(count: int) -> str:
    if count < 1024 * 1024:
        return f"{max(1, round(count / 1024))} KB"
    return f"{count / (1024 * 1024):.1f} MB"


def summarize(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "completed": sum(item["status"] in ("completed", "completed_with_warnings") for item in items),
        "active": sum(item["status"] == "running" for item in items),
        "waiting": sum(item["status"] in ("queued", "paused") for item in items),
        "failed": sum(item["status"] == "failed" for item in items),
    }


class BatchView(QWidget):
    """Everything below the command bar when the Batch job tab is active."""

    add_requested = Signal()
    clear_requested = Signal()
    item_state_requested = Signal(str, str)
    export_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []
        self.preflight: dict[str, dict[str, Any]] = {}
        self.result: dict[str, Any] | None = None
        t = theme.tokens()
        layout = vbox(self, spacing=0)

        toolbar = hbox(spacing=12)
        self.toolbar_note = theme.label("No documents queued", size=12, color=t["text_secondary"])
        toolbar.addWidget(self.toolbar_note)
        toolbar.addStretch(1)
        add = make_button("Add documents", "SecondaryButton", "FolderOpen", 17)
        add.clicked.connect(self.add_requested.emit)
        toolbar.addWidget(add)
        layout.addLayout(toolbar)
        layout.addSpacing(12)

        columns = hbox(spacing=16)
        self.queue_panel = QFrame()
        self.queue_panel.setObjectName("QueuePanel")
        theme.drop_shadow(self.queue_panel, 24, 8, "rgba(0,0,0,.06)")
        self.queue_layout = vbox(self.queue_panel, (21, 21, 21, 21), 0)
        columns.addWidget(self.queue_panel, 115)

        self.report_panel = QFrame()
        self.report_panel.setObjectName("BatchReport")
        theme.drop_shadow(self.report_panel, 24, 8, "rgba(0,0,0,.06)")
        self.report_layout = vbox(self.report_panel, (21, 21, 21, 21), 0)
        columns.addWidget(self.report_panel, 85)
        layout.addLayout(columns, 1)
        self.refresh()

    # -- state -------------------------------------------------------------
    def set_state(self, items: list[dict[str, Any]], preflight: dict[str, dict[str, Any]], result: dict[str, Any] | None) -> None:
        self.items, self.preflight, self.result = items, preflight, result
        self.refresh()

    def pending_paths(self) -> list[str]:
        return [item["source_path"] for item in self.items if item["status"] not in ("cancelled", "completed", "completed_with_warnings")]

    def refresh(self) -> None:
        t = theme.tokens()
        summary = summarize(self.items)
        self.toolbar_note.setText(
            f"{summary['completed']} complete · {summary['waiting']} waiting" if summary["total"] else "No documents queued"
        )
        self._clear(self.queue_layout)
        header = hbox(spacing=10)
        header_text = vbox(spacing=5)
        header_text.addWidget(eyebrow("Queue", "ListChecks", icon_color=t["text_tertiary"]))
        header_text.addWidget(theme.label(
            f"{summary['completed']} of {summary['total']} complete" if summary["total"] else "No documents queued",
            size=16, weight=650, color=t["text"], letter_spacing=-0.3))
        header.addLayout(header_text, 1)
        clear = make_button("Clear pending", "TextButton", font_size=12, font_weight=500)
        clear.setEnabled(bool(self.pending_paths()))
        clear.clicked.connect(self.clear_requested.emit)
        header.addWidget(clear, 0, Qt.AlignmentFlag.AlignTop)
        self.queue_layout.addLayout(header)
        self.queue_layout.addSpacing(17)
        if self.items:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            content = transparent(QWidget())
            list_layout = vbox(content, spacing=7)
            for item in self.items:
                list_layout.addWidget(self._queue_item(item))
            list_layout.addStretch(1)
            scroll.setWidget(content)
            self.queue_layout.addWidget(scroll, 1)
        else:
            empty = QPushButton()
            empty.setObjectName("QueueEmpty")
            empty.setCursor(Qt.CursorShape.PointingHandCursor)
            empty.setMinimumHeight(330)
            empty.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            empty_layout = vbox(empty, (30, 30, 30, 30), 11)
            empty_layout.addStretch(1)
            icon = Icon("FileArrowUp", 28, t["accent_text"])
            empty_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
            note = theme.label("Add PDFs or images to start a batch.", size=13, color=t["text_secondary"])
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(note)
            empty_layout.addStretch(1)
            empty.clicked.connect(self.add_requested.emit)
            self.queue_layout.addWidget(empty, 1)

        self._clear(self.report_layout)
        report_header = vbox(spacing=5)
        report_header.addWidget(eyebrow("Latest batch", "ClipboardText", icon_color=t["text_tertiary"]))
        report_header.addWidget(theme.label(
            f"{len(self.result['results'])} completed, {len(self.result['failures'])} failed" if self.result else "Awaiting a batch",
            size=16, weight=650, color=t["text"], letter_spacing=-0.3))
        self.report_layout.addLayout(report_header)
        self.report_layout.addSpacing(17)
        if self.result:
            metrics = hbox(spacing=8)
            pages = sum(len(item.get("pages", [])) for item in self.result["results"])
            warnings = sum(len(item.get("warnings", [])) for item in self.result["results"])
            cache_hits = sum(bool(item.get("cache_hit")) for item in self.result["results"])
            for value, caption in ((pages, "Pages processed"), (warnings, "Warnings retained"), (cache_hits, "Cache hits")):
                card = QFrame()
                card.setObjectName("MetricCard")
                card_layout = vbox(card, (11, 15, 11, 15), 4)
                card_layout.addWidget(theme.label(str(value), size=26, weight=580, color=t["accent_text"], letter_spacing=-1.0))
                caption_label = theme.label(caption, size=10, color=t["text_secondary"])
                caption_label.setWordWrap(True)
                card_layout.addWidget(caption_label)
                metrics.addWidget(card, 1)
            self.report_layout.addLayout(metrics)
            self.report_layout.addSpacing(16)
            export_row = hbox()
            export = make_button("Export completed conversions…", "SecondaryButton", "DownloadSimple", 16)
            export.setEnabled(bool(self.result["results"]))
            export.clicked.connect(self.export_requested.emit)
            export_row.addWidget(export)
            export_row.addStretch(1)
            self.report_layout.addLayout(export_row)
            self.report_layout.addSpacing(6)
            links_scroll = QScrollArea()
            links_scroll.setWidgetResizable(True)
            links_scroll.setFrameShape(QFrame.Shape.NoFrame)
            links_host = transparent(QWidget())
            links_layout = vbox(links_host, spacing=7)
            for item in self.result["results"]:
                evidence = item.get("outputs", {}).get("evidence")
                link = make_button(f"{Path(item.get('source_path', '')).name} evidence report", "ExportListLink", "DownloadSimple", 16, t["export_link_text"], font_size=12, font_weight=400)
                if evidence:
                    link.clicked.connect(lambda _=False, value=str(evidence): QDesktopServices.openUrl(QUrl.fromLocalFile(value)))
                links_layout.addWidget(link)
            links_layout.addStretch(1)
            links_scroll.setWidget(links_host)
            self.report_layout.addWidget(links_scroll, 1)
        else:
            empty = QFrame()
            empty.setObjectName("ReportEmpty")
            empty.setMinimumHeight(350)
            empty_layout = vbox(empty, (30, 30, 30, 30), 11)
            empty_layout.addStretch(1)
            empty_layout.addWidget(Icon("ShieldCheck", 28, t["accent_text"], "thin"), 0, Qt.AlignmentFlag.AlignHCenter)
            note = theme.label("Results will show source counts, cache use, and review warnings here.", size=13, color=t["text_secondary"])
            note.setWordWrap(True)
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(note)
            empty_layout.addStretch(1)
            self.report_layout.addWidget(empty, 1)

    def _queue_item(self, item: dict[str, Any]) -> QFrame:
        t = theme.tokens()
        path = item["source_path"]
        status = item["status"]
        row = QFrame()
        row.setObjectName("QueueItem")
        layout = hbox(row, (12, 12, 12, 12), 10)
        layout.addWidget(Icon("FilePdf", 20, t["accent_text"], "duotone"))
        body = vbox(spacing=3)
        body.addWidget(theme.label(Path(path).name, size=12, weight=600, color=t["text"]))
        inspected = self.preflight.get(path)
        state = status.replace("_", " ")
        if inspected and inspected.get("status") == "blocked":
            detail = str(inspected.get("error", ""))
        elif inspected and inspected.get("preflight"):
            preflight = inspected["preflight"]
            pages = preflight.get("declared_page_count")
            detail = f"{str(preflight.get('kind', '')).upper()} · {pages if pages is not None else '?'} page(s) · {bytes_label(int(preflight.get('bytes', 0)))} · {state}"
        else:
            detail = state
        if status == "running":
            detail += " — finishing this document before pausing or cancelling"
        detail_label = theme.label(detail, size=10, color=t["text_secondary"])
        body.addWidget(detail_label)
        layout.addLayout(body, 1)
        controls = hbox(spacing=4)
        if status == "queued":
            controls.addWidget(self._control("Pause", lambda: self.item_state_requested.emit(path, "paused")))
        if status == "running":
            controls.addWidget(self._control("Pause after", lambda: self.item_state_requested.emit(path, "paused")))
        if status in ("paused", "cancelled", "failed"):
            controls.addWidget(self._control("Retry", lambda: self.item_state_requested.emit(path, "queued")))
        if status not in ("completed", "completed_with_warnings"):
            remove = self._control("", lambda: self.item_state_requested.emit(path, "cancelled"))
            remove.setIcon(pixmap("X", 16, t["text_tertiary"]))
            remove.setIconSize(QSize(16, 16))
            controls.addWidget(remove)
        layout.addLayout(controls)
        return row

    def _control(self, text: str, on_click: Any) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("queueControl", "true")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.font(button, 10)
        button.clicked.connect(on_click)
        return button

    def _clear(self, layout: Any) -> None:
        clear_layout(layout)
