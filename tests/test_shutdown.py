"""Quitting must not destroy a worker thread, or abandon a helper process.

Qt aborts the process outright when a running QThread is destroyed, so a
window closed during a library refresh or an export would crash rather than
close. The port also runs the engine in-process, which the source project runs
behind a socket bridge: there is no bridge here whose death takes a helper with
it, so an Apple Vision or llama.cpp run started by a conversion is a direct
child of the application and outlives it unless something ends it. These checks
run off-screen and construct no window.
"""

import os
import subprocess
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from philon_desktop.app import WorkThread, stop_leftover_children, wait_for_workers  # noqa: E402
from philon_desktop.gui.workers import own_child_processes  # noqa: E402


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


class LeftoverHelperTest(unittest.TestCase):
    """A conversion that will not stop must not leave its helper running.

    `sleep` stands in for the helper processes the engine actually starts. What
    matters is the shape they share: a worker thread blocked in
    `subprocess.run` on a child of this process, with a timeout long enough
    (90s for Apple Vision, 240s for embeddings, 420s for a repair) that the
    bounded shutdown wait will always give up first.
    """

    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.started: dict[str, int] = {}

    def _worker_blocked_on_a_child(self) -> WorkThread:
        def job(_progress):
            child = subprocess.Popen(["/bin/sleep", "45"])
            self.started["pid"] = child.pid
            child.wait()

        worker = WorkThread(job)
        worker.start()
        deadline = time.monotonic() + 5
        while "pid" not in self.started and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIn("pid", self.started, "the helper process never started")
        return worker

    def _alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def test_the_shutdown_wait_alone_leaves_the_helper_running(self):
        """The reason the reap exists, stated as a test rather than a comment."""
        worker = self._worker_blocked_on_a_child()
        pid = self.started["pid"]
        try:
            self.assertFalse(wait_for_workers(300), "the wait was expected to give up on a blocked worker")
            self.assertTrue(self._alive(pid), "waiting was never what ended the helper")
        finally:
            stop_leftover_children()
            worker.wait(5000)

    def test_a_helper_a_conversion_left_behind_is_ended(self):
        worker = self._worker_blocked_on_a_child()
        pid = self.started["pid"]
        try:
            self.assertFalse(wait_for_workers(300))
            self.assertIn(pid, stop_leftover_children())
            self.assertFalse(self._alive(pid), "the helper outlived the application that started it")
        finally:
            worker.wait(5000)

    def test_ending_the_helper_is_what_lets_the_stuck_worker_finish(self):
        """So the second wait succeeds and Qt is never asked to destroy a live thread."""
        worker = self._worker_blocked_on_a_child()
        self.assertFalse(wait_for_workers(300))
        stop_leftover_children()
        self.assertTrue(wait_for_workers(5000), "the worker stayed blocked after its helper ended")
        self.assertFalse(worker.isRunning())

    def test_the_listing_never_reports_the_process_doing_the_listing(self):
        """`ps` is itself a child of this process and appears in its own output."""
        for pid in own_child_processes():
            self.assertTrue(self._alive(pid), f"pid {pid} was listed as a live child but has already gone")


if __name__ == "__main__":
    unittest.main()
