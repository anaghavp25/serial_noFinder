"""Index maintenance: rebuild the search index from stored text, or clear it and re-process."""
from core.library_sync import sync_library
from core.ocr_engine import OcrEngine
from core.progress import Progress
from database.search_index import clear_index, rebuild_index
from workers.base_worker import TaskWorker


class RebuildIndexWorker(TaskWorker):
    title = "Rebuilding search index"

    def __init__(self, library, reprocess_all=False, tesseract_cmd="", parent=None):
        super().__init__(parent)
        self.library, self.reprocess_all, self.tesseract_cmd = library, reprocess_all, tesseract_cmd

    def work(self, progress_cb, cancel):
        conn = self.library.connect()
        try:
            if self.reprocess_all:                      # "Clear index": text + index are dropped, files re-read
                clear_index(conn)
            else:
                rebuild_index(conn, lambda d, t: progress_cb(Progress("Rebuilding index", d, t, message="Rebuilding search index")))
        finally:
            conn.close()
        if self.reprocess_all:
            s = self.library.settings()
            ocr = OcrEngine(s["ocr_language"], self.tesseract_cmd, s["ocr_dpi"], s["min_text_chars"])
            return sync_library(self.library, ocr, progress_cb, cancel)
        return None
