"""Reconcile the library folder with the database (incremental; never re-hashes unchanged files)."""
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from core.library_manager import Library
from core.ocr_engine import OcrEngine
from core.pdf_processor import process_document
from core.progress import Progress, ProgressCB, check_cancel, Cancelled
from database import schema as S
from database.db_manager import now, transaction
from database.search_index import remove_document_pages
from utils.file_hash import sha256_file
from utils.logging_utils import get_logger
from utils.path_utils import iter_pdfs, to_rel

log = get_logger("sync")


@dataclass
class SyncReport:
    added: List[str] = field(default_factory=list)
    marked_missing: List[str] = field(default_factory=list)
    restored: List[str] = field(default_factory=list)
    renamed: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    purged: List[str] = field(default_factory=list)
    processed: int = 0
    failed: List[str] = field(default_factory=list)
    cancelled: bool = False

    def summary(self) -> str:
        parts = [(len(self.added), "new"), (len(self.modified), "modified"), (len(self.marked_missing), "missing"),
                 (len(self.restored), "restored"), (len(self.renamed), "renamed"), (len(self.purged), "purged"),
                 (len(self.failed), "failed")]
        txt = ", ".join(f"{n} {w}" for n, w in parts if n)
        return txt or "No changes"


def sync_library(library: Library, ocr: Optional[OcrEngine] = None, progress_cb: ProgressCB = None,
                 cancel: Optional[threading.Event] = None, process: bool = True) -> SyncReport:
    library.check_accessible()
    report = SyncReport()
    prog = Progress(stage="Synchronizing")
    conn = library.connect()
    try:
        settings = library.settings(conn)
        ocr = ocr or OcrEngine(settings["ocr_language"], dpi=settings["ocr_dpi"], min_chars=settings["min_text_chars"])

        def emit(msg, **kw):
            prog.message = msg
            for k, v in kw.items():
                setattr(prog, k, v)
            if progress_cb:
                progress_cb(prog)

        emit("Scanning library folder...")
        on_disk: Dict[str, os.stat_result] = {}
        for p in iter_pdfs(library.docs_dir, recursive=True):
            check_cancel(cancel)
            try:
                on_disk[to_rel(library.root, p)] = p.stat()
            except OSError as e:
                log.warning("Cannot stat %s: %s", p, e)
        docs = {r["relative_path"]: r for r in conn.execute("SELECT * FROM documents")}
        missing_info = {r["document_id"]: r for r in conn.execute("SELECT * FROM missing_documents")}

        unplaced = []   # DB documents whose file is not at its recorded path
        prog.total = len(docs) + len(on_disk)
        for n, (rel, row) in enumerate(docs.items()):
            check_cancel(cancel)
            prog.done = n
            st = on_disk.get(rel)
            if st is None:
                unplaced.append(row)
                continue
            _check_present(library, conn, rel, row, st, missing_info.get(row["document_id"]), report, cancel)

        # ---- new files / renames --------------------------------------------------
        new_files = [r for r in on_disk if r not in docs]
        by_hash: Dict[str, list] = {}
        for row in unplaced:
            if row["content_hash"]:
                by_hash.setdefault(row["content_hash"], []).append(row)
        claimed = set()
        for n, rel in enumerate(new_files):
            check_cancel(cancel)
            st = on_disk[rel]
            prog.done, prog.current = len(docs) + n, rel
            emit(f"Checking new file {rel}")
            path = library.abs_path(rel)
            try:
                h = sha256_file(path, cancel)
            except OSError as e:
                report.failed.append(f"{rel}: {e}")
                continue
            match = next((r for r in by_hash.get(h, []) if r["document_id"] not in claimed), None)
            if match:                                   # same content at a new path: rename / restore elsewhere
                claimed.add(match["document_id"])
                with transaction(conn):
                    conn.execute("UPDATE documents SET relative_path=?, filename=?, file_size=?, modified_ns=?, "
                                 "availability_status='available', last_verified_timestamp=? WHERE document_id=?",
                                 (rel, Path(rel).name, st.st_size, st.st_mtime_ns, now(), match["document_id"]))
                    conn.execute("DELETE FROM missing_documents WHERE document_id=?", (match["document_id"],))
                report.renamed.append(f"{match['filename']} -> {Path(rel).name}")
                continue
            did = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO documents(document_id,filename,relative_path,file_size,modified_ns,content_hash,"
                "import_timestamp,processing_status,availability_status,last_verified_timestamp) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (did, Path(rel).name, rel, st.st_size, st.st_mtime_ns, h, now(), S.ST_IMPORTED, S.AV_AVAILABLE, now()))
            report.added.append(rel)

        # ---- files that are gone -------------------------------------------------
        days = float(settings.get("missing_file_recovery_days", 7))
        for row in unplaced:
            if row["document_id"] in claimed:
                continue
            _handle_missing(library, conn, row, missing_info.get(row["document_id"]), days, report)

        # ---- (re)process what needs it ----------------------------------------------
        if process:
            states = list(S.PENDING_STATUSES)
            if settings.get("ocr_enabled", True) and ocr.available():
                states.append(S.ST_WAITING)
            q = ",".join("?" * len(states))
            todo = conn.execute(f"SELECT * FROM documents WHERE availability_status='available' AND "
                                f"processing_status IN ({q}) ORDER BY import_timestamp", states).fetchall()
            prog.total, prog.done = len(todo), 0
            for n, row in enumerate(todo):
                check_cancel(cancel)
                prog.done, prog.current = n, row["filename"]
                emit(f"Indexing {row['filename']}")
                path = library.abs_path(row["relative_path"])
                res = process_document(conn, row["document_id"], path, settings, ocr, cancel,
                                       reuse_ok_pages=(row["processing_status"] == S.ST_WAITING),
                                       page_cb=lambda i, k, name=row["filename"]: emit(f"Indexing {name}: page {i}/{k}"))
                report.processed += 1
                if res.status == S.ST_FAILED:
                    report.failed.append(f"{row['filename']}: {res.error}")
                    prog.failed += 1
                elif res.cancelled:
                    raise Cancelled()
                else:
                    prog.success += 1
        emit("Synchronization finished", done=prog.total)
    except Cancelled:
        report.cancelled = True
    finally:
        conn.close()
    return report


def _check_present(library, conn, rel, row, st, missing, report, cancel):
    """File exists at its recorded path: unchanged / touched / modified / restored."""
    did = row["document_id"]
    was_missing = row["availability_status"] == S.AV_MISSING
    same_stat = st.st_size == row["file_size"] and st.st_mtime_ns == row["modified_ns"]
    if same_stat and not was_missing:
        conn.execute("UPDATE documents SET last_verified_timestamp=? WHERE document_id=?", (now(), did))
        return
    path = library.abs_path(rel)
    try:
        h = sha256_file(path, cancel)
    except OSError as e:
        conn.execute("UPDATE documents SET availability_status=? WHERE document_id=?", (S.AV_INACCESSIBLE, did))
        report.failed.append(f"{row['filename']}: {e}")
        return
    unchanged = h == row["content_hash"]
    with transaction(conn):
        if was_missing:
            conn.execute("DELETE FROM missing_documents WHERE document_id=?", (did,))
        if unchanged:
            conn.execute("UPDATE documents SET file_size=?, modified_ns=?, availability_status='available', "
                         "last_verified_timestamp=? WHERE document_id=?", (st.st_size, st.st_mtime_ns, now(), did))
            if row["processing_status"] in (S.ST_PROCESSING, S.ST_OCR, S.ST_TEXT):
                conn.execute("UPDATE documents SET processing_status=? WHERE document_id=?", (S.ST_IMPORTED, did))
        else:
            # invalidate stale text/index immediately; new data is written only after successful processing
            remove_document_pages(conn, did)
            conn.execute("UPDATE documents SET processing_status=?, availability_status='available', page_count=0, "
                         "ocr_pages=0, last_error=NULL WHERE document_id=?", (S.ST_MODIFIED, did))
    if was_missing and unchanged:
        report.restored.append(row["filename"])
    elif was_missing:
        report.restored.append(f"{row['filename']} (changed; re-indexing)")
        report.modified.append(row["filename"])
    elif not unchanged:
        report.modified.append(row["filename"])


def _handle_missing(library, conn, row, missing, days, report):
    did = row["document_id"]
    t = now()
    if days <= 0:                                       # folder is the source of truth: forget it right away
        library.purge_document_records(conn, did)
        report.purged.append(row["filename"])
        return
    if row["availability_status"] != S.AV_MISSING or missing is None:
        with transaction(conn):
            conn.execute("UPDATE documents SET availability_status='missing' WHERE document_id=?", (did,))
            conn.execute("INSERT OR REPLACE INTO missing_documents(document_id,missing_since,recovery_deadline,"
                         "last_known_path,previous_file_hash,recovery_status) VALUES(?,?,?,?,?,'pending')",
                         (did, t, t + days * 86400, row["relative_path"], row["content_hash"]))
        report.marked_missing.append(row["filename"])
        return
    if missing["recovery_deadline"] <= t:
        library.purge_document_records(conn, did)
        report.purged.append(row["filename"])


def purge_missing(library: Library, document_ids: Optional[List[str]] = None) -> List[str]:
    """Permanently purge records of missing documents (all, or the given ids) before the grace period ends."""
    conn = library.connect()
    try:
        rows = conn.execute("SELECT document_id, filename FROM documents WHERE availability_status='missing'").fetchall()
        names = []
        for r in rows:
            if document_ids is None or r["document_id"] in document_ids:
                library.purge_document_records(conn, r["document_id"])
                names.append(r["filename"])
        return names
    finally:
        conn.close()
