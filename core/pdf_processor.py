"""Validate -> extract text -> OCR where needed -> index, for one document."""
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from core.ocr_engine import OcrEngine
from core.progress import Cancelled
from core.text_extractor import extract_page_text, page_needs_ocr
from database import schema as S
from database.db_manager import now, transaction
from database.search_index import index_page, remove_document_pages
from utils.file_hash import HashCancelled, sha256_file
from utils.fitz_compat import fitz
from utils.logging_utils import get_logger

log = get_logger("processor")

PAGE_OK = "ok"
PAGE_OCR_FAILED = "ocr_failed"
PAGE_OCR_PENDING = "ocr_pending"   # needs OCR but OCR is disabled/unavailable


@dataclass
class ProcessResult:
    status: str
    page_count: int = 0
    ocr_pages: int = 0
    error: Optional[str] = None
    cancelled: bool = False


def _set_status(conn, doc_id, status, error=None):
    conn.execute("UPDATE documents SET processing_status=?, last_error=? WHERE document_id=?", (status, error, doc_id))


def process_document(
    conn: sqlite3.Connection,
    document_id: str,
    abs_path: Path,
    settings: dict,
    ocr: OcrEngine,
    cancel: Optional[threading.Event] = None,
    page_cb: Optional[Callable[[int, int], None]] = None,
    content_hash: Optional[str] = None,
    reuse_ok_pages: bool = False,
) -> ProcessResult:
    """Everything is staged in memory; the database is only changed (in one transaction)
    after the whole document has been processed, so a crash never leaves half an index."""
    abs_path = Path(abs_path)
    doc = None
    try:
        _set_status(conn, document_id, S.ST_PROCESSING)
        st_before = abs_path.stat()
        if content_hash is None:
            content_hash = sha256_file(abs_path, cancel)

        reuse: Dict[int, tuple] = {}
        if reuse_ok_pages:
            for r in conn.execute(
                "SELECT page_number, extracted_text, extraction_method FROM pages "
                "WHERE document_id=? AND processing_status=?", (document_id, PAGE_OK)):
                reuse[r["page_number"]] = (r["extracted_text"], r["extraction_method"])

        doc = fitz.open(str(abs_path))
        if doc.needs_pass:
            raise ValueError("Password-protected PDF")
        n = doc.page_count
        if n == 0:
            raise ValueError("PDF has no pages")

        ocr_wanted = bool(settings.get("ocr_enabled", True))
        ocr_ready = ocr_wanted and ocr.available()
        min_chars = int(settings.get("min_text_chars", 25))
        pages = []
        ocr_used = 0
        ocr_status_set = False
        for i in range(n):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if page_cb:
                page_cb(i + 1, n)
            if (i + 1) in reuse:
                text, method = reuse[i + 1]
                pages.append((i + 1, text, method, PAGE_OK))
                continue
            page = doc.load_page(i)
            try:
                text = extract_page_text(page)
            except Exception as e:
                log.warning("Text extraction failed on %s p%d: %s", abs_path.name, i + 1, e)
                text = ""
            try:
                has_images = bool(page.get_images(full=False))
            except Exception:
                has_images = True
            if not page_needs_ocr(text, min_chars, has_images):
                pages.append((i + 1, text, "text", PAGE_OK))
                continue
            if not ocr_ready:
                pages.append((i + 1, text, "text", PAGE_OCR_PENDING))
                continue
            if not ocr_status_set:
                _set_status(conn, document_id, S.ST_OCR)
                ocr_status_set = True
            try:
                ocr_text = ocr.ocr_page(page)
                merged = (text + "\n" + ocr_text).strip() if text.strip() else ocr_text
                pages.append((i + 1, merged, "ocr", PAGE_OK))
                ocr_used += 1
            except Exception as e:
                log.warning("OCR failed on %s p%d: %s", abs_path.name, i + 1, e)
                pages.append((i + 1, text, "text", PAGE_OCR_FAILED))

        ok = sum(1 for p in pages if p[3] == PAGE_OK)
        pending = sum(1 for p in pages if p[3] == PAGE_OCR_PENDING)
        failed = sum(1 for p in pages if p[3] == PAGE_OCR_FAILED)
        if ok == n:
            status, err = S.ST_INDEXED, None
        elif ok > 0:
            status = S.ST_PARTIAL
            err = f"{pending + failed} page(s) not OCR'd" + ("" if ocr_ready else f": {ocr.error or 'OCR disabled'}")
        elif failed == 0:
            status = S.ST_WAITING
            err = ocr.error or "OCR is disabled" if not ocr_ready else None
        else:
            status, err = S.ST_FAILED, f"OCR failed on {failed} page(s)"

        # The file may have been replaced while we were reading it.
        st_after = abs_path.stat()
        changed = (st_after.st_size, st_after.st_mtime_ns) != (st_before.st_size, st_before.st_mtime_ns)

        with transaction(conn):
            remove_document_pages(conn, document_id)
            for num, text, method, pstatus in pages:
                cur = conn.execute(
                    "INSERT INTO pages(document_id,page_number,extracted_text,extraction_method,processing_status) "
                    "VALUES(?,?,?,?,?)", (document_id, num, text, method, pstatus))
                index_page(conn, cur.lastrowid, text)
            conn.execute(
                "UPDATE documents SET page_count=?, file_size=?, modified_ns=?, content_hash=?, processing_status=?, "
                "last_error=?, ocr_pages=?, last_verified_timestamp=?, availability_status='available' "
                "WHERE document_id=?",
                (n, st_before.st_size, st_before.st_mtime_ns, content_hash,
                 S.ST_MODIFIED if changed else status, err, ocr_used, now(), document_id))
        return ProcessResult(S.ST_MODIFIED if changed else status, n, ocr_used, err)

    except (Cancelled, HashCancelled):
        _safe_status(conn, document_id, S.ST_WAITING, "Processing cancelled")
        return ProcessResult(S.ST_WAITING, cancelled=True, error="cancelled")
    except Exception as e:
        log.exception("Processing failed for %s", abs_path)
        msg = f"{type(e).__name__}: {e}"
        try:
            with transaction(conn):
                remove_document_pages(conn, document_id)
                conn.execute("UPDATE documents SET processing_status=?, last_error=?, ocr_pages=0 WHERE document_id=?",
                             (S.ST_FAILED, msg, document_id))
        except Exception:
            log.exception("Could not record failure")
        return ProcessResult(S.ST_FAILED, error=msg)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def _safe_status(conn, doc_id, status, err):
    try:
        _set_status(conn, doc_id, status, err)
    except Exception:
        pass
