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

from philon_desktop.gui.qt import QApplication, QPushButton  # noqa: E402
from philon_desktop.gui import theme  # noqa: E402

_app = QApplication.instance() or QApplication([])
theme.apply_application_font(_app)
theme.reset_tokens()

from philon_desktop.core import PhilonService  # noqa: E402
from philon_desktop.gui import about  # noqa: E402
from philon_desktop.gui.evidence_panel import EvidencePanel, changed_token_count, confidence_label  # noqa: E402
from philon_desktop.gui.main_window import MainWindow  # noqa: E402
from philon_desktop.gui.output_panel import markdown_body, table_rows  # noqa: E402
from philon_desktop.gui.batch_view import bytes_label  # noqa: E402
from philon_desktop.gui.secondary_pages import (  # noqa: E402
    LibraryView, ModelsView, format_timestamp, model_setup_summary)
from philon_desktop.gui.splash import Splash  # noqa: E402
from philon_desktop.gui.workers import wait_for_workers  # noqa: E402


def sample_document() -> dict:
    return {
        "id": "doc-1",
        "source_path": "/tmp/sample.pdf",
        "cache_hit": False,
        "pages": [{"id": "page-1", "number": 1, "width": 612, "height": 792, "rotation": 90, "route": {"decision": "native-text"},
                   "ruled_tables": [{"bbox": None, "row_count": 3, "column_count": 3, "complete": True,
                                     "recoverable": True, "crossing_count": 16}]}],
        "blocks": [
            {
                "id": "block-1", "page": "page-1", "type": "heading", "level": 1, "text": "A Study of Readings",
                "source": {"method": "native-text", "confidence": 0.97},
                "bbox": {"x0": 40, "y0": 700, "x1": 570, "y1": 760, "coordinate_space": "pdf-page-points"},
                "links": [{"uri": "https://example.test/paper", "text": "A Study", "bbox": None},
                          {"uri": "javascript:alert(1)", "text": "of Readings", "bbox": None}],
                "evidence": {"alternatives": [{"kind": "vision-alternative", "text": "A Study of Reading", "selected": False}]},
            },
            {
                "id": "block-2", "page": "page-1", "type": "paragraph", "text": "Body text of the study.",
                "source": {"method": "native-text", "confidence": 0.91}, "evidence": {"alternatives": []},
            },
            {
                "id": "block-3", "page": "page-1", "type": "table", "text": "Region Q1\nNorth 14",
                "source": {"method": "native-text", "confidence": 0.99}, "evidence": {"alternatives": []},
                "table": {"rows": [["Region", "Q1"], ["North", "14"]], "row_count": 2, "column_count": 2,
                          "source": "ruled-geometry"},
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

    def test_every_launch_shows_the_splash_and_records_nothing(self):
        """It was once per version. The owner asked for it on every launch.

        Nothing is written down any more, because nothing is being decided: the
        screen carries what Philon is, what it refuses to do and the sources
        behind it, and that is worth meeting each time rather than once.
        """
        self.assertIsInstance(self.window.overlay, Splash)
        self.window.overlay.dismissed.emit()
        _app.processEvents()
        self.assertIsNone(self.window.overlay)
        # A second window, same store, same everything: it opens again.
        second = MainWindow(self.service)
        try:
            self.assertIsInstance(second.overlay, Splash)
        finally:
            second.close()

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
        self.assertEqual(self.window.output_panel.footer_note.text(), "3 evidence-linked blocks")
        self.assertEqual(len(self.window.output_panel.rendered.blocks), 3)
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

    def test_byte_sizes_match_the_source_rounding(self):
        """A file with bytes in it is never reported as "0 KB"."""
        self.assertEqual(bytes_label(1), "1 KB")
        self.assertEqual(bytes_label(4096), "4 KB")
        self.assertEqual(bytes_label(1024 * 1024), "1.0 MB")
        self.assertEqual(bytes_label(3_500_000), "3.3 MB")

    def test_a_recorded_time_is_read_and_a_missing_one_is_named(self):
        """The library dates a job by the time the local database recorded."""
        self.assertEqual(format_timestamp("2026-08-23T15:47:48+00:00"),
                         format_timestamp("2026-08-23T15:47:48Z"))
        # Shown as stored rather than as a parsed date that is not one.
        self.assertEqual(format_timestamp("whenever"), "whenever")
        # And an absent time is a fact about the record, not a blank in the row.
        self.assertEqual(format_timestamp(""), "Date not recorded")

    def test_the_model_pane_counts_only_packs_a_person_can_act_on(self):
        packs = [
            {"id": "built-in", "required": True, "approved": True, "available_locally": True},
            {"id": "here", "approved": True, "available_locally": True},
            {"id": "fetchable", "approved": True, "available_locally": False, "downloadable": True},
            {"id": "blocked", "approved": False, "available_locally": False, "downloadable": True},
        ]
        summary = model_setup_summary(packs)
        # Two optional approved packs: the built-in and the policy-blocked one
        # pad neither half of the count.
        self.assertIn("1 of 2", summary)
        self.assertIn("1 can be downloaded", summary)
        self.assertEqual(model_setup_summary([packs[0], packs[3]]),
                         "No optional model packs are approved for this build.")
        self.assertNotIn("can be downloaded", model_setup_summary([packs[0], packs[1]]))


class ConversionGatingTest(unittest.TestCase):
    """Nothing can be converted that is not there, or that preflight refused.

    The source project spreads this across four tests of its convert button.
    Here it is one compound expression in `_refresh_command_bar`, so it is
    worth exercising each branch of that expression rather than trusting it to
    read correctly.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.window = MainWindow(PhilonService(Path(_data_dir)))
        wait_for_workers()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        wait_for_workers()

    def setUp(self) -> None:
        self.window.job_tabs.select("Single Job", announce=False)
        self.window.single_path = None
        self.window.running = False
        self.window.batch_items = []
        self.window.batch_preflight = {}

    def _queue(self, *items) -> None:
        self.window.job_tabs.select("Batch", announce=False)
        self.window.batch_items = list(items)
        self.window.batch_view.set_state(self.window.batch_items, self.window.batch_preflight, None)

    def test_a_single_job_cannot_start_before_a_document_is_chosen(self):
        self.window._refresh_command_bar()
        self.assertFalse(self.window.convert_button.isEnabled())

    def test_choosing_a_document_enables_the_conversion(self):
        self.window.single_path = "/tmp/chosen.pdf"
        self.window._refresh_command_bar()
        self.assertTrue(self.window.convert_button.isEnabled())
        self.assertEqual(self.window.selected_name.full_text(), "chosen.pdf")

    def test_a_conversion_already_running_cannot_be_started_again(self):
        self.window.single_path = "/tmp/chosen.pdf"
        self.window.running = True
        self.window._refresh_command_bar()
        self.assertFalse(self.window.convert_button.isEnabled())
        self.assertEqual(self.window.convert_button.text(), "Converting…")

    def test_the_button_comes_back_when_a_conversion_ends(self):
        """A failed conversion must not leave the workspace unusable."""
        self.window.single_path = "/tmp/chosen.pdf"
        self.window.running = True
        self.window._refresh_command_bar()
        self.window._finish_running("convert")
        self.window._refresh_command_bar()
        self.assertFalse(self.window.running)
        self.assertTrue(self.window.convert_button.isEnabled())

    def test_an_empty_batch_cannot_be_started(self):
        self._queue()
        self.window._refresh_command_bar()
        self.assertFalse(self.window.convert_button.isEnabled())

    def test_a_queued_batch_can_be_started(self):
        self._queue({"source_path": "/tmp/a.pdf", "status": "queued"})
        self.window._refresh_command_bar()
        self.assertTrue(self.window.convert_button.isEnabled())

    def test_a_batch_holding_a_document_preflight_blocked_cannot_be_started(self):
        """One refused document stops the queue, not just itself."""
        self._queue({"source_path": "/tmp/a.pdf", "status": "queued"},
                    {"source_path": "/tmp/bad.pdf", "status": "queued"})
        self.window.batch_preflight = {"/tmp/bad.pdf": {"source_path": "/tmp/bad.pdf", "status": "blocked",
                                                        "reason": "The file is encrypted."}}
        self.window._refresh_command_bar()
        self.assertFalse(self.window.convert_button.isEnabled())

    def test_the_page_range_is_offered_for_a_single_job_only(self):
        """A queue of documents is not something one range can describe."""
        self.window._refresh_command_bar()
        self.assertTrue(self.window.pages_field.isVisibleTo(self.window))
        self._queue({"source_path": "/tmp/a.pdf", "status": "queued"})
        self.window._refresh_command_bar()
        self.assertFalse(self.window.pages_field.isVisibleTo(self.window))


class BannerTest(unittest.TestCase):
    """A local failure is reported, and can be put away again."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.window = MainWindow(PhilonService(Path(_data_dir)))
        wait_for_workers()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        wait_for_workers()

    def test_a_failure_is_shown_rather_than_swallowed(self):
        self.window.show_error("The local converter refused the document.")
        self.assertTrue(self.window.error_banner.isVisibleTo(self.window))
        self.assertIn("refused the document", self.window.error_banner.message.text())

    def test_a_shown_banner_can_be_dismissed(self):
        self.window.show_error("Something local went wrong.")
        self.window._clear_banners()
        self.assertFalse(self.window.error_banner.isVisibleTo(self.window))

    def test_a_conversion_that_produced_nothing_says_why(self):
        self.window._clear_banners()
        self.window._conversion_finished({"results": [], "failures": [{"error": "The file is encrypted."}]})
        wait_for_workers()
        self.assertTrue(self.window.error_banner.isVisibleTo(self.window))
        self.assertIn("encrypted", self.window.error_banner.message.text())

    def test_choosing_a_new_document_clears_the_last_failure(self):
        self.window.show_error("The last one failed.")
        self.window.single_path = "/tmp/next.pdf"
        self.window.error_banner.hide()
        self.assertFalse(self.window.error_banner.isVisibleTo(self.window))


class LibraryRemovalTest(unittest.TestCase):
    """Removing local history is confirmed first, and says what it kept."""

    def setUp(self) -> None:
        self.view = LibraryView()
        self.view.set_history([{"id": "j1", "created_at": "2026-08-21T10:00:00+00:00",
                                "profile": "Balanced", "documents": 1, "warnings": 0}])

    def tearDown(self) -> None:
        self.view.deleteLater()
        _app.processEvents()

    def _button_labelled(self, fragment: str) -> QPushButton | None:
        return next((b for b in self.view.findChildren(QPushButton) if fragment in b.text()), None)

    def test_removal_is_not_offered_directly(self):
        self.assertIsNotNone(self._button_labelled("Clean"))
        self.assertIsNone(self._button_labelled("Remove 1 job"))

    def test_asking_to_clean_asks_for_confirmation_first(self):
        asked = []
        self.view.clean_requested.connect(lambda: asked.append(True))
        self.view._request_clean()
        self.assertTrue(self.view.clean_pending)
        self.assertIsNotNone(self._button_labelled("Remove 1 job"))
        self.assertIsNotNone(self._button_labelled("Cancel"))
        self.assertEqual(asked, [], "nothing may be removed on the first click")

    def test_the_confirmation_can_be_taken_back(self):
        self.view._request_clean()
        self.view._cancel_clean()
        self.assertFalse(self.view.clean_pending)
        self.assertIsNone(self._button_labelled("Remove 1 job"))

    def test_there_is_nothing_to_clean_with_no_history(self):
        self.view.set_history([])
        clean = self._button_labelled("Clean")
        self.assertIsNotNone(clean)
        self.assertFalse(clean.isEnabled())


class ModelPolicyTest(unittest.TestCase):
    """The approval gate is what the pane offers, not only what the engine enforces."""

    def setUp(self) -> None:
        self.view = ModelsView()

    def tearDown(self) -> None:
        self.view.deleteLater()
        _app.processEvents()

    def _toggles(self) -> list[QPushButton]:
        return [b for b in self.view.findChildren(QPushButton) if b.objectName() == "ModelToggle"]

    def test_a_policy_blocked_pack_cannot_be_enabled(self):
        self.view.set_packs([{"id": "unapproved-pack", "approved": False, "available_locally": True,
                              "readiness": "ready", "role": "repair", "runtime": "llama.cpp",
                              "license": "UNRESOLVED - review required before distribution"}], [])
        toggle = next(b for b in self._toggles() if b.text() != "Download")
        self.assertFalse(toggle.isEnabled(), "an unapproved pack offered a working switch")

    def test_an_approved_ready_pack_can_be_enabled(self):
        self.view.set_packs([{"id": "approved-pack", "approved": True, "available_locally": True,
                              "readiness": "ready", "role": "repair", "runtime": "llama.cpp",
                              "license": "Apache-2.0"}], [])
        toggle = next(b for b in self._toggles() if b.text() != "Download")
        self.assertTrue(toggle.isEnabled())
        self.assertEqual(toggle.text(), "Enable")

    def test_a_download_is_never_offered_for_a_pack_policy_blocks(self):
        self.view.set_packs([{"id": "unapproved-pack", "approved": False, "available_locally": False,
                              "downloadable": True, "download_bytes": 520 * 1024 ** 2,
                              "download_verified": True, "readiness": "absent",
                              "role": "repair", "runtime": "llama.cpp", "license": "UNRESOLVED"}], [])
        self.assertEqual([b for b in self._toggles() if b.text() == "Download"], [])

    def test_a_built_in_runtime_offers_no_switch_at_all(self):
        self.view.set_packs([{"id": "built-in", "required": True, "approved": True,
                              "available_locally": True, "readiness": "ready",
                              "role": "extraction", "runtime": "pdfium", "license": "BSD-3-Clause"}], [])
        self.assertEqual(self._toggles(), [])


class PageSelectionControlTest(unittest.TestCase):
    """The engine converts a page range; the interface has to be able to ask."""

    def setUp(self) -> None:
        self.service = PhilonService()
        self.window = MainWindow(self.service)

    def tearDown(self) -> None:
        wait_for_workers()
        self.window.close()
        self.window.deleteLater()

    def test_the_field_is_offered_for_a_single_job_only(self):
        self.window.job_tabs.select("Single Job")
        self.window._refresh_command_bar()
        self.assertTrue(self.window.pages_field.isVisibleTo(self.window))
        # A batch is a queue of documents, not one document to take a range of.
        self.window.job_tabs.select("Batch")
        self.window._refresh_command_bar()
        self.assertFalse(self.window.pages_field.isVisibleTo(self.window))

    def test_an_empty_field_means_the_whole_document(self):
        self.assertIsNone(self.service.parse_pages(""))
        self.assertIsNone(self.service.parse_pages("   "))

    def test_a_range_is_read_the_way_the_engine_reads_it(self):
        self.assertEqual(self.service.parse_pages("1-3,8"), (1, 2, 3, 8))

    def test_a_malformed_range_is_refused_before_a_job_starts(self):
        self.window.job_tabs.select("Single Job")
        self.window.single_path = "/tmp/not-really-opened.pdf"
        self.window.pages_field.setText("3-1")
        started = []
        self.window._spawn = lambda *args, **kwargs: started.append(args)

        self.window.convert()

        self.assertEqual(started, [], "a job was started on a selection the engine would refuse")
        self.assertTrue(self.window.error_banner.isVisibleTo(self.window))
        self.assertIn("3-1", self.window.error_banner.message.text())
        self.assertFalse(self.window.running)

    def test_a_sound_range_reaches_the_service(self):
        self.window.job_tabs.select("Single Job")
        self.window.single_path = "/tmp/not-really-opened.pdf"
        self.window.pages_field.setText("2-4")
        seen = {}

        def spawn(_key, _kind, _message, work, _done, total=1):
            seen["work"] = work

        self.window._spawn = spawn
        self.window.convert()

        captured = {}
        self.service.convert = lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs)
        seen["work"](lambda _progress: None)
        self.assertEqual(captured["args"][-1], (2, 3, 4))


class EvidenceForNewFieldsTest(unittest.TestCase):
    """Evidence the engine records is evidence the panel has to show."""

    def setUp(self) -> None:
        self.panel = EvidencePanel()

    def tearDown(self) -> None:
        self.panel.deleteLater()

    @staticmethod
    def summary_text(panel) -> str:
        from philon_desktop.gui.qt import QLabel

        return " | ".join(label.text() for label in panel.findChildren(QLabel))

    def test_a_rotated_page_says_so(self):
        self.panel.set_document(sample_document(), "block-1")
        self.assertIn("90°", self.summary_text(self.panel))

    def test_links_are_counted_and_the_withheld_one_is_named(self):
        self.panel.set_document(sample_document(), "block-1")
        text = self.summary_text(self.panel)
        self.assertIn("1 anchored", text)
        self.assertIn("withheld", text)

    def test_a_recovered_table_is_named_on_the_block_it_was_recovered_for(self):
        self.panel.set_document(sample_document(), "block-3")
        self.assertIn("2 × 2 recovered from ruled geometry", self.summary_text(self.panel))

    def test_a_page_that_ruled_a_table_says_so_on_a_block_that_is_not_one(self):
        self.panel.set_document(sample_document(), "block-1")
        self.assertIn("1 recovered on this page", self.summary_text(self.panel))

    def test_the_summary_reads_for_each_shape_of_ruled_evidence(self):
        from philon_desktop.gui.evidence_panel import ruled_table_summary

        self.assertEqual(ruled_table_summary(None, None), "None ruled")
        self.assertEqual(ruled_table_summary(None, {"ruled_tables": []}), "None ruled")
        self.assertEqual(ruled_table_summary({"table": {"row_count": 3, "column_count": 4}}, None),
                         "3 × 4 recovered from ruled geometry")
        self.assertEqual(ruled_table_summary(None, {"ruled_tables": [{"recoverable": True}, {"recoverable": True}]}),
                         "2 recovered on this page")
        # A lattice Philon refused to force into a table is reported, not dropped.
        self.assertEqual(ruled_table_summary(None, {"ruled_tables": [{"recoverable": False}]}),
                         "1 ruled, none a shape a table can hold")
        self.assertEqual(ruled_table_summary(None, {"ruled_tables": [{"recoverable": True}, {"recoverable": False}]}),
                         "1 recovered, 1 not recoverable")

    def test_the_summary_names_the_merged_cells_the_rules_proved(self):
        from philon_desktop.gui.evidence_panel import ruled_table_summary

        spans = [[{"rowspan": 1, "colspan": 2}, None], [{"rowspan": 1, "colspan": 1}, {"rowspan": 1, "colspan": 1}]]
        self.assertEqual(ruled_table_summary({"table": {"row_count": 2, "column_count": 2, "spans": spans}}, None),
                         "2 × 2 recovered from ruled geometry, 1 merged")

    def test_the_summary_reads_for_each_shape_of_formula_evidence(self):
        from philon_desktop.gui.evidence_panel import measured_formula_summary

        self.assertEqual(measured_formula_summary(None), "Not measured")
        self.assertEqual(measured_formula_summary({"evidence": {"findings": {}}}), "None measured")
        self.assertEqual(
            measured_formula_summary({"formula": {"typeset": "E = mc^{2}"},
                                      "evidence": {"findings": {"measured_script_count": 1}}}),
            "Recovered with 1 measured script")
        self.assertEqual(
            measured_formula_summary({"evidence": {"findings": {"measured_script_count": 2}}}),
            "2 measured scripts, not a formula")
        self.assertEqual(
            measured_formula_summary({"evidence": {"findings": {"set_in_mathematical_face": True}}}),
            "Set in a mathematical face")

    def test_a_download_is_offered_only_for_a_pack_that_is_not_already_here(self):
        from philon_desktop.gui.secondary_pages import ModelsView

        view = ModelsView()
        view.set_packs([
            {"id": "fetchable", "role": "r", "runtime": "local", "license": "Apache-2.0", "approved": True,
             "required": False, "available_locally": False, "downloadable": True,
             "download_bytes": 545590272, "download_verified": True, "readiness": "not-found"},
            {"id": "already-here", "role": "r", "runtime": "local", "license": "Apache-2.0", "approved": True,
             "required": False, "available_locally": True, "downloadable": True,
             "download_bytes": 100, "download_verified": True, "readiness": "ready"},
            {"id": "unapproved", "role": "r", "runtime": "local", "license": "x", "approved": False,
             "required": False, "available_locally": False, "downloadable": True,
             "download_bytes": 100, "readiness": "blocked"},
        ], [])
        labels = [button.text() for button in view.findChildren(QPushButton)]
        self.assertEqual(labels.count("Download"), 1)

    def test_the_pane_names_the_size_and_that_the_bytes_are_checked(self):
        from philon_desktop.gui.secondary_pages import pack_download_label, model_setup_summary

        self.assertIsNone(pack_download_label({}))
        self.assertEqual(
            pack_download_label({"downloadable": True, "download_bytes": 545590272, "download_verified": True}),
            "520 MB, checked against a SHA-256")
        self.assertEqual(
            pack_download_label({"downloadable": True, "download_bytes": 6_940_000_000, "download_verified": True}),
            "6.5 GB, checked against a SHA-256")
        self.assertEqual(
            pack_download_label({"downloadable": True, "download_bytes": 1048576, "download_verified": False}),
            "1 MB, no digest declared")
        # Built-in runtimes and packs policy blocks pad neither half.
        self.assertEqual(model_setup_summary([{"required": True, "approved": True, "available_locally": True}]),
                         "No optional model packs are approved for this build.")
        self.assertEqual(model_setup_summary([
            {"required": False, "approved": True, "available_locally": True},
            {"required": False, "approved": True, "available_locally": False, "downloadable": True},
        ]), "1 of 2 approved packs are already on this machine; 1 can be downloaded.")

    def test_the_summary_reads_for_each_shape_of_link_evidence(self):
        from philon_desktop.gui.evidence_panel import link_summary

        self.assertEqual(link_summary([]), "None declared")
        self.assertEqual(link_summary([{"uri": "https://a.test"}]), "1 anchored")
        self.assertEqual(link_summary([{"uri": "javascript:x"}]), "1 declared, none an anchorable scheme")
        self.assertEqual(link_summary([{"uri": "https://a.test"}, {"uri": "file:///x"}]),
                         "1 anchored, 1 withheld as unanchorable")


if __name__ == "__main__":
    unittest.main()
