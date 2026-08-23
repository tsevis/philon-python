"""Background work threads shared by every page of the desktop shell."""

from __future__ import annotations

import os
import signal
import subprocess
import time
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


def own_child_processes() -> list[int]:
    """Processes this application still owns.

    The port runs the engine in-process, so a helper the engine starts -- the
    Apple Vision OCR binary, a llama.cpp run for embeddings or for a repair --
    is a direct child of the application itself rather than of a bridge
    process. Nothing else in Philon spawns one, so a direct child that outlives
    a conversion is always a helper that was left behind.
    """
    try:
        with subprocess.Popen(["/bin/ps", "-A", "-o", "pid=,ppid="],
                              stdout=subprocess.PIPE, text=True) as probe:
            listing, _ = probe.communicate(timeout=5)
            # `ps` is itself a child of this process and reports itself.
            probe_pid = probe.pid
    except (OSError, subprocess.SubprocessError):
        return []
    mine = os.getpid()
    children = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, parent = int(fields[0]), int(fields[1])
        if parent == mine and pid != probe_pid:
            children.append(pid)
    return children


def _still_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop_leftover_children(grace_ms: int = 2000) -> list[int]:
    """End helper processes a worker left running, and report which they were.

    A child may exit between being listed and being signalled, so every signal
    tolerates a pid that has already gone; that race is normal rather than an
    error. Anything still alive after the grace period is killed, because the
    point of this call is that nothing survives it -- a helper that ignored the
    polite signal would otherwise hold a core for the rest of its timeout with
    no window left to cancel it from.
    """
    children = own_child_processes()
    for pid in children:
        _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace_ms / 1000
    while time.monotonic() < deadline and any(_still_alive(pid) for pid in children):
        time.sleep(0.02)
    for pid in children:
        if _still_alive(pid):
            _signal(pid, signal.SIGKILL)
    return children


def _signal(pid: int, number: int) -> None:
    try:
        os.kill(pid, number)
    except OSError:
        pass
