"""Read one PDF: extract the text of every page with PyMuPDF and index it."""
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.progress import Cancelled
from core.text_extractor import alnum_count, extract_page_text
from database import schema as S
from database.db_manager import now, transaction
from database.search_index import index_page, remove_document_pages
from utils.file_hash import HashCancelled, sha256_file
from utils.fitz_compat import fitz
from utils.logging_utils import get_logger

log = get_logger("processor")


@dataclass
class ProcessResult:
    status: str
    page_count: int = 0
    error: Optional[str] = None
    cancelled: bool = False


def process_document(conn: sqlite3.Connection, document_id: str, abs_path: Path,
                     cancel: Optional[threading.Event] = None,
                     page_cb: Optional[Callable[[int, int], None]] = None,
                     content_hash: Optional[str] = None) -> ProcessResult:
    """All text is collected in memory first; the database is changed in ONE transaction at the end,
    so a crash or cancel never leaves a half-built index."""
    abs_path = Path(abs_path)
    doc = None
    try:
        conn.execute("UPDATE documents SET processing_status=?, last_error=NULL WHERE document_id=?",
                     (S.ST_PROCESSING, document_id))
        st_before = abs_path.stat()
        if content_hash is None:
            content_hash = sha256_file(abs_path, cancel)
        doc = fitz.open(str(abs_path))
        if doc.needs_pass:
            raise ValueError("Password-protected PDF")
        n = doc.page_count
        if n == 0:
            raise ValueError("PDF has no pages")
        pages = []
        for i in range(n):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if page_cb:
                page_cb(i + 1, n)
            try:
                text = extract_page_text(doc.load_page(i))
            except Exception as e:
                log.warning("Text extraction failed on %s p%d: %s", abs_path.name, i + 1, e)
                text = ""
            pages.append((i + 1, text))

        has_text = any(alnum_count(t) > 0 for _, t in pages)
        status = S.ST_INDEXED if has_text else S.ST_NO_TEXT
        err = None if has_text else "No selectable text (scanned or image-only PDF)"
        st_after = abs_path.stat()             # was the file replaced while we were reading it?
        changed = (st_after.st_size, st_after.st_mtime_ns) != (st_before.st_size, st_before.st_mtime_ns)

        with transaction(conn):
            remove_document_pages(conn, document_id)
            for num, text in pages:
                cur = conn.execute("INSERT INTO pages(document_id,page_number,extracted_text,extraction_method,"
                                   "processing_status) VALUES(?,?,?,'text','ok')", (document_id, num, text))
                index_page(conn, cur.lastrowid, text)
            conn.execute(
                "UPDATE documents SET page_count=?, file_size=?, modified_ns=?, content_hash=?, processing_status=?, "
                "last_error=?, last_verified_timestamp=?, availability_status='available' WHERE document_id=?",
                (n, st_before.st_size, st_before.st_mtime_ns, content_hash,
                 S.ST_MODIFIED if changed else status, err, now(), document_id))
        return ProcessResult(S.ST_MODIFIED if changed else status, n, err)

    except (Cancelled, HashCancelled):
        try:
            conn.execute("UPDATE documents SET processing_status=? WHERE document_id=?", (S.ST_IMPORTED, document_id))
        except Exception:
            pass
        return ProcessResult(S.ST_IMPORTED, cancelled=True, error="cancelled")
    except Exception as e:
        log.exception("Processing failed for %s", abs_path)
        msg = f"{type(e).__name__}: {e}"
        try:
            with transaction(conn):
                remove_document_pages(conn, document_id)
                conn.execute("UPDATE documents SET processing_status=?, last_error=? WHERE document_id=?",
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
