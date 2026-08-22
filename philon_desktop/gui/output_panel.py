"""The Conversion panel: rendered preview, Markdown/IR exports, copy actions."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from . import theme
from .widgets import Segmented, clear_layout, eyebrow, hbox, make_button, transparent, vbox
from .qt import (
    QApplication,
    QDesktopServices,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QUrl,
    QWidget,
    Qt,
    Signal,
)

PLACEHOLDER = "# Evidence before output\n\nPhilon turns source structure into readable exports and keeps every uncertain region visible."
_DIVIDER_CELL = re.compile(r"^:?-{3,}:?$")


def table_rows(text: str) -> list[list[str]]:
    rows = [
        [cell.strip() for cell in line.split("|")[1:-1]]
        for line in (line.strip() for line in text.split("\n"))
        if line.startswith("|")
    ]
    return [row for index, row in enumerate(rows) if index != 1 or not all(_DIVIDER_CELL.match(cell) for cell in row)]


def markdown_body(document: dict[str, Any] | None) -> str:
    if not document:
        return PLACEHOLDER
    parts = []
    for block in document.get("blocks", []):
        kind, text = block.get("type"), block.get("text", "")
        if kind == "heading":
            parts.append("#" * (block.get("level") or 2) + " " + text)
        elif kind == "formula":
            parts.append(f"```text\n{text}\n```")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def ir_body(document: dict[str, Any]) -> str:
    return json.dumps({"pages": document.get("pages", []), "blocks": document.get("blocks", [])}, indent=2, ensure_ascii=False)


class BlockView(QLabel):
    """One rendered block: click to inspect, highlighted when selected."""

    clicked = Signal(str)

    def __init__(self, block_id: str) -> None:
        super().__init__()
        self.block_id = block_id
        self.selected = False
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self._restyle(hover=False)

    def enterEvent(self, event: Any) -> None:
        self._restyle(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._restyle(hover=False)
        super().leaveEvent(event)

    def _restyle(self, hover: bool) -> None:
        if self.selected:
            style = "background: rgba(15,122,85,.105); border: 2px solid rgba(15,122,85,.42); border-radius: 4px;"
        elif hover:
            style = "background: rgba(15,122,85,.055); border: 2px solid transparent; border-radius: 4px;"
        else:
            style = "background: transparent; border: 2px solid transparent; border-radius: 4px;"
        self.setStyleSheet(f"QLabel {{ color: #1d1d1f; {style} }}")

    def mousePressEvent(self, event: Any) -> None:
        self.clicked.emit(self.block_id)
        super().mousePressEvent(event)


class RenderedDocument(QFrame):
    """The white reading page: serif body, sans headings, native tables."""

    block_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: #ffffff;")
        self.setMaximumWidth(780)
        self.blocks: dict[str, BlockView] = {}
        self.layout_box = vbox(self, (56, 44, 56, 72), 14)
        theme.drop_shadow(self, 16, 3, "rgba(0,0,0,.09)")

    def render_document(self, document: dict[str, Any], selected_block_id: str | None) -> None:
        clear_layout(self.layout_box)
        self.blocks = {}
        for block in document.get("blocks", []):
            view = BlockView(str(block.get("id")))
            kind, text = block.get("type"), block.get("text", "")
            if kind == "heading":
                level = min(4, max(1, block.get("level") or 2))
                size = {1: 28, 2: 21, 3: 17, 4: 15}[level]
                theme.font(view, size, 700, -0.5)
                view.setText(text)
            elif kind == "table":
                rows = table_rows(text)
                if rows:
                    header = "".join(f"<th style='padding:7px 8px; background:#f5f5f7; text-align:left;'>{html.escape(cell)}</th>" for cell in rows[0])
                    body = "".join(
                        "<tr>" + "".join(f"<td style='padding:7px 8px; border-bottom:1px solid #d2d2d7;'>{html.escape(cell)}</td>" for cell in row) + "</tr>"
                        for row in rows[1:]
                    )
                    theme.font(view, 13)
                    view.setText(f"<table width='100%' cellspacing='0'><tr>{header}</tr>{body}</table>")
                else:
                    theme.serif_font(view, 16)
                    view.setText(text)
            elif kind == "formula":
                theme.mono_font(view, 13)
                view.setStyleSheet("background: #f5f5f7; border-radius: 4px; padding: 12px; color: #1d1d1f;")
                view.setText(text)
            elif kind == "caption":
                theme.serif_font(view, 13)
                view.setText(f"<span style='color:#6e6e73'>{html.escape(text)}</span>")
            else:
                theme.serif_font(view, 16)
                view.setText(text)
            view.clicked.connect(self.block_selected.emit)
            view.set_selected(str(block.get("id")) == selected_block_id)
            self.blocks[str(block.get("id"))] = view
            self.layout_box.addWidget(view)
        assets = (document.get("outputs", {}).get("extracted_assets") or {}).get("items") or []
        if assets:
            title = theme.label("Extracted source images", size=21, weight=700, color="#171719")
            self.layout_box.addSpacing(22)
            self.layout_box.addWidget(title)
            for asset in assets:
                figure = QLabel()
                figure.setAlignment(Qt.AlignmentFlag.AlignCenter)
                path = str(asset.get("path", ""))
                if Path(path).exists():
                    figure.setText(f"<img src='{html.escape(path)}' width='560'/>")
                size = f"{asset.get('pixel_width')} × {asset.get('pixel_height')}px" if asset.get("pixel_width") else str(asset.get("format") or "image")
                caption = theme.label(f"Native asset · page {', '.join(str(page) for page in asset.get('source_pages', []))} · {size}", size=11, color="#6e6e73")
                caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.layout_box.addWidget(figure)
                self.layout_box.addWidget(caption)
        self.layout_box.addStretch(1)

    def select_block(self, block_id: str | None) -> None:
        for value, view in self.blocks.items():
            view.set_selected(value == block_id)


class OutputPanel(QFrame):
    """Header with the format switcher, the content stack, and the footer."""

    block_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("OutputPanel")
        self.document: dict[str, Any] | None = None
        self.selected_block_id: str | None = None
        self.format = "Preview"
        t = theme.tokens()
        layout = vbox(self, (14, 14, 14, 14), 12)

        header = hbox(spacing=12)
        heading = vbox(spacing=5)
        heading.addWidget(eyebrow("Conversion", "BookOpenText", icon_color=t["text_tertiary"]))
        self.title = theme.label("Readable output", size=14, weight=650, color=t["text"])
        heading.addWidget(self.title)
        header.addLayout(heading, 1)
        self.switcher = Segmented([("Preview", None), ("Markdown", None), ("IR", None)], padding=3, font_size=10, font_weight=650, button_padding="5px 7px")
        self.switcher.select("Preview", announce=False)
        self.switcher.changed.connect(self.set_format)
        header.addWidget(self.switcher, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.stack = QStackedWidget()
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("OutputPreviewScroll")
        self.preview_scroll.setWidgetResizable(True)
        preview_host = transparent(QWidget())
        host_layout = hbox(preview_host, (16, 20, 16, 20), 0)
        self.rendered = RenderedDocument()
        self.rendered.block_selected.connect(self.block_selected.emit)
        host_layout.addStretch(1)
        host_layout.addWidget(self.rendered)
        host_layout.addStretch(1)
        self.preview_scroll.setWidget(preview_host)
        self.stack.addWidget(self.preview_scroll)

        self.text_output = QPlainTextEdit()
        self.text_output.setObjectName("OutputContent")
        self.text_output.setReadOnly(True)
        self.text_output.setFrameShape(QFrame.Shape.NoFrame)
        theme.mono_font(self.text_output, 12)
        self.text_output.setStyleSheet(self.text_output.styleSheet() + "QPlainTextEdit { padding: 12px; }")
        self.stack.addWidget(self.text_output)
        layout.addWidget(self.stack, 1)

        footer = hbox(spacing=8)
        self.footer_note = theme.label("Outputs include Markdown, HTML, IR, chunks, and evidence", size=11, color=t["text_tertiary"])
        footer.addWidget(self.footer_note, 1)
        self.marker_link = make_button("Marker JSON", "ExportLink", font_size=11, font_weight=600)
        self.marker_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.marker_link.clicked.connect(self._open_marker_json)
        self.marker_link.hide()
        footer.addWidget(self.marker_link)
        self.copy_button = make_button("Copy Markdown", "CopyButton", "ClipboardText", 16, t["accent_text"], font_size=11, font_weight=650)
        self.copy_button.clicked.connect(self._copy_body)
        self.copy_button.hide()
        footer.addWidget(self.copy_button)
        layout.addLayout(footer)
        self._refresh()

    # -- state -------------------------------------------------------------
    def set_document(self, document: dict[str, Any] | None, selected_block_id: str | None) -> None:
        rerender = document is not self.document or self.document is None
        self.document, self.selected_block_id = document, selected_block_id
        if rerender:
            self._refresh()
        else:
            self.rendered.select_block(selected_block_id)

    def set_format(self, value: str) -> None:
        self.format = value
        self._refresh()

    def _body(self) -> str:
        if not self.document:
            return PLACEHOLDER
        return ir_body(self.document) if self.format == "IR" else markdown_body(self.document)

    def _refresh(self) -> None:
        self.title.setText("Readable output" if self.format == "Preview" else f"{self.format} export")
        if self.document and self.format == "Preview":
            self.rendered.render_document(self.document, self.selected_block_id)
            self.stack.setCurrentWidget(self.preview_scroll)
        else:
            self.text_output.setPlainText(self._body())
            self.stack.setCurrentWidget(self.text_output)
        blocks = len(self.document.get("blocks", [])) if self.document else 0
        self.footer_note.setText(f"{blocks} evidence-linked blocks" if self.document else "Outputs include Markdown, HTML, IR, chunks, and evidence")
        self.copy_button.setVisible(bool(self.document))
        self.copy_button.setText(" Copy " + ("Markdown" if self.format == "Preview" else self.format))
        self.marker_link.setVisible(bool(self.document and self.document.get("outputs", {}).get("marker_json")))

    def _copy_body(self) -> None:
        QApplication.clipboard().setText(self._body())

    def _open_marker_json(self) -> None:
        path = (self.document or {}).get("outputs", {}).get("marker_json")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
