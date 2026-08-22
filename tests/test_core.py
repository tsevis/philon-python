import tempfile
import unittest
from pathlib import Path

from philon_desktop.core import LocalStore, PhilonService


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

