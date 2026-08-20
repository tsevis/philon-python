"""Quitting must not destroy a worker thread that is still running.

Qt aborts the process outright in that case, so a window closed during a
library refresh or an export would crash rather than close. The check runs
off-screen and constructs no window.
"""

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from philon_desktop.app import WorkThread, wait_for_workers  # noqa: E402


class WorkerShutdownTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_waiting_leaves_no_worker_running(self):
        workers = [WorkThread(lambda _progress: time.sleep(0.3)) for _ in range(3)]
        for worker in workers:
            worker.start()
        self.assertTrue(any(worker.isRunning() for worker in workers), "the workers must be running to be waited for")

        self.assertTrue(wait_for_workers(5000))

        self.assertFalse(any(worker.isRunning() for worker in workers), "a running worker survived the wait")

    def test_a_finished_worker_is_not_waited_for_twice(self):
        worker = WorkThread(lambda _progress: None)
        worker.start()
        worker.wait(5000)
        self.assertTrue(wait_for_workers(0), "a worker that already finished must not block shutdown")


if __name__ == "__main__":
    unittest.main()
