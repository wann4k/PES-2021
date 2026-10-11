"""Menjalankan pekerjaan berat (decode / encode) di thread terpisah."""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as e:  # noqa: BLE001 - tampilkan apa pun ke user
            traceback.print_exc()
            self.signals.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(result)


_POOL = QThreadPool()
_POOL.setMaxThreadCount(4)
_KEEP: set[Job] = set()


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None) -> Job:
    job = Job(fn)
    _KEEP.add(job)

    def finish(*_):
        _KEEP.discard(job)

    if on_done:
        job.signals.done.connect(on_done)
    if on_error:
        job.signals.failed.connect(on_error)
    job.signals.done.connect(finish)
    job.signals.failed.connect(finish)
    _POOL.start(job)
    return job
