"""Retry documents that failed or are waiting for OCR."""
from core.ocr_engine import OcrEngine
from core.pdf_processor import process_document
from core.progress import Progress
from database import schema as S
from workers.base_worker import TaskWorker


class RetryFailedWorker(TaskWorker):
    title = "Retrying failed processing"

    def __init__(self, library, document_ids=None, tesseract_cmd="", parent=None):
        super().__init__(parent)
        self.library, self.document_ids, self.tesseract_cmd = library, document_ids, tesseract_cmd

    def work(self, progress_cb, cancel):
        conn = self.library.connect()
        try:
            s = self.library.settings(conn)
            ocr = OcrEngine(s["ocr_language"], self.tesseract_cmd, s["ocr_dpi"], s["min_text_chars"])
            q = ("SELECT * FROM documents WHERE availability_status='available' AND processing_status IN (?,?,?)")
            rows = conn.execute(q, (S.ST_FAILED, S.ST_PARTIAL, S.ST_WAITING)).fetchall()
            if self.document_ids is not None:
                rows = [r for r in rows if r["document_id"] in self.document_ids]
            prog = Progress("Retrying", 0, len(rows))
            for i, r in enumerate(rows):
                if cancel.is_set():
                    break
                prog.done, prog.current = i, r["filename"]
                prog.message = f"Retrying {r['filename']}"
                progress_cb(prog)
                res = process_document(conn, r["document_id"], self.library.abs_path(r["relative_path"]), s, ocr, cancel,
                                       reuse_ok_pages=r["processing_status"] in (S.ST_PARTIAL, S.ST_WAITING),
                                       page_cb=lambda a, b, n=r["filename"]: (setattr(prog, "message", f"{n}: page {a}/{b}"), progress_cb(prog)))
                if res.status == S.ST_INDEXED:
                    prog.success += 1
                else:
                    prog.failed += 1
            prog.done = len(rows)
            progress_cb(prog)
            return {"retried": len(rows), "succeeded": prog.success, "still_failing": prog.failed}
        finally:
            conn.close()
