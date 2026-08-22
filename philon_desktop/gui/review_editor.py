"""The review editor: an in-window modal for replacing one block's export text."""

from __future__ import annotations

from typing import Any

from . import theme
from .phosphor import pixmap
from .splash import Overlay
from .widgets import hbox, make_button, vbox
from .qt import QFrame, QSize, QTextEdit, QToolButton, QWidget, Qt, Signal


class ReviewEditor(Overlay):
    saved = Signal(str, str)

    def __init__(self, parent: QWidget, block: dict[str, Any]) -> None:
        super().__init__(parent)
        self.block = block
        t = theme.tokens()
        card = QFrame(self)
        card.setObjectName("ReviewEditorCard")
        card.setFixedWidth(min(680, max(320, parent.width() - 48)))
        card.setStyleSheet(
            f"#ReviewEditorCard {{ background: {t['modal_bg']}; border: 1px solid {t['modal_border']}; border-radius: 12px; }}"
        )
        card.mousePressEvent = lambda event: None
        layout = vbox(card, (20, 20, 20, 20), 0)

        header = hbox(spacing=16)
        heading = vbox(spacing=4)
        eyebrow_row = hbox(spacing=5)
        eyebrow_icon = QToolButton()
        eyebrow_icon.setObjectName("BannerClose")
        eyebrow_icon.setIcon(pixmap("ClipboardText", 14, t["text_tertiary"], "fill"))
        eyebrow_icon.setIconSize(QSize(14, 14))
        eyebrow_icon.setEnabled(False)
        eyebrow_row.addWidget(eyebrow_icon)
        eyebrow_row.addWidget(theme.label("REVIEW EDIT", size=10, weight=650, color=t["text_tertiary"], letter_spacing=0.4))
        eyebrow_row.addStretch(1)
        heading.addLayout(eyebrow_row)
        heading.addWidget(theme.label("Edit exported text", size=20, weight=650, color=t["text"], letter_spacing=-0.6))
        note = theme.label(f"{block.get('type', '')} · {block.get('page', '')} · the source candidate will remain in the evidence record.", size=12, color=t["text_secondary"])
        note.setWordWrap(True)
        heading.addWidget(note)
        header.addLayout(heading, 1)
        close = QToolButton()
        close.setObjectName("IconButton")
        close.setFixedSize(30, 30)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setIcon(pixmap("X", 18, t["text_strong"]))
        close.setIconSize(QSize(18, 18))
        close.clicked.connect(self.dismissed.emit)
        header.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        layout.addSpacing(18)
        layout.addWidget(theme.label("Replacement text", size=12, weight=650, color=t["text_strong"]))
        layout.addSpacing(6)
        self.editor = QTextEdit()
        self.editor.setPlainText(str(block.get("text", "")))
        self.editor.setMinimumHeight(235)
        theme.mono_font(self.editor, 12)
        self.editor.setStyleSheet(
            f"QTextEdit {{ background: {t['editor_field_bg']}; color: {t['text']};"
            f" border: 1px solid {t['field_border']}; border-radius: 7px; padding: 12px; }}"
        )
        layout.addWidget(self.editor)

        layout.addSpacing(14)
        footer = hbox(spacing=12)
        footer.addWidget(theme.label("⌘↵ saves · Esc cancels", size=11, color=t["text_tertiary"]))
        footer.addStretch(1)
        cancel = make_button("Cancel", "SecondaryButton", font_size=13)
        cancel.clicked.connect(self.dismissed.emit)
        footer.addWidget(cancel)
        self.save_button = make_button("Save edit", "PrimaryButton", font_size=13, font_weight=750)
        self.save_button.clicked.connect(self._save)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        self.card = card
        self.editor.textChanged.connect(lambda: self.save_button.setEnabled(bool(self.editor.toPlainText().strip())))
        self._center()
        self.editor.setFocus()

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self._save()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.dismissed.emit()
            return
        super().keyPressEvent(event)

    def _save(self) -> None:
        text = self.editor.toPlainText()
        if text.strip():
            self.saved.emit(str(self.block.get("id")), text)

    def _center(self) -> None:
        size = self.card.sizeHint()
        self.card.adjustSize()
        self.card.move((self.width() - self.card.width()) // 2, max(24, (self.height() - self.card.height()) // 2))

    def resizeEvent(self, event: Any) -> None:
        self._center()
        super().resizeEvent(event)
