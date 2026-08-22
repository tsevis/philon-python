"""Background work threads shared by every page of the desktop shell."""

from __future__ import annotations

from typing import Any, Callable

from .qt import QThread, Signal


class WorkThread(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    # Every worker that has been started and has not finished. Qt aborts the
    # process when a running QThread is destroyed, so shutdown needs to know
    # what is still in flight rather than trusting each page to remember.
    _live: set["WorkThread"] = set()

    def __init__(self, job: Callable[[Callable[[dict[str, Any]], None]], Any]) -> None:
        super().__init__()
        self.job = job
        self.finished.connect(lambda: WorkThread._live.discard(self))

    def start(self, *args: Any, **kwargs: Any) -> None:
        WorkThread._live.add(self)
        super().start(*args, **kwargs)

    def run(self) -> None:
        try:
            self.completed.emit(self.job(self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


def wait_for_workers(timeout_ms: int = 5000) -> bool:
    """Let background work finish before the threads running it are destroyed.

    Returns whether everything stopped in time. The wait is bounded rather than
    indefinite: a quit that hangs on a stuck worker would be worse than the
    abort it is avoiding, and conversion work is written to be interruptible at
    a document boundary rather than mid-write.
    """
    stopped = True
    for worker in list(WorkThread._live):
        if worker.isRunning() and not worker.wait(timeout_ms):
            stopped = False
    return stopped
