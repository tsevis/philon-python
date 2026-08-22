"""The Source panel: fitted page raster, measured region overlay, zoom, pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import theme
from .phosphor import Icon, pixmap
from .widgets import ElidedLabel, eyebrow, hbox, vbox
from .qt import (
    QFrame,
    QLabel,
    QPainter,
    QPixmap,
    QPushButton,
    QScrollArea,
    QSize,
    QSizePolicy,
    QWidget,
    Qt,
    Signal,
)

ZOOM_STEPS = (0.75, 1.0, 1.25, 1.5)


class PageCanvas(QWidget):
    """One white page: the raster stretched to the canvas, region on top."""

    def __init__(self) -> None:
        super().__init__()
        self.page_pixmap: QPixmap | None = None
        self.region: tuple[float, float, float, float] | None = None

    def paintEvent(self, event: Any) -> None:
        t = theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), theme.qcolor("#ffffff"))
        if self.page_pixmap and not self.page_pixmap.isNull():
            painter.drawPixmap(self.rect(), self.page_pixmap)
        if self.region:
            left, top, width, height = self.region
            x = round(left * self.width())
            y = round(top * self.height())
            w = max(2, round(width * self.width()))
            h = max(2, round(height * self.height()))
            painter.fillRect(x, y, w, h, theme.qcolor("rgba(255,149,0,.16)"))
            pen = painter.pen()
            pen.setColor(theme.qcolor(t["orange"]))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(x + 1, y + 1, w - 2, h - 2)
            pen.setColor(theme.qcolor("#ffffff"))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRect(x - 1, y - 1, w + 1, h + 1)
        painter.end()


class SourcePanel(QFrame):
    """Everything the source column shows, from header to footer controls."""

    page_selected = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SourcePanel")
        self.path: str | None = None
        self.document: dict[str, Any] | None = None
        self.selected_block: dict[str, Any] | None = None
        self.zoom: float | str = "fit"
        t = theme.tokens()
        layout = vbox(self, (14, 14, 14, 14), 12)

        header = vbox(spacing=0)
        header.addWidget(eyebrow("Source", "FilePdf", icon_color=t["text_tertiary"]))
        self.title = ElidedLabel("No source selected")
        theme.font(self.title, 14, 650)
        self.title.setStyleSheet(f"color: {t['text']}; background: transparent;")
        header.addSpacing(5)
        header.addWidget(self.title)
        layout.addLayout(header)

        paper = QFrame()
        paper.setObjectName("PaperPreview")
        paper_layout = vbox(paper, (10, 10, 10, 10), 0)
        meta = hbox(spacing=8)
        self.meta_left = theme.label("LOCAL SOURCE", size=10, weight=400, color=t["text_tertiary"], letter_spacing=0.3)
        self.meta_right = theme.label("AWAITING FILE", size=10, weight=400, color=t["text_tertiary"], letter_spacing=0.3)
        meta.addWidget(self.meta_left)
        meta.addStretch(1)
        meta.addWidget(self.meta_right)
        paper_layout.addLayout(meta)
        paper_layout.addSpacing(8)

        self.canvas_frame = QScrollArea()
        self.canvas_frame.setObjectName("SourceCanvasFrame")
        self.canvas_frame.setWidgetResizable(False)
        self.canvas_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas_frame.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas = PageCanvas()
        self.canvas_frame.setWidget(self.canvas)
        paper_layout.addWidget(self.canvas_frame, 1)

        self.paper_text = QLabel("No source loaded.")
        self.paper_text.setWordWrap(True)
        self.paper_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        theme.serif_font(self.paper_text, 13)
        self.paper_text.setStyleSheet("color: #29342f; background: #ffffff; padding: 13px;")
        self.paper_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        paper_layout.addWidget(self.paper_text, 1)

        overlay = hbox(spacing=8)
        self.overlay_left = theme.label("", size=10, weight=400, color=t["text_tertiary"], letter_spacing=0.3)
        self.overlay_right = theme.label("", size=10, weight=400, color=t["text_tertiary"], letter_spacing=0.3)
        overlay.addWidget(self.overlay_left)
        overlay.addStretch(1)
        overlay.addWidget(self.overlay_right)
        paper_layout.addSpacing(7)
        paper_layout.addLayout(overlay)
        layout.addWidget(paper, 1)

        footer = hbox(spacing=9)
        session = hbox(spacing=5)
        session.addWidget(Icon("CloudSlash", 15, t["green"]))
        session.addWidget(theme.label("Local-only session", size=11, color=t["text_tertiary"]))
        footer.addLayout(session)
        footer.addStretch(1)

        self.zoom_controls = QFrame()
        self.zoom_controls.setObjectName("MiniControls")
        zoom_layout = hbox(self.zoom_controls, (2, 2, 2, 2), 2)
        self.fit_button = self._mini_button("Fit", lambda: self.set_zoom("fit"))
        self.fit_button.setCheckable(True)
        zoom_layout.addWidget(self.fit_button)
        zoom_layout.addWidget(self._mini_button("−", self.zoom_out))
        self.zoom_label = theme.label("Fit", size=10, color=t["text_secondary"])
        self.zoom_label.setMinimumWidth(30)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(self._mini_button("+", self.zoom_in))
        self.hundred_button = self._mini_button("100%", lambda: self.set_zoom(1.0))
        self.hundred_button.setCheckable(True)
        zoom_layout.addWidget(self.hundred_button)
        footer.addWidget(self.zoom_controls)
        self.zoom_controls.hide()

        self.page_controls = QFrame()
        self.page_controls.setObjectName("MiniControls")
        page_layout = hbox(self.page_controls, (2, 2, 2, 2), 5)
        self.previous_button = self._mini_button("", lambda: self.page_selected.emit(self.current_page() - 1))
        self.previous_button.setIcon(pixmap("CaretLeft", 14, t["accent_text"]))
        self.previous_button.setIconSize(QSize(14, 14))
        page_layout.addWidget(self.previous_button)
        self.page_label = theme.label("Page 1 of 1", size=10, color=t["text_secondary"])
        self.page_label.setMinimumWidth(76)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_layout.addWidget(self.page_label)
        self.next_button = self._mini_button("", lambda: self.page_selected.emit(self.current_page() + 1))
        self.next_button.setIcon(pixmap("CaretRight", 14, t["accent_text"]))
        self.next_button.setIconSize(QSize(14, 14))
        page_layout.addWidget(self.next_button)
        footer.addWidget(self.page_controls)
        self.page_controls.hide()

        self.footer_note = theme.label("PDF and image files only", size=11, color=t["text_tertiary"])
        footer.addWidget(self.footer_note)
        layout.addLayout(footer)

    def _mini_button(self, text: str, on_click: Any) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("miniControl", "true")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.font(button, 10, 650)
        button.setMinimumWidth(23)
        button.clicked.connect(on_click)
        return button

    # -- state -------------------------------------------------------------
    def set_source(self, path: str | None, document: dict[str, Any] | None, selected_block: dict[str, Any] | None) -> None:
        page_turn = self.document is document and document is not None
        self.path, self.document, self.selected_block = path, document, selected_block
        if not page_turn:
            self.zoom = "fit"
        self.title.setText(Path(path).name if path else "No source selected")
        pages = document.get("pages", []) if document else []
        self.meta_right.setText((f"{len(pages)} PAGE" + ("" if len(pages) == 1 else "S")) if document else "AWAITING FILE")
        self._refresh_preview()
        self._refresh_footer()

    def current_page(self) -> int:
        if not self.document:
            return 1
        block = self.selected_block or (self.document.get("blocks") or [{}])[0]
        page_id = str(block.get("page", "page-1"))
        try:
            return int(page_id.rsplit("-", 1)[-1])
        except ValueError:
            return 1

    def _page_record(self) -> dict[str, Any]:
        pages = self.document.get("pages", []) if self.document else []
        return next((page for page in pages if page.get("number") == self.current_page()), pages[0] if pages else {})

    def _asset_path(self) -> Path | None:
        if not self.document:
            return None
        assets = self.document.get("outputs", {}).get("assets") or []
        number = self.current_page()
        if isinstance(assets, list) and len(assets) >= number:
            candidate = Path(str(assets[number - 1]))
            if candidate.exists():
                return candidate
        return None

    def _region_fraction(self) -> tuple[float, float, float, float] | None:
        block = self.selected_block
        page = self._page_record()
        bbox = block.get("bbox") if block else None
        if not bbox or str(block.get("page", "")) != str(page.get("id", "")):
            return None
        normalized = bbox.get("coordinate_space") == "normalized-image"
        width = 1.0 if normalized else float(page.get("width") or 1)
        height = 1.0 if normalized else float(page.get("height") or 1)
        x0, y0, x1, y1 = (float(bbox[key]) for key in ("x0", "y0", "x1", "y1"))
        return (x0 / width, (height - y1) / height, (x1 - x0) / width, (y1 - y0) / height)

    def _refresh_preview(self) -> None:
        asset = self._asset_path()
        if asset:
            self.paper_text.hide()
            self.canvas_frame.show()
            self.canvas.page_pixmap = QPixmap(str(asset))
            self.canvas.region = self._region_fraction()
            self._layout_canvas()
        else:
            self.canvas_frame.hide()
            self.paper_text.show()
            blocks = (self.document or {}).get("blocks") or []
            page_id = f"page-{self.current_page()}"
            page_blocks = [block for block in blocks if str(block.get("page")) == page_id] or blocks
            lines = "\n\n".join(block.get("text", "") for block in page_blocks)
            self.paper_text.setText(lines.split("\n\n", 1)[0][:400] + ("\n\n" + "\n".join(lines.split("\n")[1:22]) if "\n" in lines else "") if lines else "No source loaded.")
        t = theme.tokens()
        if self.document:
            region = self._region_fraction()
            page = self._page_record()
            page_number = str(page.get("id", "page-1")).rsplit("-", 1)[-1]
            space = str((self.selected_block or {}).get("bbox", {}).get("coordinate_space", "")).replace("-", " ")
            label = "Cache evidence reused" if self.document.get("cache_hit") else (f"Measured p{page_number} · {space}" if region else "No measured source region")
            self.overlay_left.setText(label.upper())
            block = self.selected_block or ((self.document.get("blocks") or [None])[0])
            confidence = round(float(block.get("source", {}).get("confidence", 0)) * 100) if block else 0
            self.overlay_right.setText(f"{confidence}% BLOCK CONFIDENCE" if block else "")
        else:
            self.overlay_left.setText("")
            self.overlay_right.setText("")

    def _refresh_footer(self) -> None:
        pages = (self.document or {}).get("pages") or []
        has_raster = self._asset_path() is not None
        self.zoom_controls.setVisible(has_raster)
        multi = len(pages) > 1
        self.page_controls.setVisible(bool(self.document) and multi)
        self.footer_note.setVisible(not (self.document and multi))
        self.footer_note.setText("Original never altered" if self.path else "PDF and image files only")
        if multi:
            number = self.current_page()
            self.page_label.setText(f"Page {number} of {len(pages)}")
            self.previous_button.setEnabled(number > 1)
            self.next_button.setEnabled(number < len(pages))
        self.zoom_label.setText("Fit" if self.zoom == "fit" else f"{round(float(self.zoom) * 100)}%")
        self.fit_button.setChecked(self.zoom == "fit")
        self.hundred_button.setChecked(self.zoom == 1.0)

    # -- zoom --------------------------------------------------------------
    def set_zoom(self, zoom: float | str) -> None:
        self.zoom = zoom
        self._layout_canvas()
        self._refresh_footer()

    def zoom_out(self) -> None:
        self.set_zoom(0.75 if self.zoom == "fit" else max(0.75, float(self.zoom) - 0.25))

    def zoom_in(self) -> None:
        self.set_zoom(1.0 if self.zoom == "fit" else min(1.5, float(self.zoom) + 0.25))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._layout_canvas()

    def _layout_canvas(self) -> None:
        source = self.canvas.page_pixmap
        if not source or source.isNull() or not self.canvas_frame.isVisible():
            return
        ratio = source.width() / max(1, source.height())
        if self.zoom == "fit":
            available = self.canvas_frame.viewport().size()
            width = min(max(1, available.width() - 20), max(1, available.height() - 20) * ratio)
            self.canvas.setFixedSize(round(width), round(width / ratio))
        else:
            factor = float(self.zoom)
            self.canvas.setFixedSize(round(source.width() / source.devicePixelRatio() * factor), round(source.height() / source.devicePixelRatio() * factor))
        self.canvas.update()
