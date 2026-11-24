from core.library_sync import sync_library
from workers.base_worker import TaskWorker


class SyncWorker(TaskWorker):
    title = "Loading documents"

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library

    def work(self, progress_cb, cancel):
        return sync_library(self.library, progress_cb, cancel)
