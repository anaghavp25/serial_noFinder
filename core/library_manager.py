"""The library IS the folder the user selects: PDFs are indexed in place.
Only a hidden `.pdfsf/` folder (index database + thumbnail cache) is added inside it."""
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List

from database.db_manager import Database, DatabaseCorruptError, get_settings, integrity_problems, transaction
from database.search_index import remove_document_pages
from utils.logging_utils import get_logger
from utils.path_utils import is_within, resolve_rel

log = get_logger("library")

META_DIR = ".pdfsf"


class LibraryError(Exception):
    pass


class LibraryUnavailable(LibraryError):
    """Folder missing / disconnected / not writable. Never 'fixed' by silently picking another folder."""


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.docs_dir = self.root
        self.meta_dir = self.root / META_DIR
        self.db_path = self.meta_dir / "library.db"
        self.thumbs_dir = self.meta_dir / "cache" / "thumbs"
        self.db = Database(self.db_path)

    @classmethod
    def open(cls, root) -> "Library":
        root = Path(root)
        try:
            if not root.is_dir():
                raise LibraryUnavailable(f"The folder cannot be found:\n{root}")
            os.listdir(root)
        except OSError as e:
            raise LibraryUnavailable(f"The folder cannot be read:\n{root}\n{e}")
        lib = cls(root)
        try:
            lib.thumbs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LibraryUnavailable(f"The app cannot write to this folder:\n{root}\n{e}\nChoose a folder you can modify.")
        if sys.platform.startswith("win"):       # keep the helper folder out of sight
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(lib.meta_dir), 0x02)
            except Exception:
                pass
        if not lib.db_path.exists():
            lib.db.initialize()
        else:
            lib.check_database()
        return lib

    def check_database(self) -> None:
        try:
            self.db.initialize()
            conn = self.db.connect()
            try:
                problems = integrity_problems(conn)
            finally:
                conn.close()
        except (sqlite3.DatabaseError, DatabaseCorruptError) as e:
            raise DatabaseCorruptError(str(e))
        if problems:
            raise DatabaseCorruptError("; ".join(problems[:5]))

    def recover_database(self) -> None:
        """The folder is the source of truth, so a damaged index is simply rebuilt from the PDFs."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.db_path) + suffix)
            if p.exists():
                try:
                    p.rename(p.with_name(p.name + f".corrupt-{stamp}"))
                except OSError:
                    p.unlink(missing_ok=True)
        self.db.initialize()

    # ------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        return self.db.connect()

    def abs_path(self, relative_path: str) -> Path:
        return resolve_rel(self.root, relative_path)

    def settings(self, conn=None) -> dict:
        own = conn is None
        conn = conn or self.connect()
        try:
            return get_settings(conn)
        finally:
            if own:
                conn.close()

    def check_accessible(self) -> None:
        if not self.root.is_dir():
            raise LibraryUnavailable(f"The library folder is not available:\n{self.root}")

    def purge_document_records(self, conn: sqlite3.Connection, document_id: str) -> None:
        """Remove a document's record, text, index entries and thumbnails."""
        with transaction(conn):
            remove_document_pages(conn, document_id)
            conn.execute("DELETE FROM missing_documents WHERE document_id=?", (document_id,))
            conn.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
        if self.thumbs_dir.exists():
            for p in self.thumbs_dir.glob(f"{document_id}_*"):
                try:
                    p.unlink()
                except OSError:
                    pass

    def delete_documents(self, conn: sqlite3.Connection, document_ids: Iterable[str]) -> Dict[str, List[str]]:
        """Delete PDFs from the selected folder (permanently) and forget them. Nothing outside the folder is touched."""
        out = {"deleted": [], "errors": []}
        for did in document_ids:
            row = conn.execute("SELECT * FROM documents WHERE document_id=?", (did,)).fetchone()
            if not row:
                continue
            try:
                path = self.abs_path(row["relative_path"])
                if not is_within(path, self.root):
                    raise LibraryError("Refusing to delete a file outside the library folder")
                if path.exists():
                    path.unlink()
                self.purge_document_records(conn, did)
                out["deleted"].append(row["filename"])
            except Exception as e:
                log.exception("Delete failed for %s", did)
                out["errors"].append(f"{row['filename']}: {e}")
        return out
