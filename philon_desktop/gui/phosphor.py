"""Render Phosphor icons (MIT) to tinted pixmaps at the source app's sizes."""

from __future__ import annotations

from .qt import QColor, QPainter, QPixmap, QRectF, QSvgRenderer, Qt, QLabel, QWidget
from .phosphor_data import ICON_PATHS

_cache: dict[tuple[str, str, int, str, float], QPixmap] = {}


def pixmap(name: str, size: int, color: str, weight: str = "regular", device_ratio: float = 2.0) -> QPixmap:
    """A square icon pixmap, drawn from the 256-unit Phosphor path data."""
    key = (name, weight, size, color, device_ratio)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    paths = ICON_PATHS[name][weight]
    parts = []
    for d, opacity in paths:
        opacity_attr = f' opacity="{opacity}"' if opacity else ""
        parts.append(f'<path d="{d}" fill="{color}"{opacity_attr}/>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">{"".join(parts)}</svg>'
    renderer = QSvgRenderer(bytearray(svg, "utf-8"))
    result = QPixmap(round(size * device_ratio), round(size * device_ratio))
    result.fill(QColor(0, 0, 0, 0))
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, result.width(), result.height()))
    painter.end()
    result.setDevicePixelRatio(device_ratio)
    _cache[key] = result
    return result


class Icon(QLabel):
    """A fixed-size icon widget, tinted with a token color."""

    def __init__(self, name: str, size: int, color: str, weight: str = "regular", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec = (name, size, color, weight)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: transparent;")
        self.setPixmap(pixmap(name, size, color, weight))

    def set_color(self, color: str) -> None:
        name, size, _, weight = self._spec
        self._spec = (name, size, color, weight)
        self.setPixmap(pixmap(name, size, color, weight))
