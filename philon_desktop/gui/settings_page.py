"""The Settings workspace: profile, cache, outputs, and the assurances list."""

from __future__ import annotations

from typing import Any

from . import theme
from .phosphor import Icon
from .secondary_pages import SecondaryWorkspace
from .widgets import hbox, make_button, transparent, vbox
from .qt import QCheckBox, QComboBox, QFrame, QGridLayout, QScrollArea, QWidget, Qt, Signal

PROFILES = ("Fast", "Balanced", "Verified")
OUTPUT_LABELS = {
    "machine": "Machine-ready folder",
    "markdown": "Clean reading Markdown",
    "html": "Presentation HTML",
    "ir": "Philon IR",
    "page_tree": "Page tree JSON (interchange)",
    "chunks": "RAG chunks",
    "evidence": "Evidence report",
    "table_csv": "Table CSV",
    "assets": "Source images and previews",
    "manifest": "Output manifest",
}
CACHE_LABELS = (("use", "Use cache"), ("refresh", "Refresh cache"), ("bypass", "Bypass cache"))
ASSURANCES = (
    ("CloudSlash", "Local-only conversion", "Cloud providers are not configured.", "Enabled"),
    ("ShieldCheck", "Evidence retention", "Warnings, routing decisions, alternatives, and reviews remain exportable.", "Enabled"),
    ("MagicWand", "Manual model repair", "Only runs with an enabled local model for a selected source crop.", "Local"),
)


class SettingsView(SecondaryWorkspace):
    preferences_changed = Signal(dict)
    defaults_restored = Signal()

    def __init__(self) -> None:
        super().__init__(max_width=780)
        self.preferences: dict[str, Any] = {}
        self.rebuild()

    def set_preferences(self, preferences: dict[str, Any]) -> None:
        self.preferences = dict(preferences)
        self.rebuild()

    def rebuild(self) -> None:
        t = theme.tokens()
        self.clear()
        restore = make_button("Restore defaults", "SecondaryButton", font_size=13)
        restore.clicked.connect(self.defaults_restored.emit)
        self.heading("Settings", "GearSix", "Conversion preferences", "Choose the default quality and the files Philon creates for every conversion.", [restore])
        self.card_layout.addSpacing(18)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = transparent(QWidget())
        form = vbox(content, spacing=16)

        rows_section = QFrame()
        rows_section.setObjectName("SettingsSection")
        rows_layout = vbox(rows_section, (0, 0, 0, 0), 0)
        rows_layout.addWidget(self._preference_row(
            "Default profile", "Applied when you next open or start a conversion.",
            list(PROFILES), str(self.preferences.get("profile", "Balanced")),
            lambda value: self._update({"profile": value})))
        divider = QFrame()
        divider.setObjectName("PreferenceRowDivider")
        rows_layout.addWidget(divider)
        cache_value = str(self.preferences.get("cache_policy", "use"))
        cache_label = dict(CACHE_LABELS).get(cache_value, "Use cache")
        rows_layout.addWidget(self._preference_row(
            "Cache behavior", "Use prior local results, refresh them, or bypass the cache entirely.",
            [label for _, label in CACHE_LABELS], cache_label,
            lambda value: self._update({"cache_policy": next(key for key, label in CACHE_LABELS if label == value)})))
        form.addWidget(rows_section)

        outputs_section = QFrame()
        outputs_section.setObjectName("SettingsSection")
        outputs_layout = vbox(outputs_section, (18, 18, 18, 18), 0)
        outputs_layout.addWidget(theme.label("Output formats", size=14, weight=650, color=t["text"]))
        outputs_layout.addSpacing(4)
        outputs_layout.addWidget(theme.label("Choose the files saved for new conversions.", size=12, color=t["text_secondary"]))
        outputs_layout.addSpacing(14)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(9)
        selected = list(self.preferences.get("outputs") or [])
        for index, (key, label_text) in enumerate(OUTPUT_LABELS.items()):
            check = QCheckBox(label_text)
            theme.font(check, 12)
            check.setChecked(key in selected)
            check.toggled.connect(lambda checked, value=key: self._toggle_output(value, checked))
            grid.addWidget(check, index // 2, index % 2)
        outputs_layout.addLayout(grid)
        form.addWidget(outputs_section)

        assurances_section = QFrame()
        assurances_section.setObjectName("SettingsSection")
        assurances_layout = vbox(assurances_section, (0, 0, 0, 0), 0)
        for index, (icon_name, title, message, badge) in enumerate(ASSURANCES):
            if index:
                row_divider = QFrame()
                row_divider.setObjectName("PreferenceRowDivider")
                assurances_layout.addWidget(row_divider)
            row = QWidget()
            row_layout = hbox(row, (16, 13, 16, 13), 10)
            row_layout.addWidget(Icon(icon_name, 20, t["accent_text"]))
            text_column = vbox(spacing=2)
            text_column.addWidget(theme.label(title, size=12, weight=650, color=t["text"]))
            text_column.addWidget(theme.label(message, size=11, color=t["text_secondary"]))
            row_layout.addLayout(text_column, 1)
            row_layout.addWidget(theme.label(badge, size=11, weight=700, color=t["accent_text"]))
            assurances_layout.addWidget(row)
        form.addWidget(assurances_section)
        form.addStretch(1)
        scroll.setWidget(content)
        scroll.setMinimumHeight(content.sizeHint().height() + 4)
        self.card_layout.addWidget(scroll, 1)

    def _preference_row(self, title: str, note: str, options: list[str], current: str, on_change: Any) -> QWidget:
        t = theme.tokens()
        row = QWidget()
        row.setObjectName("PreferenceRow")
        layout = hbox(row, (16, 15, 16, 15), 24)
        text_column = vbox(spacing=3)
        text_column.addWidget(theme.label(title, size=13, weight=650, color=t["text"]))
        text_column.addWidget(theme.label(note, size=11, color=t["text_secondary"]))
        layout.addLayout(text_column, 1)
        select = QComboBox()
        theme.font(select, 12)
        select.addItems(options)
        select.setCurrentText(current)
        select.currentTextChanged.connect(on_change)
        layout.addWidget(select)
        return row

    def _toggle_output(self, key: str, checked: bool) -> None:
        outputs = list(self.preferences.get("outputs") or [])
        next_outputs = [item for item in outputs if item != key] + ([key] if checked else [])
        if next_outputs:
            ordered = [item for item in OUTPUT_LABELS if item in next_outputs]
            self._update({"outputs": ordered})
        else:
            # An empty output set is refused, exactly as the source app refuses it.
            self.rebuild()

    def _update(self, changes: dict[str, Any]) -> None:
        self.preferences = {**self.preferences, **changes}
        self.preferences_changed.emit(dict(self.preferences))
