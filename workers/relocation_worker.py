from core.library_relocator import move_library
from workers.base_worker import TaskWorker


class RelocationWorker(TaskWorker):
    title = "Moving library"

    def __init__(self, library, target, parent=None):
        super().__init__(parent)
        self.library, self.target = library, target

    def work(self, progress_cb, cancel):
        return move_library(self.library, self.target, progress_cb, cancel)
