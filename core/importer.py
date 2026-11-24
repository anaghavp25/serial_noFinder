"""Copy PDFs into the library (never move/delete originals), then process them."""
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from core.file_validator import validate_pdf
from core.library_manager import Library
from core.ocr_engine import OcrEngine
from core.pdf_processor import process_document
from core.progress import Progress, ProgressCB
from database import schema as S
from database.db_manager import now
from utils.file_hash import sha256_file
from utils.logging_utils import get_logger
from utils.path_utils import TMP_SUFFIX, is_within, iter_pdfs, to_rel, unique_destination

log = get_logger("importer")


@dataclass
class ImportItem:
    source: str
    outcome: str              # imported | duplicate | skipped | failed
    detail: str = ""
    document_id: Optional[str] = None
    status: str = ""          # processing status after import


@dataclass
class ImportReport:
    items: List[ImportItem] = field(default_factory=list)
    cancelled: bool = False

    def count(self, outcome: str) -> int:
        return sum(1 for i in self.items if i.outcome == outcome)


def collect_pdfs(sources: Iterable, include_subfolders: bool) -> Tuple[List[Path], List[ImportItem]]:
    files: List[Path] = []
    skipped: List[ImportItem] = []
    seen = set()
    for s in sources:
        p = Path(s)
        try:
            if p.is_dir():
                found = list(iter_pdfs(p, include_subfolders))
                if not found:
                    skipped.append(ImportItem(str(p), "skipped", "No PDF files found in folder"))
            elif p.is_file():
                if p.suffix.lower() != ".pdf":
                    skipped.append(ImportItem(str(p), "skipped", "Not a .pdf file"))
                    continue
                found = [p]
            else:
                skipped.append(ImportItem(str(p), "failed", "File or folder not found"))
                continue
        except OSError as e:
            skipped.append(ImportItem(str(p), "failed", f"Cannot access: {e}"))
            continue
        for f in found:
            key = os.path.normcase(str(f.resolve()))
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files, skipped


def _copy_verified(src: Path, dest: Path, src_hash: str) -> None:
    tmp = dest.with_name(dest.name + TMP_SUFFIX)
    try:
        shutil.copy2(src, tmp)
        if sha256_file(tmp) != src_hash:
            raise IOError("Copy verification failed (checksum mismatch)")
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def import_documents(library: Library, sources: Iterable, include_subfolders: bool = True,
                     allow_duplicates: bool = False, ocr: Optional[OcrEngine] = None,
                     progress_cb: ProgressCB = None, cancel: Optional[threading.Event] = None) -> ImportReport:
    report = ImportReport()
    files, pre = collect_pdfs(sources, include_subfolders)
    report.items.extend(pre)
    prog = Progress(stage="Importing", total=len(files), skipped=len(pre) and sum(1 for i in pre if i.outcome == "skipped"),
                    failed=sum(1 for i in pre if i.outcome == "failed"))
    conn = library.connect()
    try:
        settings = library.settings(conn)
        ocr = ocr or OcrEngine(settings["ocr_language"], dpi=settings["ocr_dpi"], min_chars=settings["min_text_chars"])
        for idx, src in enumerate(files):
            if cancel is not None and cancel.is_set():
                report.cancelled = True
                break
            prog.current, prog.done = src.name, idx
            prog.message = f"Importing {src.name}"
            if progress_cb:
                progress_cb(prog)
            item = _import_one(library, conn, src, settings, ocr, allow_duplicates, cancel, progress_cb, prog)
            report.items.append(item)
            if item.outcome == "imported":
                prog.success += 1
            elif item.outcome == "failed":
                prog.failed += 1
            else:
                prog.skipped += 1
        prog.done = prog.total if not report.cancelled else prog.done
        prog.message = "Import cancelled" if report.cancelled else "Import finished"
        if progress_cb:
            progress_cb(prog)
    finally:
        conn.close()
    return report


def _import_one(library, conn, src: Path, settings, ocr, allow_duplicates, cancel, progress_cb, prog) -> ImportItem:
    dest: Optional[Path] = None
    doc_id: Optional[str] = None
    try:
        if is_within(src, library.root):
            return ImportItem(str(src), "skipped", "File is already inside the library (use Sync Library)")
        v = validate_pdf(src)
        if not v.ok:
            return ImportItem(str(src), "failed", v.reason)
        h = sha256_file(src, cancel)
        dup = conn.execute("SELECT filename FROM documents WHERE content_hash=? AND availability_status='available'",
                           (h,)).fetchone()
        if dup and not allow_duplicates:
            return ImportItem(str(src), "duplicate", f"Identical to existing document '{dup['filename']}'")
        library.check_accessible()
        library.docs_dir.mkdir(exist_ok=True)
        dest = unique_destination(library.docs_dir, src.name)
        try:
            _copy_verified(src, dest, h)
        except OSError as e:
            code = "insufficient disk space" if getattr(e, "errno", None) == 28 else str(e)
            return ImportItem(str(src), "failed", f"Copy failed: {code}")
        st = dest.stat()
        doc_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO documents(document_id,filename,relative_path,original_import_path,file_size,modified_ns,"
            "content_hash,import_timestamp,page_count,processing_status,availability_status,last_verified_timestamp)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, dest.name, to_rel(library.root, dest), str(src), st.st_size, st.st_mtime_ns, h, now(),
             v.page_count, S.ST_IMPORTED, S.AV_AVAILABLE, now()))
        res = process_document(
            conn, doc_id, dest, settings, ocr, cancel, content_hash=h,
            page_cb=lambda i, n: progress_cb and progress_cb(_with(prog, f"{src.name}: page {i}/{n}")))
        detail = f"Stored as {dest.name}"
        if res.status in (S.ST_FAILED, S.ST_PARTIAL, S.ST_WAITING):
            detail += f" - indexing: {S.STATUS_LABELS.get(res.status, res.status)}" + (f" ({res.error})" if res.error else "")
        return ImportItem(str(src), "imported", detail, doc_id, res.status)
    except Exception as e:
        log.exception("Import failed for %s", src)
        if doc_id is None and dest is not None:   # copied but not registered -> remove our own copy
            try:
                dest.unlink()
            except OSError:
                pass
        return ImportItem(str(src), "failed", f"{type(e).__name__}: {e}")


def _with(prog: Progress, message: str) -> Progress:
    prog.message = message
    return prog
