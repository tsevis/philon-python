import tempfile
import unittest
from pathlib import Path

from philon_desktop.core import (
    DEFAULT_OUTPUTS, OUTPUTS, LocalStore, PhilonService,
    default_preferences, sanitize_preferences)


class LocalStoreTest(unittest.TestCase):
    def test_queue_state_survives_a_new_store_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalStore(root)
            batch_id = store.create_batch(["/tmp/a.pdf", "/tmp/a.pdf", "/tmp/b.pdf"], "Balanced", "use", ["ir"])
            self.assertEqual(len(store.batch_items(batch_id)), 2)
            store.set_item_state(store.batch_items(batch_id)[0]["id"], "paused")
            restored = LocalStore(root)
            self.assertEqual(restored.batch_items(batch_id)[0]["status"], "paused")

    def test_interrupted_running_item_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            batch_id = store.create_batch(["/tmp/a.pdf"], "Fast", "bypass", ["ir"])
            item = store.batch_items(batch_id)[0]
            store.mark_item_running(item["id"])
            recovered = LocalStore(Path(directory))
            self.assertEqual(recovered.batch_items(batch_id)[0]["status"], "queued")

    def test_clear_jobs_removes_history_and_reports_the_count(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            store.store_job({"id": "job-1", "created_at": "2026-08-21T00:00:00+00:00", "profile": "Balanced", "results": [], "failures": []})
            store.store_job({"id": "job-2", "created_at": "2026-08-21T00:00:01+00:00", "profile": "Fast", "results": [], "failures": []})
            self.assertEqual(store.clear_jobs(), 2)
            self.assertEqual(store.list_jobs(), [])
            self.assertEqual(store.clear_jobs(), 0)

    def test_preferences_carry_enabled_model_ids_and_reject_bad_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PhilonService(Path(directory))
            self.assertEqual(service.preferences()["enabled_model_ids"], [])
            service.save_preferences({"profile": "Balanced", "cache_policy": "use", "outputs": ["ir"], "enabled_model_ids": ["qwen3.8-27b-local-repair"]})
            self.assertEqual(service.preferences()["enabled_model_ids"], ["qwen3.8-27b-local-repair"])
            with self.assertRaises(ValueError):
                service.save_preferences({"profile": "Balanced", "cache_policy": "use", "outputs": ["ir"], "enabled_model_ids": [3]})

    def test_preferences_reject_empty_output_set(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PhilonService(Path(directory))
            with self.assertRaises(ValueError):
                service.save_preferences({"profile": "Balanced", "cache_policy": "use", "outputs": []})


if __name__ == "__main__":
    unittest.main()



class StoredPreferenceTest(unittest.TestCase):
    """What a preference set written by another build means to this one.

    Validating on write does not cover this. `OUTPUTS` and `PROFILES` are part
    of the build and they change -- `page_tree` was added to the output list --
    so a database written by an older Philon can hold names this build does not
    offer, having been perfectly valid when it was saved. The read is the only
    place that can catch it.
    """

    def test_nothing_stored_yields_the_documented_defaults(self):
        self.assertEqual(sanitize_preferences(None), default_preferences())

    def test_a_stored_value_of_the_wrong_shape_is_ignored_entirely(self):
        for stored in ("a string", 42, [], None):
            self.assertEqual(sanitize_preferences(stored), default_preferences())

    def test_a_fully_valid_set_is_kept(self):
        stored = {"profile": "Verified", "cache_policy": "bypass",
                  "outputs": ["markdown", "ir"], "enabled_model_ids": ["olmocr-2"]}
        self.assertEqual(sanitize_preferences(stored), stored)

    def test_a_profile_this_build_does_not_offer_is_refused(self):
        self.assertEqual(sanitize_preferences({"profile": "Turbo"})["profile"], "Balanced")

    def test_a_cache_policy_this_build_does_not_offer_is_refused(self):
        self.assertEqual(sanitize_preferences({"cache_policy": "reuse"})["cache_policy"], "use")

    def test_an_output_this_build_removed_is_dropped_rather_than_forwarded(self):
        """The engine would be asked for a format it no longer writes."""
        stored = {"outputs": ["markdown", "lasagne", "ir"]}
        self.assertEqual(sanitize_preferences(stored)["outputs"], ["markdown", "ir"])

    def test_outputs_come_back_in_this_builds_canonical_order(self):
        stored = {"outputs": ["ir", "machine", "markdown"]}
        self.assertEqual(sanitize_preferences(stored)["outputs"], ["machine", "markdown", "ir"])

    def test_an_output_set_this_build_no_longer_recognises_falls_back(self):
        """Emitting nothing is not a conversion, so it is the defaults."""
        self.assertEqual(sanitize_preferences({"outputs": ["gopher", "lasagne"]})["outputs"],
                         list(DEFAULT_OUTPUTS))
        self.assertEqual(sanitize_preferences({"outputs": []})["outputs"], list(DEFAULT_OUTPUTS))

    def test_page_tree_survives_when_a_build_that_had_it_stored_it(self):
        """The added-name direction: off by default, kept when chosen."""
        self.assertNotIn("page_tree", DEFAULT_OUTPUTS)
        self.assertIn("page_tree", OUTPUTS)
        self.assertIn("page_tree", sanitize_preferences({"outputs": ["ir", "page_tree"]})["outputs"])

    def test_a_model_id_of_the_wrong_type_is_dropped(self):
        stored = {"enabled_model_ids": ["olmocr-2", 3, None, "bge-m3"]}
        self.assertEqual(sanitize_preferences(stored)["enabled_model_ids"], ["olmocr-2", "bge-m3"])

    def test_a_model_list_of_the_wrong_shape_becomes_no_models(self):
        self.assertEqual(sanitize_preferences({"enabled_model_ids": "olmocr-2"})["enabled_model_ids"], [])

    def test_the_shared_defaults_are_never_handed_out_for_a_caller_to_mutate(self):
        first = sanitize_preferences(None)
        first["outputs"].append("mutated")
        first["enabled_model_ids"].append("mutated")
        second = sanitize_preferences(None)
        self.assertNotIn("mutated", second["outputs"])
        self.assertNotIn("mutated", second["enabled_model_ids"])
        self.assertEqual(second, default_preferences())

    def test_a_stored_set_is_read_back_through_the_same_gate(self):
        """The path that actually matters: SQLite -> service -> the engine."""
        with tempfile.TemporaryDirectory() as directory:
            service = PhilonService(Path(directory))
            # Written directly, as a previous build would have left it.
            service.store.save_setting("preferences", {"profile": "Verified",
                                                       "cache_policy": "bypass",
                                                       "outputs": ["markdown", "retired_format"],
                                                       "enabled_model_ids": ["ok", 7]})
            read = service.preferences()
            self.assertEqual(read["outputs"], ["markdown"])
            self.assertEqual(read["enabled_model_ids"], ["ok"])
            self.assertEqual(read["profile"], "Verified")
