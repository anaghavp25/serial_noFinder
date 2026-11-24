from core.library_sync import sync_library
from core.ocr_engine import OcrEngine
from workers.base_worker import TaskWorker


class SyncWorker(TaskWorker):
    title = "Synchronizing library"

    def __init__(self, library, tesseract_cmd="", parent=None):
        super().__init__(parent)
        self.library, self.tesseract_cmd = library, tesseract_cmd

    def work(self, progress_cb, cancel):
        s = self.library.settings()
        ocr = OcrEngine(s["ocr_language"], self.tesseract_cmd, s["ocr_dpi"], s["min_text_chars"])
        return sync_library(self.library, ocr, progress_cb, cancel)
