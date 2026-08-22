"""The splash, at the measurements of the macOS original: 640x580, 250px of
full-bleed key art, 26px margins, 36/13/11pt type, and the mark's height
derived from the type beside it rather than picked by eye."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import theme
from .about import ABOUT, CREDIT, LEGAL, LINKS, SUBTITLE, TITLE, VERSION
from .phosphor import Icon, pixmap
from .widgets import hbox, make_button, vbox
from .qt import (
    QColor,
    QDesktopServices,
    QFrame,
    QLabel,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QPushButton,
    QUrl,
    QWidget,
    Qt,
    Signal,
)

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SPLASH_SEEN_KEY = "splash.seen.v1"


class Overlay(QWidget):
    """A dimmed full-window backdrop; clicking outside the card dismisses."""

    dismissed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Scoped to this widget only: an unscoped rule would cascade the dim
        # onto every child of the card in front of it.
        self.setObjectName("ModalBackdrop")
        self.setStyleSheet("#ModalBackdrop { background: rgba(29,29,31,.32); }")
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if watched is self.parent() and event.type() in (14, 12):  # Resize, Paint
            self.setGeometry(self.parent().rect())
        return False

    def mousePressEvent(self, event: Any) -> None:
        self.dismissed.emit()

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.dismissed.emit()
        else:
            super().keyPressEvent(event)


class SplashArt(QLabel):
    """The key art band with its bottom scrim, so white type stays legible."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(250)
        self.banner = QPixmap(str(ASSETS / "splash-banner.jpg"))

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self.banner.isNull():
            scaled = self.banner.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.5, QColor(0, 0, 0, 0))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 89))
        painter.fillRect(self.rect(), gradient)
        painter.end()


class Splash(Overlay):
    """The about card in front of the workspace, in the system appearance."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        t = theme.tokens()
        self.legal_open = False
        card = QFrame(self)
        card.setObjectName("SplashCard")
        card.setFixedSize(640, 580)
        card.setStyleSheet(
            f"#SplashCard {{ background: {t['modal_bg'] if theme.system_is_dark() else '#ffffff'};"
            f" border: 1px solid {t['modal_border']}; border-radius: 12px; }}"
        )
        card.mousePressEvent = lambda event: None  # clicks inside stay inside
        layout = vbox(card, (0, 0, 0, 0), 0)

        art = SplashArt()
        art.setStyleSheet("border-top-left-radius: 12px; border-top-right-radius: 12px;")
        art_host = QWidget()
        art_layout = vbox(art_host, (0, 0, 0, 0), 0)
        art_layout.addWidget(art)
        lockup = QWidget(art)
        lockup_layout = hbox(lockup, (26, 0, 26, 0), 12)
        logo = QLabel()
        logo_pixmap = QPixmap(str(ASSETS / "tvd-logo.png"))
        if not logo_pixmap.isNull():
            logo.setPixmap(logo_pixmap.scaled(49, 49, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        lockup_layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignBottom)
        names = vbox(spacing=0)
        title = theme.label(TITLE, size=36, weight=600, color=t["accent"], letter_spacing=-0.6)
        names.addWidget(title)
        subtitle = theme.label(SUBTITLE, size=13, color="rgba(255,255,255,.85)")
        names.addWidget(subtitle)
        names.addSpacing(3)
        lockup_layout.addLayout(names)
        lockup_layout.addStretch(1)
        version = theme.label(VERSION, size=11, weight=500, color="rgba(255,255,255,.7)")
        version_column = vbox(spacing=0)
        version_column.addStretch(1)
        version_column.addWidget(version)
        version_column.addSpacing(3)
        lockup_layout.addLayout(version_column)
        lockup.setGeometry(0, 250 - 20 - 66, 640, 66)
        layout.addWidget(art_host)

        body = vbox(spacing=11)
        for paragraph in ABOUT.split("\n\n"):
            text = theme.label(paragraph, size=12, color=t["text"])
            text.setWordWrap(True)
            body.addWidget(text)
        body_host = QWidget()
        body_layout = vbox(body_host, (26, 20, 26, 0), 0)
        body_layout.addLayout(body)
        layout.addWidget(body_host)
        layout.addStretch(1)

        legal_host = QWidget()
        legal_layout = vbox(legal_host, (26, 0, 26, 14), 6)
        self.legal_toggle = QPushButton(" Sources, licences and credits")
        self.legal_toggle.setStyleSheet(f"QPushButton {{ background: transparent; border: 0; color: {t['text_secondary']}; text-align: left; }} QPushButton:hover {{ color: {t['text']}; }}")
        theme.font(self.legal_toggle, 11, 500)
        self.legal_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.legal_toggle.setIcon(pixmap("CaretRight", 11, t["text_secondary"], "bold"))
        self.legal_toggle.clicked.connect(self._toggle_legal)
        legal_layout.addWidget(self.legal_toggle)
        self.legal_body = QWidget()
        legal_body_layout = vbox(self.legal_body, spacing=6)
        for paragraph in LEGAL.split("\n\n"):
            text = theme.label(paragraph, size=10, color=t["text_secondary"])
            text.setWordWrap(True)
            legal_body_layout.addWidget(text)
        self.legal_body.hide()
        legal_layout.addWidget(self.legal_body)
        layout.addWidget(legal_host)

        footer = QFrame()
        footer.setObjectName("SplashFooter")
        footer.setStyleSheet(
            f"#SplashFooter {{ background: {t['window_bg'] if theme.system_is_dark() else '#f5f5f7'};"
            f" border-top: 1px solid {t['header_border']};"
            " border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }"
        )
        footer_layout = hbox(footer, (26, 14, 26, 14), 14)
        footer_layout.addWidget(theme.label(CREDIT, size=11, color=t["text_secondary"]))
        for label_text, address in LINKS:
            link = QPushButton(label_text)
            link.setStyleSheet(f"QPushButton {{ background: transparent; border: 0; color: {t['accent_text']}; }}")
            theme.font(link, 11)
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.clicked.connect(lambda _=False, value=address: QDesktopServices.openUrl(QUrl(value)))
            footer_layout.addWidget(link)
        footer_layout.addStretch(1)
        continue_button = make_button("Continue", "PrimaryButton", font_size=12, font_weight=600)
        continue_button.clicked.connect(self.dismissed.emit)
        footer_layout.addWidget(continue_button)
        layout.addWidget(footer)

        self.card = card
        self._center()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _toggle_legal(self) -> None:
        t = theme.tokens()
        self.legal_open = not self.legal_open
        self.legal_body.setVisible(self.legal_open)
        # The disclosure caret rotates 90° when open, as the CSS transition does.
        icon = pixmap("CaretRight", 11, t["text_secondary"], "bold")
        if self.legal_open:
            from .qt import QTransform

            icon = icon.transformed(QTransform().rotate(90), Qt.TransformationMode.SmoothTransformation)
        self.legal_toggle.setIcon(icon)

    def _center(self) -> None:
        self.card.move((self.width() - 640) // 2, max(0, (self.height() - 580) // 2))

    def resizeEvent(self, event: Any) -> None:
        self._center()
        super().resizeEvent(event)
