"""Secondary workspaces: Library, Models, and Diagnostics cards."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import theme
from .phosphor import Icon
from .widgets import clear_layout, eyebrow, hbox, make_button, transparent, vbox
from .qt import QFrame, QPushButton, QScrollArea, QWidget, Qt, Signal


class SecondaryWorkspace(QWidget):
    """The centered card every secondary view lives in."""

    def __init__(self, max_width: int = 980) -> None:
        super().__init__()
        outer = hbox(self, (0, 0, 0, 0), 0)
        outer.addStretch(1)
        self.card = QFrame()
        self.card.setObjectName("SecondaryWorkspace")
        self.card.setMaximumWidth(max_width)
        self.card.setMinimumHeight(440)
        theme.drop_shadow(self.card, 40, 12, "rgba(0,0,0,.05)")
        card_column = vbox(spacing=0)
        card_column.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
        card_column.addStretch(1)
        # The side stretches only absorb what the max-width card cannot use,
        # exactly like the source layout's `margin: 0 auto; max-width`.
        outer.addLayout(card_column, 1000)
        outer.addStretch(1)
        self.card.setMinimumWidth(min(max_width, 680))
        self.card_layout = vbox(self.card, (25, 25, 25, 25), 0)

    def clear(self) -> None:
        clear_layout(self.card_layout)

    def heading(self, eyebrow_text: str, eyebrow_icon: str, title: str, subtitle: str, actions: list[QPushButton]) -> None:
        t = theme.tokens()
        row = hbox(spacing=22)
        text_column = vbox(spacing=0)
        text_column.addWidget(eyebrow(eyebrow_text, eyebrow_icon, icon_color=t["text_tertiary"]))
        text_column.addSpacing(6)
        text_column.addWidget(theme.label(title, size=22, weight=650, color=t["text"], letter_spacing=-0.75))
        text_column.addSpacing(6)
        subtitle_label = theme.label(subtitle, size=13, color=t["text_secondary"])
        subtitle_label.setWordWrap(True)
        subtitle_label.setMaximumWidth(600)
        text_column.addWidget(subtitle_label)
        row.addLayout(text_column, 1)
        actions_row = hbox(spacing=8)
        for action in actions:
            actions_row.addWidget(action)
        row.addLayout(actions_row)
        self.card_layout.addLayout(row)
        self.card_layout.addSpacing(22)
        divider = QFrame()
        divider.setObjectName("PreferenceRowDivider")
        divider.setStyleSheet(f"background: {t['panel_divider']};")
        self.card_layout.addWidget(divider)

    def empty_state(self, icon_name: str, message: str) -> None:
        t = theme.tokens()
        empty = QFrame()
        empty.setObjectName("SecondaryEmpty")
        empty.setMinimumHeight(250)
        layout = vbox(empty, (30, 30, 30, 30), 9)
        layout.addStretch(1)
        layout.addWidget(Icon(icon_name, 30, t["accent_text"], "thin"), 0, Qt.AlignmentFlag.AlignHCenter)
        note = theme.label(message, size=13, color=t["text_secondary"])
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(note)
        layout.addStretch(1)
        self.card_layout.addSpacing(20)
        self.card_layout.addWidget(empty)
        self.card_layout.addStretch(1)

    def scrolling_list(self) -> tuple[QScrollArea, Any]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = transparent(QWidget())
        layout = vbox(content, spacing=8)
        scroll.setWidget(content)
        return scroll, layout





def format_timestamp(value: str) -> str:
    """A conversion's own recorded time, as the local machine would write it.

    A timestamp that will not parse is shown exactly as it was stored, since
    that is what Philon actually holds. An absent one is named as absent rather
    than left as a blank in the row: a record with no time is a fact about the
    record, and an empty string in front of the profile reads as a rendering
    fault instead.
    """
    if not value:
        return "Date not recorded"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%-m/%-d/%Y, %-I:%M:%S %p")
    except ValueError:
        return value


class LibraryView(SecondaryWorkspace):
    refresh_requested = Signal()
    clean_requested = Signal()
    job_opened = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.history: list[dict[str, Any]] = []
        self.clean_pending = False
        self.rebuild()

    def set_history(self, history: list[dict[str, Any]]) -> None:
        self.history = history
        self.clean_pending = False
        self.rebuild()

    def rebuild(self) -> None:
        t = theme.tokens()
        self.clear()
        actions: list[QPushButton] = []
        if self.clean_pending:
            cancel = make_button("Cancel", "TextButton", font_size=12, font_weight=500)
            cancel.clicked.connect(self._cancel_clean)
            actions.append(cancel)
            count = len(self.history)
            confirm = make_button(f"Remove {count} job{'' if count == 1 else 's'}", "DangerButton", font_size=12)
            confirm.clicked.connect(self.clean_requested.emit)
            actions.append(confirm)
        else:
            clean = make_button("Clean", "SecondaryButton", font_size=13)
            clean.setEnabled(bool(self.history))
            clean.clicked.connect(self._request_clean)
            actions.append(clean)
        refresh = make_button("Refresh", "SecondaryButton", "ArrowClockwise", 16)
        refresh.clicked.connect(self.refresh_requested.emit)
        actions.append(refresh)
        self.heading("Library", "Archive", "Recent local conversions", "History is stored in Philon’s local SQLite database on this Mac.", actions)
        if not self.history:
            self.empty_state("Archive", "No local conversion history yet.")
            return
        self.card_layout.addSpacing(20)
        scroll, list_layout = self.scrolling_list()
        for job in self.history:
            list_layout.addWidget(self._history_item(job))
        list_layout.addStretch(1)
        self.card_layout.addWidget(scroll, 1)
        total_warnings = sum(int(job.get("warnings", 0)) for job in self.history)
        if total_warnings:
            review = QFrame()
            review.setObjectName("ReviewQueueCard")
            review_layout = vbox(review, (15, 15, 15, 15), 7)
            review_layout.addWidget(eyebrow("Cross-document review", "WarningCircle", color=t["history_warning"], icon_color=t["history_warning"]))
            body = theme.label(
                f"{total_warnings} warning{'' if total_warnings == 1 else 's'} remain across locally retained conversions. Select a job above to inspect its evidence.",
                size=12, color=t["review_queue_text"])
            body.setWordWrap(True)
            review_layout.addWidget(body)
            self.card_layout.addSpacing(16)
            self.card_layout.addWidget(review)

    def _request_clean(self) -> None:
        self.clean_pending = True
        self.rebuild()

    def _cancel_clean(self) -> None:
        self.clean_pending = False
        self.rebuild()

    def _history_item(self, job: dict[str, Any]) -> QPushButton:
        t = theme.tokens()
        row = QPushButton()
        row.setObjectName("HistoryItem")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = hbox(row, (14, 14, 14, 14), 13)
        text_column = vbox(spacing=4)
        documents = int(job.get("documents", 0))
        text_column.addWidget(theme.label(f"{documents} document{'' if documents == 1 else 's'}", size=13, weight=650, color=t["text"]))
        text_column.addWidget(theme.label(f"{format_timestamp(str(job.get('created_at', '')))} · {job.get('profile', '')}", size=11, color=t["text_secondary"]))
        layout.addLayout(text_column, 1)
        warnings = int(job.get("warnings", 0))
        status = theme.label(f"{warnings} warnings" if warnings else "Verified", size=11, weight=700, color=t["history_warning"] if warnings else t["history_good"])
        layout.addWidget(status)
        row.setMinimumHeight(layout.sizeHint().height())
        row.clicked.connect(lambda: self.job_opened.emit(str(job.get("id"))))
        return row


MODEL_STATUS = {
    "ready": "Ready locally",
    "incomplete": "Runtime incomplete",
    "probe-required": "Manual probe required",
    "blocked": "Blocked by policy",
    "not-found": "Not found locally",
    "unavailable": "Unavailable",
}


def pack_download_label(pack: dict[str, Any]) -> str | None:
    """What a pack would cost to fetch, from the manifest and never from a host."""
    if not pack.get("downloadable"):
        return None
    total = int(pack.get("download_bytes") or 0)
    size = f"{total / 1024 ** 3:.1f} GB" if total >= 1024 ** 3 else f"{max(1, round(total / 1024 ** 2))} MB"
    return f"{size}, checked against a SHA-256" if pack.get("download_verified") else f"{size}, no digest declared"


def model_setup_summary(packs: list[dict[str, Any]]) -> str:
    """One line on what a first run found, and what it would have to fetch.

    Counts only packs a person can act on, so built-in runtimes and the packs
    policy blocks pad neither half.
    """
    optional = [pack for pack in packs if not pack.get("required") and pack.get("approved")]
    if not optional:
        return "No optional model packs are approved for this build."
    present = sum(1 for pack in optional if pack.get("available_locally"))
    fetchable = sum(1 for pack in optional if not pack.get("available_locally") and pack.get("downloadable"))
    if not fetchable:
        return f"{present} of {len(optional)} approved packs are already on this machine."
    return (f"{present} of {len(optional)} approved packs are already on this machine; "
            f"{fetchable} can be downloaded.")


class ModelsView(SecondaryWorkspace):
    refresh_requested = Signal()
    toggle_requested = Signal(str, bool)
    download_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.packs: list[dict[str, Any]] = []
        self.enabled_ids: list[str] = []
        self.rebuild()

    def set_packs(self, packs: list[dict[str, Any]], enabled_ids: list[str]) -> None:
        self.packs, self.enabled_ids = packs, enabled_ids
        self.rebuild()

    def rebuild(self) -> None:
        t = theme.tokens()
        self.clear()
        refresh = make_button("Refresh", "SecondaryButton", "ArrowClockwise", 16)
        refresh.clicked.connect(self.refresh_requested.emit)
        self.heading(
            "Models", "MagicWand", "Local model access",
            "Optional models stay off until you enable them. Philon never uploads a model or your "
            "document. A pack you ask for is downloaded from its publisher over HTTPS and installed "
            "only if every file matches the SHA-256 recorded in Philon's manifest. "
            + model_setup_summary(self.packs),
            [refresh],
        )
        if not self.packs:
            self.empty_state("MagicWand", "Inspect local model availability.")
            return
        self.card_layout.addSpacing(20)
        scroll, list_layout = self.scrolling_list()
        for pack in self.packs:
            list_layout.addWidget(self._pack_row(pack))
        list_layout.addStretch(1)
        self.card_layout.addWidget(scroll, 1)

    def _pack_row(self, pack: dict[str, Any]) -> QFrame:
        t = theme.tokens()
        row = QFrame()
        row.setObjectName("ModelPack")
        layout = hbox(row, (14, 14, 14, 14), 13)
        layout.addWidget(Icon("ShieldCheck", 20, t["accent_text"]), 0, Qt.AlignmentFlag.AlignTop)
        text_column = vbox(spacing=4)
        text_column.addWidget(theme.label(str(pack.get("id", "")).replace("-", " ").title(), size=13, weight=650, color=t["text"]))
        role = theme.label(f"{pack.get('role', '')} · {pack.get('runtime', '')}", size=11, color=t["text_secondary"])
        role.setWordWrap(True)
        text_column.addWidget(role)
        licence = theme.label(str(pack.get("license", "")), size=10, color=t["text_tertiary"])
        licence.setWordWrap(True)
        text_column.addWidget(licence)
        download_label = pack_download_label(pack)
        if download_label and not pack.get("available_locally"):
            note = theme.label(f"Download {download_label}", size=10, color=t["text_tertiary"])
            note.setWordWrap(True)
            text_column.addWidget(note)
        if pack.get("managed"):
            note = theme.label("Installed by Philon into its own model store", size=10, color=t["text_tertiary"])
            note.setWordWrap(True)
            text_column.addWidget(note)
        for diagnostic in pack.get("diagnostics") or []:
            note = theme.label(str(diagnostic), size=10, color=t["text_tertiary"])
            note.setWordWrap(True)
            text_column.addWidget(note)
        layout.addLayout(text_column, 1)
        readiness = str(pack.get("readiness") or "")
        if pack.get("approved") and not pack.get("required") and pack.get("downloadable") and not pack.get("available_locally"):
            download = QPushButton("Download")
            download.setObjectName("ModelToggle")
            download.setCursor(Qt.CursorShape.PointingHandCursor)
            theme.font(download, 11, 700)
            download.clicked.connect(lambda _=False, pack_id=str(pack.get("id")): self.download_requested.emit(pack_id))
            layout.addWidget(download)
        if pack.get("managed"):
            remove = QPushButton("Remove")
            remove.setObjectName("ModelToggle")
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            theme.font(remove, 11, 700)
            remove.clicked.connect(lambda _=False, pack_id=str(pack.get("id")): self.remove_requested.emit(pack_id))
            layout.addWidget(remove)
        if pack.get("required"):
            layout.addWidget(theme.label("Built in", size=11, weight=650, color=t["text_secondary"]))
        else:
            enabled = str(pack.get("id")) in self.enabled_ids
            can_enable = bool(pack.get("approved") and not pack.get("required") and readiness in ("ready", "probe-required"))
            status = MODEL_STATUS.get(readiness, "Available locally" if pack.get("available_locally") else "Not installed")
            toggle = QPushButton("Enabled" if enabled else ("Enable" if can_enable else status))
            toggle.setObjectName("ModelToggle")
            toggle.setCheckable(True)
            toggle.setChecked(enabled)
            toggle.setEnabled(can_enable)
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            theme.font(toggle, 11, 700)
            toggle.clicked.connect(lambda _=False, pack_id=str(pack.get("id")), state=not enabled: self.toggle_requested.emit(pack_id, state))
            layout.addWidget(toggle)
        return row


class DiagnosticsView(SecondaryWorkspace):
    check_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.health: str | None = None
        self.document: dict[str, Any] | None = None
        self.rebuild()

    def set_state(self, health: str | None, document: dict[str, Any] | None) -> None:
        self.health, self.document = health, document
        self.rebuild()

    def rebuild(self) -> None:
        t = theme.tokens()
        self.clear()
        run = make_button("Run check", "SecondaryButton", "Play", 16, icon_weight="fill")
        run.clicked.connect(self.check_requested.emit)
        self.heading("Diagnostics", "Gauge", "Inspect the local runtime", "Diagnostics checks the bundled local engine only. It makes no network request.", [run])
        self.card_layout.addSpacing(21)
        card = QFrame()
        card.setObjectName("DiagnosticCard")
        card_layout = hbox(card, (20, 20, 20, 20), 14)
        card_layout.addWidget(Icon("ShieldCheck", 24, t["accent_text"], "fill"), 0, Qt.AlignmentFlag.AlignTop)
        text_column = vbox(spacing=5)
        text_column.addWidget(theme.label("Engine ready" if self.health else "Awaiting check", size=14, weight=650, color=t["text"]))
        body = theme.label(self.health or "Run a local check to verify the bundled engine bridge and available review actions.", size=12, color=t["diagnostic_body"])
        body.setWordWrap(True)
        body.setMaximumWidth(650)
        text_column.addWidget(body)
        card_layout.addLayout(text_column, 1)
        self.card_layout.addWidget(card)
        report = (self.document or {}).get("evidence_report")
        if report:
            self.card_layout.addSpacing(20)
            scroll, list_layout = self.scrolling_list()
            summary = report.get("summary", {})
            confidence = round(float(summary.get("average_block_confidence", 0)) * 100)
            warnings = int(summary.get("warnings", 0))
            list_layout.addWidget(self._finding_row(
                "Gauge", "Current conversion evidence",
                f"{summary.get('pages', 0)} pages · {summary.get('blocks', 0)} blocks · {confidence}% average confidence",
                f"{warnings} warnings" if warnings else "No warnings"))
            for finding in (report.get("accessibility", {}).get("findings") or []):
                list_layout.addWidget(self._finding_row("ShieldCheck", str(finding.get("rule", "")).replace("-", " "), str(finding.get("message", "")), str(finding.get("status", ""))))
            list_layout.addStretch(1)
            self.card_layout.addWidget(scroll, 1)
        else:
            self.card_layout.addStretch(1)

    def _finding_row(self, icon_name: str, title: str, message: str, status: str) -> QFrame:
        t = theme.tokens()
        row = QFrame()
        row.setObjectName("SettingsCard")
        layout = hbox(row, (14, 14, 14, 14), 13)
        layout.addWidget(Icon(icon_name, 20, t["accent_text"]), 0, Qt.AlignmentFlag.AlignTop)
        text_column = vbox(spacing=4)
        text_column.addWidget(theme.label(title, size=13, weight=650, color=t["text"]))
        body = theme.label(message, size=11, color=t["text_secondary"])
        body.setWordWrap(True)
        text_column.addWidget(body)
        layout.addLayout(text_column, 1)
        layout.addWidget(theme.label(status, size=11, weight=700, color=t["accent_text"]))
        return row
