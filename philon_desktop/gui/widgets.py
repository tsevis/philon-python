"""Shared controls of the workspace skin: tabs, segments, banners, progress."""

from __future__ import annotations

from typing import Any, Callable

from . import theme
from .phosphor import Icon, pixmap
from .qt import (
    QColor,
    QFontMetrics,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPainter,
    QPainterPath,
    QPushButton,
    QSize,
    QSizePolicy,
    QTimer,
    QToolButton,
    QTransform,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
)


def hbox(parent: QWidget | None = None, margins: tuple[int, int, int, int] = (0, 0, 0, 0), spacing: int = 0) -> QHBoxLayout:
    layout = QHBoxLayout(parent) if parent else QHBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def vbox(parent: QWidget | None = None, margins: tuple[int, int, int, int] = (0, 0, 0, 0), spacing: int = 0) -> QVBoxLayout:
    layout = QVBoxLayout(parent) if parent else QVBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def clear_layout(layout: Any) -> None:
    """Empty a layout NOW. deleteLater alone leaves the removed widgets
    parented and painting until the event loop spins, which reads as ghost
    duplicates whenever a view rebuilds."""
    while layout.count():
        entry = layout.takeAt(0)
        widget = entry.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif entry.layout() is not None:
            clear_layout(entry.layout())


def transparent(widget: QWidget) -> QWidget:
    """Mark a container so the app stylesheet paints it transparent without
    cascading into its children the way an inline background rule would."""
    widget.setProperty("transparentBg", "true")
    return widget


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

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        available_width = max(0, self.contentsRect().width())
        visible_text = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, available_width)
        super().setText(visible_text)


def eyebrow(text: str, icon_name: str | None = None, color: str | None = None, icon_color: str | None = None, weight: str = "fill") -> QWidget:
    """The uppercase 10px section label, with its small leading icon."""
    t = theme.tokens()
    row = QWidget()
    layout = hbox(row, spacing=5)
    if icon_name:
        layout.addWidget(Icon(icon_name, 14, icon_color or color or t["text_tertiary"], weight))
    text_label = theme.label(text.upper(), size=10, weight=650, color=color or t["text_tertiary"], letter_spacing=0.4)
    layout.addWidget(text_label)
    layout.addStretch(1)
    return row


class Segmented(QFrame):
    """The rounded segmented control: job tabs, profile picker, format switch."""

    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str | None]], padding: int = 3, font_size: int = 12, font_weight: int = 650, button_padding: str = "7px 11px", tooltips: dict[str, str] | None = None) -> None:
        super().__init__()
        self.setObjectName("Segmented")
        self.buttons: dict[str, QPushButton] = {}
        layout = hbox(self, (padding, padding, padding, padding), 0)
        t = theme.tokens()
        for name, icon_name in items:
            button = QPushButton(f" {name}" if icon_name else name)
            button.setProperty("segment", "true")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if icon_name:
                button.setIcon(pixmap(icon_name, 17, t["text_mid"]))
                button.setIconSize(QSize(17, 17))
            if tooltips and name in tooltips:
                button.setToolTip(tooltips[name])
            theme.font(button, font_size, font_weight)
            button.setStyleSheet(f"padding: {button_padding};")
            button.clicked.connect(lambda _=False, value=name: self.select(value))
            layout.addWidget(button)
            self.buttons[name] = button

    def select(self, name: str, announce: bool = True) -> None:
        t = theme.tokens()
        for value, button in self.buttons.items():
            active = value == name
            button.setChecked(active)
            if not button.icon().isNull():
                icon_name = {" Single Job": "FileArrowUp", " Batch": "ListChecks"}.get(button.text())
                if icon_name:
                    button.setIcon(pixmap(icon_name, 17, t["segment_active_text"] if active else t["text_mid"]))
        if announce:
            self.changed.emit(name)

    def value(self) -> str:
        return next((name for name, button in self.buttons.items() if button.isChecked()), "")


class MainTab(QPushButton):
    """A header navigation tab: text, optional count badge, active underline."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.count = 0
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        theme.font(self, 13, 590)
        self.setStyleSheet("QPushButton { background: transparent; border: 0; }")

    def set_count(self, count: int) -> None:
        self.count = count
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(self.text()) + 24
        if self.count:
            width += 6 + max(17, metrics.horizontalAdvance(str(self.count)) + 8)
        return QSize(width, 58)

    def paintEvent(self, event: Any) -> None:
        t = theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() and not self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(t["accent_soft_055"]))
            painter.drawRect(self.rect())
        color = t["accent_text"] if self.isChecked() else (t["text"] if self.underMouse() else t["text_secondary"])
        font = self.font()
        font.setWeight(font.Weight.Bold if self.isChecked() else font.Weight.Medium)
        painter.setFont(font)
        painter.setPen(theme.qcolor(color))
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(self.text())
        x = 12
        painter.drawText(x, 0, text_width, self.height(), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.count:
            badge_font = self.font()
            badge_font.setPixelSize(10)
            badge_metrics = QFontMetrics(badge_font)
            badge_width = max(17, badge_metrics.horizontalAdvance(str(self.count)) + 8)
            badge_x = x + text_width + 6
            badge_y = (self.height() - 17) // 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(t["accent"]))
            path = QPainterPath()
            path.addRoundedRect(badge_x, badge_y, badge_width, 17, 9, 9)
            painter.drawPath(path)
            painter.setFont(badge_font)
            painter.setPen(theme.qcolor(t["accent_ink"]))
            painter.drawText(badge_x, badge_y, badge_width, 17, Qt.AlignmentFlag.AlignCenter, str(self.count))
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(t["accent_text"]))
            painter.drawRoundedRect(12, self.height() - 2, self.sizeHint().width() - 24, 2, 1, 1)
        painter.end()


class Banner(QFrame):
    """The dismissible error/notice strip below the header."""

    dismissed = Signal()

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.setObjectName("ErrorBanner" if kind == "error" else "NoticeBanner")
        t = theme.tokens()
        layout = hbox(self, (14, 11, 14, 11), 9)
        icon_name = "WarningCircle" if kind == "error" else "CheckCircle"
        icon_color = t["error_banner_text"] if kind == "error" else t["notice_banner_text"]
        layout.addWidget(Icon(icon_name, 19 if kind == "error" else 18, icon_color, "fill"))
        self.message = QLabel("")
        self.message.setWordWrap(True)
        theme.font(self.message, 13)
        layout.addWidget(self.message, 1)
        close = QToolButton()
        close.setObjectName("BannerClose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setIcon(pixmap("X", 17, icon_color))
        close.setIconSize(QSize(17, 17))
        close.clicked.connect(self.dismissed.emit)
        layout.addWidget(close)
        self.hide()

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.show()


class ProgressTrack(QWidget):
    """The 5px rounded track, determinate or sweeping."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(5)
        self.percent = 0
        self.indeterminate = False
        self._sweep = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def set_state(self, percent: int, indeterminate: bool) -> None:
        self.percent = max(0, min(100, percent))
        self.indeterminate = indeterminate
        if indeterminate and not self._timer.isActive():
            self._timer.start()
        elif not indeterminate:
            self._timer.stop()
        self.update()

    def hideEvent(self, event: Any) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event: Any) -> None:
        if self.indeterminate:
            self._timer.start()
        super().showEvent(event)

    def _advance(self) -> None:
        self._sweep = (self._sweep + 0.016 / 1.25) % 1.0
        self.update()

    def paintEvent(self, event: Any) -> None:
        t = theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.qcolor(t["task_track"]))
        painter.drawRoundedRect(0, 0, self.width(), 5, 2.5, 2.5)
        painter.setBrush(theme.qcolor(t["task_fill"]))
        if self.indeterminate:
            width = self.width() * 0.34
            x = (self.width() + width) * self._sweep - width
            painter.drawRoundedRect(int(x), 0, int(width), 5, 2.5, 2.5)
        elif self.percent:
            painter.drawRoundedRect(0, 0, max(4, int(self.width() * self.percent / 100)), 5, 2.5, 2.5)
        painter.end()


TASK_TITLES = {
    "preflight": "Inspecting files",
    "repair": "Repairing selected block",
    "export": "Exporting conversion",
    "library": "Loading local library",
    "models": "Inspecting local models",
    "conversion": "Converting locally",
}


class TaskProgressCard(QFrame):
    """The in-flow progress surface: heading, track, and message detail."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TaskProgress")
        t = theme.tokens()
        layout = vbox(self, (13, 11, 13, 10), 0)
        heading = hbox(spacing=14)
        title_row = hbox(spacing=7)
        self.spinner = Icon("CircleNotch", 15, t["accent_text"], "bold")
        self._angle = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(40)
        self._spin_timer.timeout.connect(self._spin)
        title_row.addWidget(self.spinner)
        self.title = theme.label("Converting locally", size=12, weight=650, color=t["text"])
        title_row.addWidget(self.title)
        heading.addLayout(title_row)
        heading.addStretch(1)
        self.percent_label = theme.label("0%", size=12, weight=650, color=t["accent_text"])
        heading.addWidget(self.percent_label)
        layout.addLayout(heading)
        layout.addSpacing(9)
        self.track = ProgressTrack()
        layout.addWidget(self.track)
        layout.addSpacing(7)
        detail = hbox(spacing=14)
        self.detail = ElidedLabel("")
        theme.font(self.detail, 11)
        self.detail.setStyleSheet(f"color: {t['text_secondary']}; background: transparent;")
        detail.addWidget(self.detail, 1)
        self.detail_strong = theme.label("", size=11, weight=600, color=t["text_strong"])
        detail.addWidget(self.detail_strong)
        layout.addLayout(detail)
        self.hide()

    def _spin(self) -> None:
        self._angle = (self._angle + 12) % 360
        t = theme.tokens()
        base = pixmap("CircleNotch", 15, t["accent_text"], "bold")
        rotated = base.transformed(QTransform().rotate(self._angle), Qt.TransformationMode.SmoothTransformation)
        self.spinner.setPixmap(rotated)

    def show_progress(self, progress: dict[str, Any]) -> None:
        percent = max(0, min(100, round(progress.get("percent") or 0)))
        indeterminate = bool(progress.get("indeterminate"))
        self.title.setText(TASK_TITLES.get(progress.get("kind", "conversion"), "Converting locally"))
        self.percent_label.setText("Working" if indeterminate else f"{percent}%")
        self.track.set_state(percent, indeterminate)
        self.detail.setText(progress.get("message", ""))
        current, total = progress.get("current"), progress.get("total")
        if current and total:
            self.detail_strong.setText(f"{current} of {total}")
        elif progress.get("sourcePath"):
            self.detail_strong.setText(progress["sourcePath"].rsplit("/", 1)[-1])
        else:
            self.detail_strong.setText("")
        if not self._spin_timer.isActive():
            self._spin_timer.start()
        self.show()

    def hide_progress(self) -> None:
        self._spin_timer.stop()
        self.hide()


def count_pill(count: int, purple: bool = False) -> QLabel:
    pill = theme.label(str(count), size=11, weight=650)
    pill.setObjectName("CountPillPurple" if purple else "CountPill")
    pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pill.setFixedHeight(17)
    pill.setMinimumWidth(18)
    pill.setContentsMargins(5, 0, 5, 0)
    return pill


def make_button(text: str, object_name: str, icon_name: str | None = None, icon_size: int = 16, icon_color: str | None = None, icon_weight: str = "regular", font_size: int = 13, font_weight: int = 650) -> QPushButton:
    button = QPushButton(f" {text}" if icon_name else text)
    button.setObjectName(object_name)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    theme.font(button, font_size, font_weight)
    if icon_name:
        t = theme.tokens()
        color = icon_color or {"PrimaryButton": t["accent_ink"], "SecondaryButton": t["accent_text"]}.get(object_name, t["accent_text"])
        button.setIcon(pixmap(icon_name, icon_size, color, icon_weight))
        button.setIconSize(QSize(icon_size, icon_size))
    return button
