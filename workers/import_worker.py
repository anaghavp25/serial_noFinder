from core.importer import import_documents
from core.ocr_engine import OcrEngine
from workers.base_worker import TaskWorker


class ImportWorker(TaskWorker):
    title = "Importing documents"

    def __init__(self, library, sources, include_subfolders, allow_duplicates, tesseract_cmd="", parent=None):
        super().__init__(parent)
        self.library, self.sources = library, list(sources)
        self.include_subfolders, self.allow_duplicates, self.tesseract_cmd = include_subfolders, allow_duplicates, tesseract_cmd

    def work(self, progress_cb, cancel):
        s = self.library.settings()
        ocr = OcrEngine(s["ocr_language"], self.tesseract_cmd, s["ocr_dpi"], s["min_text_chars"])
        return import_documents(self.library, self.sources, self.include_subfolders, self.allow_duplicates,
                                ocr, progress_cb, cancel)
