"""The Evidence panel: source method, warnings, candidates, review, repair."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import theme
from .phosphor import Icon, pixmap
from .widgets import ElidedLabel, count_pill, eyebrow, hbox, make_button, transparent, vbox
from .qt import (
    QDesktopServices,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSize,
    QSizePolicy,
    QToolButton,
    QUrl,
    QWidget,
    Qt,
    Signal,
)


def confidence_label(value: float) -> str:
    if value >= 0.9:
        return "High confidence"
    if value >= 0.75:
        return "Review suggested"
    return "Needs review"


def changed_token_count(before: str, after: str) -> int:
    before_tokens = before.lower().split()
    after_tokens = after.lower().split()
    counts: dict[str, int] = {}
    for token in before_tokens:
        counts[token] = counts.get(token, 0) + 1
    shared = 0
    for token in after_tokens:
        if counts.get(token):
            shared += 1
            counts[token] -= 1
    return max(len(before_tokens), len(after_tokens)) - shared


def link_summary(links: list[dict[str, Any]]) -> str:
    """Say what the PDF's own link annotations came to on this block.

    A target the exports will not make clickable is still reported. It is
    something the source declared, and saying nothing about it would hide a
    decision Philon made rather than record it.
    """
    if not links:
        return "None declared"
    anchorable = [link for link in links if str(link.get("uri", "")).strip().lower().startswith(("http://", "https://", "mailto:"))]
    withheld = len(links) - len(anchorable)
    if not withheld:
        return f"{len(anchorable)} anchored"
    if not anchorable:
        return f"{withheld} declared, none an anchorable scheme"
    return f"{len(anchorable)} anchored, {withheld} withheld as unanchorable"


def candidates_of(block: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (block or {}).get("evidence", {}).get("alternatives")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and isinstance(item.get("text"), str)]


class EvidencePanel(QFrame):
    """The right column, rebuilt from state exactly as the source panel is."""

    block_chosen = Signal(str)
    review_requested = Signal(str, str)
    candidate_restored = Signal(str, int)
    repair_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("EvidencePanelSurface")
        self.document: dict[str, Any] | None = None
        self.selected_block_id: str | None = None
        self.compared_candidate: int | None = None
        outer = vbox(self, (0, 0, 0, 0), 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("EvidenceScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self._rebuild()

    # -- state -------------------------------------------------------------
    def set_document(self, document: dict[str, Any] | None, selected_block_id: str | None) -> None:
        if document is not self.document:
            self.compared_candidate = None
        if selected_block_id != self.selected_block_id:
            self.compared_candidate = None
        self.document, self.selected_block_id = document, selected_block_id
        self._rebuild()

    def _selected_block(self) -> dict[str, Any] | None:
        blocks = (self.document or {}).get("blocks") or []
        return next((block for block in blocks if str(block.get("id")) == self.selected_block_id), blocks[0] if blocks else None)

    # -- construction ------------------------------------------------------
    def _rebuild(self) -> None:
        t = theme.tokens()
        content = transparent(QWidget())
        layout = vbox(content, (14, 14, 14, 14), 0)
        if not self.document:
            layout.addStretch(1)
            layout.addWidget(eyebrow("Evidence", "ShieldCheck", icon_color=t["text_tertiary"]))
            layout.addSpacing(9)
            layout.addWidget(theme.label("Nothing hidden.", size=20, weight=650, color=t["text"], letter_spacing=-0.6))
            layout.addSpacing(6)
            description = theme.label("Every converted block will show its source, confidence, and validation record here.", size=13, color=t["text_secondary"])
            description.setWordWrap(True)
            layout.addWidget(description)
            layout.addStretch(2)
            self.scroll.setWidget(content)
            return

        document = self.document
        block = self._selected_block()
        warnings = document.get("warnings", [])
        heading = hbox(spacing=12)
        heading_text = vbox(spacing=5)
        heading_text.addWidget(eyebrow("Evidence", "ShieldCheck", icon_color=t["text_tertiary"]))
        heading_text.addWidget(theme.label("Review queue" if warnings else "Verified output", size=14, weight=650, color=t["text"]))
        heading.addLayout(heading_text, 1)
        mark = QLabel()
        mark.setFixedSize(25, 25)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if warnings:
            mark.setText(str(len(warnings)))
            theme.font(mark, 12, 750)
            mark.setStyleSheet(f"background: {t['orange']}; color: #ffffff; border-radius: 12px;")
        else:
            mark.setPixmap(pixmap("CheckCircle", 16, "#ffffff", "fill"))
            mark.setStyleSheet(f"background: {t['green']}; border-radius: 12px;")
        heading.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading)
        layout.addSpacing(8)
        layout.addWidget(self._summary(document, block))

        assets = (document.get("outputs", {}).get("extracted_assets") or {}).get("items") or []
        if assets:
            layout.addSpacing(14)
            layout.addWidget(self._asset_shelf(assets))

        layout.addSpacing(13)
        if warnings:
            warning_list = vbox(spacing=8)
            for warning in warnings:
                warning_list.addWidget(self._warning_item(warning))
            layout.addLayout(warning_list)
        else:
            note = QFrame()
            note.setObjectName("VerifiedNote")
            note_layout = hbox(note, (12, 12, 12, 12), 8)
            note_layout.addWidget(Icon("CheckCircle", 20, t["green"], "fill"), 0, Qt.AlignmentFlag.AlignTop)
            note_text = theme.label("Native text and reading order passed the active checks.", size=12, color=t["verified_text"])
            note_text.setWordWrap(True)
            note_layout.addWidget(note_text, 1)
            layout.addWidget(note)

        candidates = candidates_of(block)
        if block and candidates:
            layout.addSpacing(14)
            layout.addWidget(self._candidate_shelf(block, candidates))

        layout.addSpacing(14)
        block_list = vbox(spacing=5)
        for item in document.get("blocks", []):
            block_list.addWidget(self._block_row(item, block))
        layout.addLayout(block_list)

        if block:
            layout.addSpacing(13)
            actions = QGridLayout()
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(6)
            accept = make_button("Accept source", "ReviewButton", "CheckCircle", 15, t["accent_text"], font_size=10)
            accept.clicked.connect(lambda: self.review_requested.emit(str(block["id"]), "accept"))
            edit = make_button("Edit text", "ReviewButton", "ClipboardText", 15, t["accent_text"], font_size=10)
            edit.clicked.connect(lambda: self.review_requested.emit(str(block["id"]), "edit"))
            rerun = make_button("Rerun with Qwen", "ReviewButton", font_size=10)
            rerun.clicked.connect(lambda: self.repair_requested.emit(str(block["id"]), "transcription"))
            ignore = make_button("Ignore warning", "ReviewButtonQuiet", font_size=10)
            ignore.clicked.connect(lambda: self.review_requested.emit(str(block["id"]), "ignore_warning"))
            actions.addWidget(accept, 0, 0)
            actions.addWidget(edit, 0, 1)
            actions.addWidget(rerun, 1, 0)
            actions.addWidget(ignore, 2, 0, 1, 2)
            layout.addLayout(actions)

        layout.addSpacing(10)
        modes = QFrame()
        modes.setObjectName("RepairModes")
        modes_layout = hbox(modes, (3, 3, 3, 3), 5)
        for label_text, mode in (("Text", "transcription"), ("Table", "table"), ("Formula", "formula")):
            mode_button = QPushButton(label_text)
            mode_button.setProperty("repairMode", "true")
            mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
            theme.font(mode_button, 10, 650)
            mode_button.clicked.connect(lambda _=False, value=mode: block and self.repair_requested.emit(str(block["id"]), value))
            modes_layout.addWidget(mode_button, 1)
        layout.addWidget(modes)

        layout.addStretch(1)
        layout.addSpacing(10)
        repair = QPushButton()
        repair.setObjectName("RepairButton")
        repair.setCursor(Qt.CursorShape.PointingHandCursor)
        repair_layout = hbox(repair, (10, 10, 10, 10), 8)
        repair_layout.addWidget(Icon("MagicWand", 17, t["text_secondary"]))
        repair_label = theme.label("Repair selected block", size=11, color=t["text_secondary"])
        repair_layout.addWidget(repair_label)
        repair_layout.addStretch(1)
        repair_layout.addWidget(theme.label("Qwen 3.8 · manual only", size=10, color=t["text_tertiary"]))
        repair.setMinimumHeight(40)
        repair.clicked.connect(lambda: block and self.repair_requested.emit(str(block["id"]), "transcription"))
        layout.addWidget(repair)
        self.scroll.setWidget(content)

    def _summary(self, document: dict[str, Any], block: dict[str, Any] | None) -> QFrame:
        t = theme.tokens()
        pages = document.get("pages", [])
        page = next((item for item in pages if item.get("id") == (block or {}).get("page")), None) or {}
        assets = document.get("outputs", {}).get("extracted_assets")
        overlays = document.get("outputs", {}).get("overlay_diagnostics") or []
        markers = (page.get("source_artifacts") or {}).get("numeric_markers") or []
        confidence = float((block or {}).get("source", {}).get("confidence", 0))
        rows = [
            ("Source method", str((block or {}).get("source", {}).get("method", "")).replace("-", " ") or "Awaiting conversion"),
            ("Confidence", f"{round(confidence * 100)}% {confidence_label(confidence).lower()}" if block else "Not measured"),
            ("Route", str(((page.get("route") or {}).get("decision") or "")).replace("-", " ") or "Not recorded"),
            ("Source region", f"Measured · {str((block or {}).get('bbox', {}).get('coordinate_space', '')).replace('-', ' ')}" if (block or {}).get("bbox") else "No measured region"),
            ("Page rotation", f"{int(page.get('rotation') or 0)}°, measured as displayed" if page.get("rotation") else "Upright"),
            ("Source links", link_summary((block or {}).get("links") or [])),
            ("Native assets", f"{len(assets.get('items', []))} extracted with provenance" if assets else "None extracted"),
            ("Source markers", str(len(markers)) if markers else "None"),
            ("Overlay diagnostics", f"{len(overlays)} pages mapped" if overlays else "No measured overlays"),
        ]
        card = QFrame()
        card.setObjectName("EvidenceSummary")
        layout = vbox(card, (13, 13, 13, 13), 3)
        for index, (key, value) in enumerate(rows):
            layout.addWidget(theme.label(key.upper(), size=10, color=t["text_tertiary"], letter_spacing=0.55))
            value_label = theme.label(value.capitalize(), size=12, weight=650, color=t["text"])
            value_label.setWordWrap(True)
            layout.addWidget(value_label)
            if index < len(rows) - 1:
                layout.addSpacing(8)
        return card

    def _asset_shelf(self, assets: list[dict[str, Any]]) -> QWidget:
        t = theme.tokens()
        shelf = QWidget()
        layout = vbox(shelf, spacing=7)
        header = hbox(spacing=5)
        header.addWidget(Icon("ImageSquare", 15, t["accent_text"], "fill"))
        header.addWidget(theme.label("Extracted images", size=11, weight=650, color=t["text_secondary"]))
        header.addStretch(1)
        header.addWidget(count_pill(len(assets)))
        layout.addLayout(header)
        strip_scroll = QScrollArea()
        strip_scroll.setWidgetResizable(True)
        strip_scroll.setFrameShape(QFrame.Shape.NoFrame)
        strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        strip_scroll.setFixedHeight(112)
        strip = transparent(QWidget())
        strip_layout = hbox(strip, spacing=7)
        for asset in assets[:8]:
            strip_layout.addWidget(self._asset_card(asset))
        strip_layout.addStretch(1)
        strip_scroll.setWidget(strip)
        layout.addWidget(strip_scroll)
        if len(assets) > 8:
            note = theme.label(f"Showing 8 of {len(assets)}. The full set and manifest are in this conversion’s extracted-assets folder.", size=10, color=t["text_tertiary"])
            note.setWordWrap(True)
            layout.addWidget(note)
        return shelf

    def _asset_card(self, asset: dict[str, Any]) -> QPushButton:
        t = theme.tokens()
        card = QPushButton()
        card.setObjectName("AssetCard")
        card.setFixedWidth(92)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setToolTip(f"Open {asset.get('original_name', '')}")
        layout = vbox(card, (5, 5, 5, 5), 4)
        thumbnail = QLabel()
        thumbnail.setFixedHeight(58)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setStyleSheet(f"background: {t['metric_bg']}; border-radius: 4px;")
        path = str(asset.get("path", ""))
        if str(asset.get("mime_type", "")).startswith("image/") and Path(path).exists():
            from .qt import QPixmap

            source = QPixmap(path)
            if not source.isNull():
                thumbnail.setPixmap(source.scaled(82, 58, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        else:
            thumbnail.setPixmap(pixmap("ImageSquare", 22, t["text_tertiary"]))
        layout.addWidget(thumbnail)
        size = f"{asset.get('pixel_width')} × {asset.get('pixel_height')}" if asset.get("pixel_width") else str(asset.get("format") or "Image")
        layout.addWidget(theme.label(size, size=10, weight=650, color=t["text_strong"]))
        layout.addWidget(theme.label(f"p. {', '.join(str(page) for page in asset.get('source_pages', []))}", size=10, color=t["text_tertiary"]))
        card.setMinimumHeight(layout.sizeHint().height())
        card.clicked.connect(lambda: Path(path).exists() and QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        return card

    def _warning_item(self, warning: dict[str, Any]) -> QFrame:
        t = theme.tokens()
        item = QFrame()
        item.setObjectName("WarningItem")
        layout = hbox(item, (10, 10, 10, 10), 9)
        layout.addWidget(Icon("WarningCircle", 18, t["warning_item_text"], "fill"), 0, Qt.AlignmentFlag.AlignTop)
        body = vbox(spacing=3)
        body.addWidget(theme.label(str(warning.get("code", "")).replace("_", " ").upper(), size=10, weight=700, color=t["warning_item_title"], letter_spacing=0.45))
        message = theme.label(str(warning.get("message", "")), size=11, color=t["warning_item_body"])
        message.setWordWrap(True)
        body.addWidget(message)
        layout.addLayout(body, 1)
        return item

    def _candidate_shelf(self, block: dict[str, Any], candidates: list[dict[str, Any]]) -> QWidget:
        t = theme.tokens()
        shelf = QWidget()
        layout = vbox(shelf, spacing=7)
        header = hbox(spacing=5)
        header.addWidget(Icon("MagicWand", 15, t["purple"], "fill"))
        header.addWidget(theme.label("Retained candidates", size=11, weight=650, color=t["text_secondary"]))
        header.addStretch(1)
        header.addWidget(count_pill(len(candidates), purple=True))
        layout.addLayout(header)
        if self.compared_candidate is not None and self.compared_candidate < len(candidates):
            layout.addWidget(self._compare_card(block, candidates[self.compared_candidate]))
        for index, candidate in enumerate(candidates):
            layout.addWidget(self._candidate_item(block, candidate, index))
        return shelf

    def _compare_card(self, block: dict[str, Any], candidate: dict[str, Any]) -> QFrame:
        t = theme.tokens()
        card = QFrame()
        card.setObjectName("CandidateCompare")
        layout = vbox(card, (10, 10, 10, 10), 3)
        header = hbox(spacing=8)
        header.addWidget(theme.label("Compare before applying", size=11, weight=650, color=t["text"]))
        header.addStretch(1)
        close = QToolButton()
        close.setObjectName("BannerClose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setIcon(pixmap("X", 13, t["text_secondary"]))
        close.setIconSize(QSize(13, 13))
        close.clicked.connect(self._close_compare)
        header.addWidget(close)
        layout.addLayout(header)
        changed = changed_token_count(block.get("text", ""), candidate.get("text", ""))
        layout.addWidget(theme.label(f"{changed} changed token{'' if changed == 1 else 's'} · source evidence remains retained", size=10, color="#5a6f64" if not theme.system_is_dark() else t["verified_text"]))
        layout.addSpacing(5)
        columns = hbox(spacing=7)
        for caption, text in (("Current export", block.get("text", "")), (str(candidate.get("model") or candidate.get("kind") or "Candidate").replace("-", " "), candidate.get("text", ""))):
            column = QFrame()
            column.setObjectName("CompareColumn")
            column_layout = vbox(column, (7, 7, 7, 7), 5)
            caption_label = ElidedLabel(caption.upper())
            theme.font(caption_label, 9, 700, 0.3)
            caption_label.setStyleSheet(f"color: {t['accent_text']}; background: transparent;")
            column_layout.addWidget(caption_label)
            body = theme.label(text[:600], size=10, color=t["text_strong"])
            body.setWordWrap(True)
            theme.mono_font(body, 10)
            column_layout.addWidget(body)
            columns.addWidget(column, 1)
        layout.addLayout(columns)
        return card

    def _close_compare(self) -> None:
        self.compared_candidate = None
        self._rebuild()

    def _open_compare(self, index: int) -> None:
        self.compared_candidate = index
        self._rebuild()

    def _candidate_item(self, block: dict[str, Any], candidate: dict[str, Any], index: int) -> QFrame:
        t = theme.tokens()
        item = QFrame()
        item.setObjectName("CandidateItem")
        layout = vbox(item, (9, 9, 9, 9), 6)
        heading = hbox(spacing=8)
        title = ElidedLabel(str(candidate.get("model") or candidate.get("kind") or "Retained candidate").replace("-", " "))
        theme.font(title, 10, 650)
        title.setStyleSheet(f"color: {t['text_strong']}; background: transparent;")
        heading.addWidget(title, 1)
        quality = candidate.get("quality") or {}
        if candidate.get("selected"):
            status = "Currently selected"
        elif quality.get("status") == "review-required":
            issues = quality.get("issues") or []
            status = "Format review required" + (f" · {' '.join(issues)}" if issues else "")
        else:
            status = "Unselected · source retained"
        status_label = ElidedLabel(status)
        theme.font(status_label, 10)
        status_label.setStyleSheet(f"color: {t['text_tertiary']}; background: transparent;")
        status_label.setMaximumWidth(150)
        heading.addWidget(status_label, 1)
        layout.addLayout(heading)
        body = theme.label(candidate.get("text", "")[:280], size=10, color=t["text_strong"])
        body.setWordWrap(True)
        theme.mono_font(body, 10)
        layout.addWidget(body)
        actions = hbox(spacing=7)
        crop = candidate.get("source_crop")
        if crop:
            crop_button = make_button("View source crop", "CandidateLink", font_size=10)
            crop_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(crop))))
            actions.addWidget(crop_button, 1)
        compare = make_button("Compare", "ReviewButton", font_size=10)
        compare.clicked.connect(lambda _=False, value=index: self._open_compare(value))
        actions.addWidget(compare, 1)
        use = make_button("Use this candidate", "ReviewButton", font_size=10)
        use.setEnabled(not candidate.get("selected"))
        use.clicked.connect(lambda _=False, value=index: self.candidate_restored.emit(str(block["id"]), value))
        actions.addWidget(use, 1)
        layout.addLayout(actions)
        return item

    def _block_row(self, item: dict[str, Any], selected: dict[str, Any] | None) -> QPushButton:
        t = theme.tokens()
        row = QPushButton()
        row.setProperty("blockItem", "true")
        row.setCheckable(True)
        row.setChecked(bool(selected and item.get("id") == selected.get("id")))
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setMinimumHeight(34)
        layout = hbox(row, (8, 0, 8, 0), 8)
        kind = theme.label(str(item.get("type", "")).upper(), size=10, color=t["accent_text"])
        kind.setFixedWidth(56)
        layout.addWidget(kind)
        text = theme.label((item.get("text", "")[:52] or "No text extracted"), size=11, weight=540, color=t["text_strong"])
        layout.addWidget(text, 1)
        layout.addWidget(Icon("CaretRight", 15, t["text_tertiary"]))
        row.clicked.connect(lambda: self.block_chosen.emit(str(item.get("id"))))
        return row
