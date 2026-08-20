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

    def test_preferences_reject_empty_output_set(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PhilonService(Path(directory))
            with self.assertRaises(ValueError):
                service.save_preferences({"profile": "Balanced", "cache_policy": "use", "outputs": []})


if __name__ == "__main__":
    unittest.main()

