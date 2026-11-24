import threading
import traceback

from PySide6.QtCore import QThread, Signal

from core.progress import Cancelled
from utils.logging_utils import get_logger

log = get_logger("worker")


class TaskWorker(QThread):
    """Runs `self.work(progress_cb, cancel_event)` off the GUI thread.
    All results go back through Qt signals (queued across threads). Workers open their own
    SQLite connections inside work(); connections are never shared between threads."""

    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    title = "Working"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def work(self, progress_cb, cancel_event):  # pragma: no cover - overridden
        raise NotImplementedError

    def run(self):
        try:
            result = self.work(self.progress.emit, self.cancel_event)
            if self.cancel_event.is_set():
                self.cancelled.emit()
            self.succeeded.emit(result)
        except Cancelled:
            self.cancelled.emit()
            self.succeeded.emit(None)
        except Exception as e:
            log.error("Task %s failed:\n%s", self.title, traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")


class FuncWorker(TaskWorker):
    """Run an arbitrary callable fn(progress_cb, cancel_event) in the background."""

    def __init__(self, fn, title="Working", parent=None):
        super().__init__(parent)
        self._fn = fn
        self.title = title

    def work(self, progress_cb, cancel_event):
        return self._fn(progress_cb, cancel_event)
