"""The ported shell renders the source application's structure and behavior.

Runs off-screen with an isolated data directory and constructs no window.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_data_dir = tempfile.mkdtemp(prefix="philon-gui-test-")
os.environ["PHILON_DATA_DIR"] = _data_dir

from philon_desktop.gui.qt import QApplication  # noqa: E402
from philon_desktop.gui import theme  # noqa: E402

_app = QApplication.instance() or QApplication([])
theme.apply_application_font(_app)
theme.reset_tokens()

from philon_desktop.core import PhilonService  # noqa: E402
from philon_desktop.gui import about  # noqa: E402
from philon_desktop.gui.evidence_panel import changed_token_count, confidence_label  # noqa: E402
from philon_desktop.gui.main_window import MainWindow  # noqa: E402
from philon_desktop.gui.output_panel import markdown_body, table_rows  # noqa: E402
from philon_desktop.gui.splash import SPLASH_SEEN_KEY, Splash  # noqa: E402
from philon_desktop.gui.workers import wait_for_workers  # noqa: E402


def sample_document() -> dict:
    return {
        "id": "doc-1",
        "source_path": "/tmp/sample.pdf",
        "cache_hit": False,
        "pages": [{"id": "page-1", "number": 1, "width": 612, "height": 792, "route": {"decision": "native-text"}}],
        "blocks": [
            {
                "id": "block-1", "page": "page-1", "type": "heading", "level": 1, "text": "A Study of Readings",
                "source": {"method": "native-text", "confidence": 0.97},
                "bbox": {"x0": 40, "y0": 700, "x1": 570, "y1": 760, "coordinate_space": "pdf-page-points"},
                "evidence": {"alternatives": [{"kind": "vision-alternative", "text": "A Study of Reading", "selected": False}]},
            },
            {
                "id": "block-2", "page": "page-1", "type": "paragraph", "text": "Body text of the study.",
                "source": {"method": "native-text", "confidence": 0.91}, "evidence": {"alternatives": []},
            },
        ],
        "warnings": [{"code": "LOW_CONFIDENCE", "message": "One region needs review.", "severity": "warning"}],
        "outputs": {"ir": str(Path(_data_dir) / "missing-ir.json"), "assets": []},
    }


class ShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = PhilonService(Path(_data_dir))
        cls.window = MainWindow(cls.service)
        wait_for_workers()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        wait_for_workers()

    def test_the_five_views_of_the_source_app_exist(self):
        self.assertEqual(self.window.views.count(), 5)
        self.assertEqual(self.window.view_names, ["workspace", "library", "models", "diagnostics", "settings"])
        self.assertEqual(set(self.window.main_tabs), {"workspace", "library", "models", "diagnostics", "settings"})

    def test_first_launch_shows_the_splash_and_dismissal_records_it(self):
        self.assertIsInstance(self.window.overlay, Splash)
        self.window.overlay.dismissed.emit()
        _app.processEvents()
        self.assertIsNone(self.window.overlay)
        self.assertEqual(self.service.store.setting(SPLASH_SEEN_KEY), about.VERSION)

    def test_command_bar_is_only_part_of_the_workspace_view(self):
        self.window.show_view("settings")
        wait_for_workers()
        self.assertFalse(self.window.command_bar.isVisibleTo(self.window))
        self.window.show_view("workspace")
        self.assertTrue(self.window.command_bar.isVisibleTo(self.window))

    def test_job_tabs_switch_between_grid_and_batch(self):
        self.window.job_tabs.select("Batch")
        self.assertEqual(self.window.workspace_stack.currentIndex(), 1)
        self.window.job_tabs.select("Single Job")
        self.assertEqual(self.window.workspace_stack.currentIndex(), 0)

    def test_a_document_renders_across_all_three_panels(self):
        self.window.result = {"results": [sample_document()], "failures": []}
        self.window.selected_block_id = "block-1"
        self.window.single_path = "/tmp/sample.pdf"
        self.window._refresh_workspace()
        self.assertEqual(self.window.source_panel.title.full_text(), "sample.pdf")
        self.assertEqual(self.window.output_panel.footer_note.text(), "2 evidence-linked blocks")
        self.assertEqual(len(self.window.output_panel.rendered.blocks), 2)
        self.assertTrue(self.window.export_button.isVisibleTo(self.window))
        self.window.output_panel.set_format("IR")
        self.assertIn('"block-1"', self.window.output_panel.text_output.toPlainText())
        self.window.output_panel.set_format("Preview")

    def test_selecting_a_block_synchronizes_the_panels(self):
        self.window._select_block("block-2")
        self.assertEqual(self.window.selected_block_id, "block-2")
        selected = [block_id for block_id, view in self.window.output_panel.rendered.blocks.items() if view.selected]
        self.assertEqual(selected, ["block-2"])

    def test_preferences_round_trip_including_enabled_models(self):
        self.window._save_preferences({**self.window.preferences, "profile": "Verified", "enabled_model_ids": ["qwen3.8-27b-local-repair"]})
        stored = self.service.preferences()
        self.assertEqual(stored["profile"], "Verified")
        self.assertEqual(stored["enabled_model_ids"], ["qwen3.8-27b-local-repair"])
        self.assertEqual(self.window.profile, "Verified")
        self.window._restore_defaults()
        self.assertEqual(self.service.preferences()["profile"], "Balanced")

    def test_state_survives_an_appearance_rebuild(self):
        self.window.result = {"results": [sample_document()], "failures": []}
        self.window.selected_block_id = "block-2"
        self.window.single_path = "/tmp/sample.pdf"
        self.window.job_tabs.select("Single Job", announce=False)
        self.window.output_panel.set_format("Markdown")
        self.window.show_view("workspace")
        state = self.window.export_state()
        rebuilt = MainWindow(self.service)
        rebuilt.adopt_state(state)
        wait_for_workers()
        _app.processEvents()
        try:
            self.assertEqual(rebuilt.single_path, "/tmp/sample.pdf")
            self.assertEqual(rebuilt.selected_block_id, "block-2")
            self.assertEqual(rebuilt.output_panel.format, "Markdown")
            self.assertEqual(rebuilt.job_tabs.value(), "Single Job")
            self.assertEqual(rebuilt.views.currentIndex(), 0)
        finally:
            self.window.output_panel.set_format("Preview")
            rebuilt.deleteLater()
            _app.processEvents()

    def test_library_count_badge_follows_history(self):
        self.window._history_loaded([{"id": "j1", "created_at": "2026-08-21T10:00:00+00:00", "profile": "Balanced", "documents": 1, "warnings": 2}])
        self.assertEqual(self.window.main_tabs["library"].count, 1)
        self.window._history_loaded([])
        self.assertEqual(self.window.main_tabs["library"].count, 0)


class PortedLogicTest(unittest.TestCase):
    def test_markdown_body_matches_the_source_composition(self):
        document = {"blocks": [
            {"type": "heading", "level": 2, "text": "Title"},
            {"type": "formula", "text": "x = 1"},
            {"type": "paragraph", "text": "Body."},
        ]}
        self.assertEqual(markdown_body(document), "## Title\n\n```text\nx = 1\n```\n\nBody.")

    def test_table_rows_drops_the_divider_row(self):
        rows = table_rows("| a | b |\n| --- | :---: |\n| 1 | 2 |")
        self.assertEqual(rows, [["a", "b"], ["1", "2"]])

    def test_changed_token_count_matches_the_source_algorithm(self):
        self.assertEqual(changed_token_count("the quick fox", "the quick fox"), 0)
        self.assertEqual(changed_token_count("the quick fox", "the slow fox"), 1)
        self.assertEqual(changed_token_count("", "three new tokens"), 3)

    def test_confidence_labels_match_the_source_thresholds(self):
        self.assertEqual(confidence_label(0.95), "High confidence")
        self.assertEqual(confidence_label(0.8), "Review suggested")
        self.assertEqual(confidence_label(0.5), "Needs review")

    def test_about_text_carries_the_promises(self):
        self.assertIn("Philon of Alexandria", about.ABOUT)
        self.assertIn("never sends a document", about.LEGAL)
        self.assertIn("Phosphor", about.LEGAL)
        self.assertIn("PySide6", about.LEGAL)
        self.assertTrue(about.VERSION)


if __name__ == "__main__":
    unittest.main()
