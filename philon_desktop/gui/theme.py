"""The macOS workspace skin of the source application, as design tokens + QSS.

Every value is carried over from the authored stylesheet of the original
Tauri/React Philon (`src/styles.css`), light and dark. Wherever the CSS used a
translucent color over a known surface, the rgba value is kept verbatim — Qt
style sheets composite rgba the same way the browser does.
"""

from __future__ import annotations

from .qt import QColor, QFont, QFontDatabase, QGraphicsDropShadowEffect, QGuiApplication, QLabel, QWidget

# The Continue button's green is the application accent. Two values, not one:
# the mint reads well as a fill, but as type on a white panel it falls to about
# 1.9:1, so accent *text* on light surfaces uses the same hue taken down to a
# legible value.
ACCENT = "#74d2a2"
ACCENT_HOVER = "#8de0b1"
ACCENT_INK = "#08130e"
ACCENT_TEXT = "#0f7a55"
ACCENT_TEXT_HOVER = "#0b5f43"

LIGHT: dict[str, str] = {
    "accent": ACCENT,
    "accent_hover": ACCENT_HOVER,
    "accent_ink": ACCENT_INK,
    "accent_text": ACCENT_TEXT,
    "accent_text_hover": ACCENT_TEXT_HOVER,
    "accent_soft_08": "rgba(15,122,85,.08)",
    "accent_soft_09": "rgba(15,122,85,.09)",
    "accent_soft_10": "rgba(15,122,85,.1)",
    "accent_soft_055": "rgba(15,122,85,.055)",
    "accent_soft_12": "rgba(15,122,85,.12)",
    "accent_soft_15": "rgba(15,122,85,.15)",
    "accent_soft_16": "rgba(15,122,85,.16)",
    "accent_soft_18": "rgba(15,122,85,.18)",
    "window_bg": "#f5f5f7",
    "text": "#1d1d1f",
    "text_strong": "#3a3a3c",
    "text_mid": "#5f5f63",
    "text_secondary": "#6e6e73",
    "text_tertiary": "#8e8e93",
    "text_disabled": "#aeaeb2",
    "header_bg": "rgba(255,255,255,.78)",
    "header_border": "rgba(60,60,67,.14)",
    "control_bg_10": "rgba(118,118,128,.1)",
    "control_bg_12": "rgba(118,118,128,.12)",
    "control_bg_14": "rgba(118,118,128,.14)",
    "control_bg_16": "rgba(118,118,128,.16)",
    "control_bg_20": "rgba(118,118,128,.2)",
    "segment_active_bg": "#ffffff",
    "segment_active_text": "#1d1d1f",
    "panel_bg": "rgba(255,255,255,.82)",
    "panel_border": "rgba(60,60,67,.16)",
    "panel_divider": "rgba(60,60,67,.13)",
    "evidence_bg": "rgba(246,246,248,.8)",
    "card_bg": "#ffffff",
    "card_border": "#d2d2d7",
    "field_border": "#c7c7cc",
    "field_bg": "#f5f5f7",
    "paper_frame_bg": "#e9eaed",
    "paper_frame_ring": "rgba(60,60,67,.12)",
    "paper_canvas_bg": "#dadce0",
    "preview_bg": "#ececf0",
    "green": "#34c759",
    "orange": "#ff9500",
    "purple": "#5856d6",
    "purple_hover_text": "#3634a3",
    "purple_soft": "rgba(88,86,214,.1)",
    "purple_soft_hover": "rgba(88,86,214,.12)",
    "task_bg": "rgba(255,255,255,.84)",
    "task_border": "rgba(15,122,85,.24)",
    "task_track": "rgba(118,118,128,.17)",
    "task_fill": ACCENT_TEXT,
    "danger_text": "#b42318",
    "danger_bg": "#fff1f0",
    "danger_bg_hover": "#ffe3e0",
    "warning_item_bg": "#fff7ed",
    "warning_item_border": "#fed7aa",
    "warning_item_text": "#8a3d00",
    "warning_item_title": "#9a4500",
    "warning_item_body": "#7c5b41",
    "verified_bg": "#effaf2",
    "verified_border": "#bde4c8",
    "verified_text": "#246b3a",
    "diagnostic_bg": "#eefaf4",
    "diagnostic_border": "#bfe6d3",
    "diagnostic_body": "#4a6357",
    "compare_bg": "#eef5ff",
    "compare_border": "#b9d8ff",
    "metric_bg": "#f5f5f7",
    "empty_border": "rgba(60,60,67,.25)",
    "modal_bg": "#f5f5f7",
    "modal_border": "rgba(60,60,67,.18)",
    "editor_field_bg": "#ffffff",
    "toggle_disabled_bg": "#f0f0f2",
    "shadow": "rgba(0,0,0,.06)",
}

DARK: dict[str, str] = {
    **LIGHT,
    "window_bg": "#1c1c1e",
    "text": "#f5f5f7",
    "text_strong": "#ebebf5",
    "text_mid": "#ebebf5",
    "text_secondary": "#aeaeb2",
    "text_tertiary": "#aeaeb2",
    "text_disabled": "#aeaeb2",
    "header_bg": "rgba(28,28,30,.78)",
    "header_border": "rgba(235,235,245,.13)",
    "control_bg_10": "rgba(118,118,128,.25)",
    "control_bg_12": "rgba(118,118,128,.25)",
    "control_bg_14": "rgba(118,118,128,.32)",
    "control_bg_16": "rgba(118,118,128,.32)",
    "control_bg_20": "rgba(118,118,128,.4)",
    "segment_active_bg": "#636366",
    "segment_active_text": "#ffffff",
    "panel_bg": "#2c2c2e",
    "panel_border": "rgba(235,235,245,.13)",
    "panel_divider": "rgba(235,235,245,.13)",
    "evidence_bg": "#242426",
    "card_bg": "#3a3a3c",
    "card_border": "rgba(235,235,245,.16)",
    "field_border": "#636366",
    "field_bg": "#1c1c1e",
    "paper_frame_bg": "#3a3a3c",
    "paper_frame_ring": "rgba(235,235,245,.16)",
    "preview_bg": "#3a3a3c",
    "accent_soft_08": "rgba(116,210,162,.18)",
    "accent_soft_09": "rgba(116,210,162,.18)",
    "accent_soft_10": "rgba(116,210,162,.18)",
    "accent_soft_055": "rgba(116,210,162,.16)",
    "accent_soft_12": "rgba(116,210,162,.2)",
    "accent_soft_15": "rgba(116,210,162,.24)",
    "accent_soft_16": "rgba(116,210,162,.24)",
    "accent_soft_18": "rgba(116,210,162,.24)",
    # On dark surfaces the mint itself is the legible value, so accent text
    # uses the fill color, exactly as the CSS dark overrides do.
    "accent_text": ACCENT,
    "accent_text_hover": ACCENT_HOVER,
    "purple": "#b4b2ff",
    "purple_hover_text": "#ffffff",
    "purple_soft": "rgba(94,92,230,.22)",
    "purple_soft_hover": "rgba(94,92,230,.28)",
    "task_bg": "#2c2c2e",
    "task_border": "rgba(116,210,162,.52)",
    "task_track": "rgba(235,235,245,.2)",
    "task_fill": ACCENT,
    "danger_text": "#ffb4ab",
    "danger_bg": "rgba(255,69,58,.18)",
    "danger_bg_hover": "rgba(255,69,58,.28)",
    "warning_item_bg": "#452b13",
    "warning_item_border": "#754c18",
    "warning_item_text": "#ffba8a",
    "warning_item_title": "#ffd0ad",
    "warning_item_body": "#dcbca6",
    "verified_bg": "#193b26",
    "verified_border": "#2e6b43",
    "verified_text": "#b8d9c3",
    "diagnostic_bg": "#173328",
    "diagnostic_border": "#2d6349",
    "diagnostic_body": "#bce4cf",
    "compare_bg": "#173328",
    "compare_border": "#2d6349",
    "metric_bg": "#3a3a3c",
    "empty_border": "rgba(235,235,245,.2)",
    "modal_bg": "#2c2c2e",
    "modal_border": "rgba(235,235,245,.16)",
    "editor_field_bg": "#1c1c1e",
    "toggle_disabled_bg": "rgba(118,118,128,.25)",
    "shadow": "rgba(0,0,0,0)",
}

# Values the light skin never overrode in the source stylesheet, so the shipped
# application shows them identically in both appearances. Copied, not fixed.
UNSKINNED = {
    "error_banner_bg": "#422820",
    "error_banner_border": "#754333",
    "error_banner_text": "#ffd7ca",
    "notice_banner_bg": "#183225",
    "notice_banner_border": "#315945",
    "notice_banner_text": "#c0e8ce",
    "history_good": "#94dfb2",
    "history_warning": "#ffc28e",
    "export_link_text": "#bcd6c6",
    "export_link_hover": "#9ce3ba",
    "export_link_border": "#29382f",
    "review_queue_bg": "#2d241d",
    "review_queue_border": "#5a412c",
    "review_queue_text": "#d5baa5",
    # The base layer's :hover for history rows outlives the light skin, which
    # restyles the resting state only — so the shipped app darkens on hover in
    # both appearances.
    "history_hover_bg": "#203228",
    "history_hover_border": "#456552",
}


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints() if QGuiApplication.instance() else None
    scheme = getattr(hints, "colorScheme", None)
    if scheme is None:
        return False
    return str(scheme()).endswith("Dark")


_active: dict[str, str] | None = None


def tokens() -> dict[str, str]:
    global _active
    if _active is None:
        _active = {**(DARK if system_is_dark() else LIGHT), **UNSKINNED}
    return _active


def reset_tokens() -> None:
    """Forget the cached appearance (used by tests and appearance changes)."""
    global _active
    _active = None


def qcolor(value: str) -> QColor:
    value = value.strip()
    if value.startswith("rgba"):
        parts = value[value.index("(") + 1 : value.rindex(")")].split(",")
        r, g, b = (int(part) for part in parts[:3])
        alpha = float(parts[3])
        return QColor(r, g, b, round(alpha * 255))
    return QColor(value)


#: The interface face, mirroring `font-family` in the source `src/styles.css`:
#: ui-sans-serif, -apple-system, BlinkMacSystemFont, "SF Pro Text",
#: "Helvetica Neue", sans-serif. The first three are CSS keywords naming the
#: platform's own UI face rather than a family, so the Qt equivalent is the
#: system font ahead of the same concrete fallbacks.
UI_FONT_FAMILIES = ("SF Pro Text", "SF Pro Display", "Helvetica Neue", "Helvetica", "Arial")


def apply_application_font(app) -> None:
    """Give the interface a face that exists, rather than the platform's default.

    `font()` below inherits the application font, and Qt sets that from whatever
    the platform plugin reports. The offscreen plugin -- which the GUI tests and
    `scripts/verify-release.sh` both run under -- reports "Sans Serif", a family
    present on no Mac, and resolving a missing family makes Qt walk the entire
    font database once, for about 700ms.

    The mono and serif faces were already named after the stylesheet's stacks.
    This is the third, which had been left to the platform.
    """
    available = set(QFontDatabase.families())
    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    preferred = [system, *UI_FONT_FAMILIES] if system in available else list(UI_FONT_FAMILIES)
    # Only the family Qt resolves first has to exist; the rest stay on as the
    # fallback chain they are, exactly as the stylesheet lists them.
    primary = next((family for family in preferred if family in available), "")
    if not primary:
        return
    value = QFont(app.font())
    value.setStyleHint(QFont.StyleHint.SansSerif)
    # setFamilies alone: setFamily() afterwards would reduce the chain to one.
    value.setFamilies([primary, *(family for family in preferred if family != primary)])
    app.setFont(value)


def font(widget: QWidget, size: int, weight: int = 400, letter_spacing: float | None = None, italic: bool = False) -> None:
    """Apply the token typography: system face at CSS-like px sizes/weights."""
    value = QFont(widget.font())
    value.setPixelSize(size)
    value.setWeight(QFont.Weight(max(100, min(900, round(weight / 100) * 100))))
    value.setItalic(italic)
    if letter_spacing is not None:
        value.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    widget.setFont(value)


def mono_font(widget: QWidget, size: int, weight: int = 400) -> None:
    value = QFont("SF Mono")
    value.setStyleHint(QFont.StyleHint.Monospace)
    value.setFamilies(["SF Mono", "SFMono-Regular", "Menlo", "monospace"])
    value.setPixelSize(size)
    value.setWeight(QFont.Weight(max(100, min(900, round(weight / 100) * 100))))
    widget.setFont(value)


def serif_font(widget: QWidget, size: int, weight: int = 400) -> None:
    value = QFont("New York")
    value.setStyleHint(QFont.StyleHint.Serif)
    value.setFamilies(["New York", "Iowan Old Style", "Georgia", "serif"])
    value.setPixelSize(size)
    value.setWeight(QFont.Weight(max(100, min(900, round(weight / 100) * 100))))
    widget.setFont(value)


def drop_shadow(widget: QWidget, blur: int, dy: int, color: str = "rgba(0,0,0,.08)") -> None:
    """The card shadows of the source skin; disabled in dark, exactly as there."""
    if tokens()["shadow"] == "rgba(0,0,0,0)" and not widget.property("keepShadow"):
        return
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(qcolor(color))
    widget.setGraphicsEffect(effect)


def label(text: str, object_name: str = "", size: int = 13, weight: int = 400, color: str = "", letter_spacing: float | None = None) -> QLabel:
    value = QLabel(text)
    if object_name:
        value.setObjectName(object_name)
    font(value, size, weight, letter_spacing)
    if color:
        value.setStyleSheet(f"color: {color}; background: transparent;")
    return value


def _check_glyph_path() -> str:
    """A white check mark PNG for the checkbox indicator, written once per run."""
    import tempfile
    from pathlib import Path as _Path

    from .phosphor import pixmap as _pixmap

    target = _Path(tempfile.gettempdir()) / "philon-check-glyph.png"
    if not target.exists():
        _pixmap("Check", 10, "#ffffff", "bold").save(str(target), "PNG")
    return target.as_posix()


def build_qss() -> str:
    """The application-wide sheet: base surfaces plus every shared control."""
    t = tokens()
    return f"""
    QWidget {{ background: transparent; color: {t['text']}; }}
    QMainWindow, #AppShell {{ background: {t['window_bg']}; }}
    QToolTip {{ color: {t['text']}; background: {t['card_bg']}; border: 1px solid {t['card_border']}; padding: 4px 6px; }}

    QScrollArea {{ border: 0; background: transparent; }}
    QWidget#qt_scrollarea_viewport {{ background: transparent; }}
    QWidget[transparentBg="true"] {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['control_bg_20']}; border-radius: 3px; min-height: 24px; }}
    QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['control_bg_20']}; border-radius: 3px; min-width: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    #AppHeader {{ background: {t['header_bg']}; border-bottom: 1px solid {t['header_border']}; }}
    #SystemStatusChip {{ background: {t['control_bg_12']}; border-radius: 7px; }}
    #SystemStatusChip QLabel {{ background: transparent; color: {t['text_strong']}; }}
    QToolButton#IconButton {{ background: {t['control_bg_12']}; border: 0; border-radius: 7px; }}
    QToolButton#IconButton:hover {{ background: {t['control_bg_20']}; }}

    #Segmented {{ background: {t['control_bg_14']}; border-radius: 8px; }}
    QPushButton[segment="true"] {{ background: transparent; border: 0; border-radius: 6px; color: {t['text_mid']}; padding: 7px 11px; }}
    QPushButton[segment="true"]:hover {{ background: rgba(255,255,255,.55); }}
    QPushButton[segment="true"]:checked {{ background: {t['segment_active_bg']}; color: {t['segment_active_text']}; }}

    QPushButton#PrimaryButton {{ background: {t['accent']}; color: {t['accent_ink']}; border: 0; border-radius: 7px; padding: 9px 13px; }}
    QPushButton#PrimaryButton:hover:enabled {{ background: {t['accent_hover']}; }}
    QPushButton#PrimaryButton:disabled {{ background: {t['accent']}; }}
    QPushButton#SecondaryButton {{ background: {t['accent_soft_09']}; color: {t['accent_text']}; border: 0; border-radius: 7px; padding: 8px 11px; }}
    QPushButton#SecondaryButton:hover:enabled {{ background: {t['accent_soft_16']}; }}
    QPushButton#TextButton {{ background: transparent; color: {t['accent_text']}; border: 0; padding: 0 9px; }}
    QPushButton#TextButton:hover {{ color: {t['accent_text_hover']}; }}
    QPushButton#DangerButton {{ background: {t['danger_bg']}; color: {t['danger_text']}; border: 0; border-radius: 7px; padding: 8px 11px; }}
    QPushButton#DangerButton:hover {{ background: {t['danger_bg_hover']}; }}
    QPushButton#OpenDocumentButton {{ background: transparent; color: {t['accent_text']}; border: 0; border-radius: 6px; padding: 7px 10px; }}
    QPushButton#OpenDocumentButton:hover {{ background: {t['accent_soft_09']}; }}
    QPushButton:disabled {{ color: {t['text_disabled']}; }}

    #ErrorBanner {{ background: {t['error_banner_bg']}; border: 1px solid {t['error_banner_border']}; border-radius: 9px; }}
    #ErrorBanner QLabel {{ color: {t['error_banner_text']}; background: transparent; }}
    #NoticeBanner {{ background: {t['notice_banner_bg']}; border: 1px solid {t['notice_banner_border']}; border-radius: 9px; }}
    #NoticeBanner QLabel {{ color: {t['notice_banner_text']}; background: transparent; }}
    QToolButton#BannerClose {{ background: transparent; border: 0; }}

    #TaskProgress {{ background: {t['task_bg']}; border: 1px solid {t['task_border']}; border-radius: 9px; }}
    #TaskProgress QLabel {{ background: transparent; }}

    #ConversionGrid {{ background: {t['panel_bg']}; border: 1px solid {t['panel_border']}; border-radius: 9px; }}
    #SourcePanel, #OutputPanel {{ background: transparent; border-right: 1px solid {t['panel_divider']}; }}
    #EvidencePanel {{ background: transparent; }}
    #EvidencePanelSurface {{ background: {t['evidence_bg']}; border-top-right-radius: 9px; border-bottom-right-radius: 9px; }}

    #PaperPreview {{ background: {t['paper_frame_bg']}; border: 1px solid {t['paper_frame_ring']}; border-radius: 6px; }}
    #SourceCanvasFrame {{ background: {t['paper_canvas_bg']}; border: 0; }}
    QPushButton[miniControl="true"] {{ background: transparent; border: 0; border-radius: 4px; color: {t['accent_text']}; padding: 0 5px; min-height: 20px; }}
    QPushButton[miniControl="true"]:hover:enabled, QPushButton[miniControl="true"]:checked {{ background: {t['accent_soft_12']}; }}
    QPushButton[miniControl="true"]:disabled {{ color: {t['text_disabled']}; }}
    #MiniControls {{ background: {t['control_bg_10']}; border-radius: 6px; }}
    #MiniControls QLabel {{ background: transparent; color: {t['text_secondary']}; }}

    #OutputContent {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 6px; color: {t['text']}; }}
    #OutputPreviewScroll {{ background: {t['preview_bg']}; border: 1px solid {t['card_border']}; border-radius: 6px; }}
    QPushButton#CopyButton {{ background: transparent; color: {t['accent_text']}; border: 0; border-radius: 7px; padding: 7px 9px; }}
    QPushButton#CopyButton:hover {{ background: {t['accent_soft_08']}; }}
    QPushButton#ExportLink {{ background: transparent; color: {t['accent_text']}; border: 0; padding: 0; }}

    #EvidenceSummary {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 6px; }}
    #EvidenceSummary QLabel {{ background: transparent; }}
    #WarningItem {{ background: {t['warning_item_bg']}; border: 1px solid {t['warning_item_border']}; border-radius: 6px; }}
    #WarningItem QLabel {{ background: transparent; }}
    #VerifiedNote {{ background: {t['verified_bg']}; border: 1px solid {t['verified_border']}; border-radius: 8px; }}
    #VerifiedNote QLabel {{ background: transparent; color: {t['verified_text']}; }}
    QPushButton[blockItem="true"] {{ background: transparent; border: 1px solid transparent; border-radius: 7px; text-align: left; padding: 8px; }}
    QPushButton[blockItem="true"]:hover, QPushButton[blockItem="true"]:checked {{ background: {t['accent_soft_10']}; border-color: {t['accent_soft_18']}; }}
    QPushButton#ReviewButton {{ background: {t['accent_soft_08']}; color: {t['accent_text']}; border: 0; border-radius: 6px; padding: 7px; min-height: 17px; }}
    QPushButton#ReviewButton:hover:enabled {{ background: {t['accent_soft_15']}; color: {t['accent_text_hover']}; }}
    QPushButton#ReviewButtonQuiet {{ background: {t['control_bg_10']}; color: {t['text_secondary']}; border: 0; border-radius: 6px; padding: 7px; min-height: 17px; }}
    QPushButton#ReviewButtonQuiet:hover {{ background: {t['control_bg_16']}; }}
    #RepairModes {{ background: {t['control_bg_10']}; border-radius: 7px; }}
    QPushButton[repairMode="true"] {{ background: transparent; border: 0; border-radius: 5px; color: {t['purple']}; padding: 5px; }}
    QPushButton[repairMode="true"]:hover {{ background: {t['purple_soft_hover']}; color: {t['purple_hover_text']}; }}
    QPushButton#RepairButton {{ background: transparent; color: {t['text_secondary']}; border: 1px solid {t['card_border']}; border-radius: 7px; padding: 10px; text-align: left; }}
    #CandidateItem {{ background: {t['metric_bg']}; border: 1px solid {t['card_border']}; border-radius: 6px; }}
    #CandidateItem QLabel {{ background: transparent; }}
    #CandidateCompare {{ background: {t['compare_bg']}; border: 1px solid {t['compare_border']}; border-radius: 7px; }}
    #CandidateCompare QLabel {{ background: transparent; }}
    #CompareColumn {{ background: rgba(255,255,255,.76); border-radius: 5px; }}
    QPushButton#CandidateLink {{ background: {t['accent_soft_08']}; color: {t['accent_text']}; border: 0; border-radius: 6px; padding: 7px 5px; }}
    #AssetCard {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 6px; }}
    #AssetCard QLabel {{ background: transparent; }}
    #CountPill {{ background: {t['accent_soft_10']}; color: {t['accent_text']}; border-radius: 8px; }}
    #CountPillPurple {{ background: {t['purple_soft']}; color: {t['purple']}; border-radius: 8px; }}

    #QueuePanel, #BatchReport, #SecondaryWorkspace {{ background: {t['panel_bg']}; border: 1px solid {t['panel_border']}; border-radius: 10px; }}
    #QueueItem {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 8px; }}
    #QueueItem QLabel {{ background: transparent; }}
    QPushButton[queueControl="true"] {{ background: transparent; border: 0; border-radius: 5px; color: {t['text_tertiary']}; padding: 0 6px; min-height: 25px; }}
    QPushButton[queueControl="true"]:hover {{ color: #ffd2c0; background: #3a2922; }}
    QPushButton#QueueEmpty, #ReportEmpty, #SecondaryEmpty {{ background: {t['card_bg']}; border: 1px dashed {t['empty_border']}; border-radius: 10px; color: {t['text_secondary']}; }}
    QPushButton#QueueEmpty:hover {{ border-color: {t['accent_text']}; }}
    #ReportEmpty QLabel, #SecondaryEmpty QLabel {{ background: transparent; color: {t['text_secondary']}; }}
    #MetricCard {{ background: {t['metric_bg']}; border: 0; border-radius: 8px; }}
    #MetricCard QLabel {{ background: transparent; }}
    QPushButton#ExportListLink {{ background: transparent; border: 0; border-bottom: 1px solid {t['export_link_border']}; color: {t['export_link_text']}; text-align: left; padding: 9px 0; }}
    QPushButton#ExportListLink:hover {{ color: {t['export_link_hover']}; }}

    QPushButton#HistoryItem {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 8px; text-align: left; padding: 14px; }}
    QPushButton#HistoryItem:hover {{ background: {t['history_hover_bg']}; border-color: {t['history_hover_border']}; }}
    #ReviewQueueCard {{ background: {t['review_queue_bg']}; border: 1px solid {t['review_queue_border']}; border-radius: 9px; }}
    #ReviewQueueCard QLabel {{ background: transparent; }}
    #DiagnosticCard {{ background: {t['diagnostic_bg']}; border: 1px solid {t['diagnostic_border']}; border-radius: 8px; }}
    #DiagnosticCard QLabel {{ background: transparent; }}
    #SettingsCard {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 8px; }}
    #SettingsCard QLabel {{ background: transparent; }}
    #ModelPack {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 8px; }}
    #ModelPack QLabel {{ background: transparent; }}
    QPushButton#ModelToggle {{ background: {t['accent_soft_09']}; color: {t['accent_text']}; border: 0; border-radius: 6px; padding: 7px 9px; min-width: 86px; }}
    QPushButton#ModelToggle:hover:enabled {{ background: {t['accent_soft_16']}; }}
    QPushButton#ModelToggle:checked {{ background: {t['accent']}; color: {t['accent_ink']}; }}
    QPushButton#ModelToggle:disabled {{ background: {t['toggle_disabled_bg']}; color: {t['text_tertiary']}; }}

    #SettingsSection {{ background: {t['card_bg']}; border: 1px solid {t['card_border']}; border-radius: 9px; }}
    #SettingsSection QLabel {{ background: transparent; }}
    #PreferenceRow {{ background: transparent; }}
    #PreferenceRowDivider {{ background: {t['card_border']}; border: 0; max-height: 1px; min-height: 1px; }}
    QComboBox {{ background: {t['field_bg']}; color: {t['text']}; border: 1px solid {t['field_border']}; border-radius: 6px; padding: 6px 26px 6px 9px; min-width: 138px; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {t['card_bg']}; color: {t['text']}; border: 1px solid {t['card_border']}; selection-background-color: {t['accent']}; selection-color: {t['accent_ink']}; }}
    QCheckBox {{ color: {t['text_strong']}; background: transparent; spacing: 7px; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t['field_border']}; border-radius: 3px; background: {t['card_bg']}; }}
    QCheckBox::indicator:checked {{ background: {t['accent_text']}; border-color: {t['accent_text']}; image: url("{_check_glyph_path()}"); }}
    """
