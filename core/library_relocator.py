"""Safe library relocation: copy -> verify -> (caller) switch. The old library is never touched
until the user explicitly asks for cleanup after a verified move."""
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.library_manager import (DOCS_DIR, MARKER, META_DIR, Library, LibraryError, inspect_folder, write_marker)
from core.progress import Cancelled, Progress, ProgressCB
from database.db_manager import Database, integrity_problems
from utils.file_hash import sha256_file
from utils.logging_utils import get_logger
from utils.path_utils import TMP_SUFFIX, dir_size, free_space, is_within

log = get_logger("relocator")


class RelocationError(LibraryError):
    pass


@dataclass
class TargetCheck:
    ok: bool
    state: str = ""
    problems: List[str] = field(default_factory=list)
    required_bytes: int = 0
    free_bytes: int = 0


def _writable(folder: Path) -> Optional[str]:
    probe = folder / f".pdfsf-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
        return None
    except OSError as e:
        return f"Folder is not writable: {e}"


def validate_target(source: Optional[Library], target, mode: str) -> TargetCheck:
    """mode: 'move' (Option A) or 'switch' (Option B)."""
    target = Path(target)
    chk = TargetCheck(ok=False)
    st = inspect_folder(target) if target.exists() else None
    chk.state = st.state if st else "missing"
    if st and st.state in ("not_directory", "unreadable"):
        chk.problems.append(st.detail or "Folder is not accessible")
        return chk
    if source is not None:
        if target.resolve() == source.root.resolve():
            chk.problems.append("This is already the current library folder")
            return chk
        if is_within(target, source.root) or is_within(source.root, target):
            chk.problems.append("The new folder cannot be inside the current library (or contain it)")
            return chk
    if st is None:
        parent = target
        while not parent.exists() and parent.parent != parent:
            parent = parent.parent
        if not parent.exists():
            chk.problems.append("Drive or parent folder does not exist")
            return chk
        err = _writable(parent)
        if err:
            chk.problems.append(err)
            return chk
    else:
        err = _writable(target)
        if err:
            chk.problems.append(err)
            return chk
    if mode == "move":
        if chk.state == "library":
            chk.problems.append("This folder already contains a library. Use 'Switch to a different library' instead.")
        elif chk.state == "foreign":
            chk.problems.append("For moving, choose an empty or new folder (this one already contains other files).")
        if source is not None:
            chk.required_bytes = int((dir_size(source.docs_dir) + dir_size(source.meta_dir)) * 1.05) + 10_000_000
            chk.free_bytes = free_space(target)
            if chk.free_bytes < chk.required_bytes:
                chk.problems.append("Not enough free disk space at the destination")
    chk.ok = not chk.problems
    return chk


def move_library(library: Library, target, progress_cb: ProgressCB = None,
                 cancel: Optional[threading.Event] = None) -> Library:
    """Copy the whole library to `target`, verify it, and return the new Library.
    The caller switches the active-library setting only when this returns."""
    target = Path(target)
    library.check_accessible()
    chk = validate_target(library, target, "move")
    if not chk.ok:
        raise RelocationError("; ".join(chk.problems))
    created_files: List[Path] = []
    created_dirs: List[Path] = []
    prog = Progress(stage="Moving library")

    def mkdir(d: Path):
        chain = []
        while not d.exists():
            chain.append(d)
            d = d.parent
        for c in reversed(chain):
            c.mkdir()
            created_dirs.append(c)

    try:
        mkdir(target)
        new_docs = target / DOCS_DIR
        mkdir(new_docs)
        conn = library.connect()
        try:
            known = {r["relative_path"]: r for r in conn.execute("SELECT relative_path, content_hash, file_size FROM documents")}
            n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        finally:
            conn.close()
        files = [Path(dp) / f for dp, _, fns in os.walk(library.docs_dir) for f in fns if not f.endswith(TMP_SUFFIX)]
        prog.total = len(files) + 2
        for i, src in enumerate(files):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            prog.done, prog.current = i, src.name
            prog.message = f"Copying {src.name}"
            if progress_cb:
                progress_cb(prog)
            rel_in_docs = src.relative_to(library.docs_dir)
            dest = new_docs / rel_in_docs
            if not dest.parent.exists():
                mkdir(dest.parent)
            shutil.copy2(src, dest)
            created_files.append(dest)
            if sha256_file(src, cancel) != sha256_file(dest, cancel) or src.stat().st_size != dest.stat().st_size:
                raise RelocationError(f"Verification failed for {rel_in_docs}")
        # metadata: consistent snapshot through SQLite's backup API (relative paths => nothing to rewrite)
        prog.message, prog.done = "Copying index database", len(files)
        if progress_cb:
            progress_cb(prog)
        meta = target / META_DIR
        mkdir(meta)
        (meta / "cache" / "thumbs").mkdir(parents=True)
        created_dirs.extend([meta / "cache", meta / "cache" / "thumbs"])
        new_db = meta / "library.db"
        created_files.append(new_db)
        src_conn = library.connect()
        dst = Database(new_db).connect()
        try:
            src_conn.backup(dst)
        finally:
            dst.close()
            src_conn.close()
        # verification of the copy
        prog.message, prog.done = "Verifying", len(files) + 1
        if progress_cb:
            progress_cb(prog)
        v = Database(new_db).connect()
        try:
            problems = integrity_problems(v)
            if problems:
                raise RelocationError("Copied database failed integrity check: " + "; ".join(problems[:3]))
            rows = v.execute("SELECT relative_path, file_size, availability_status FROM documents").fetchall()
            if len(rows) != n_docs:
                raise RelocationError("Document count mismatch after copy")
            for r in rows:
                if r["availability_status"] == "available":
                    p = target / r["relative_path"]
                    if not p.is_file() or p.stat().st_size != r["file_size"]:
                        raise RelocationError(f"File missing or different in new location: {r['relative_path']}")
        finally:
            v.close()
        write_marker(target, library.library_id or None)      # last step: only now is it a library
        created_files.append(target / MARKER)
        new_lib = Library.open(target)
        prog.message, prog.done = "Library moved", prog.total
        if progress_cb:
            progress_cb(prog)
        return new_lib
    except BaseException as e:
        log.warning("Relocation failed, rolling back: %s", e)
        for f in reversed(created_files):
            try:
                f.unlink()
            except OSError:
                pass
        for suffix in ("-wal", "-shm"):
            try:
                Path(str(target / META_DIR / "library.db") + suffix).unlink()
            except OSError:
                pass
        for d in reversed(created_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        if isinstance(e, (Cancelled, RelocationError)):
            raise
        raise RelocationError(f"{type(e).__name__}: {e}")


def switch_library(target) -> Library:
    """Option B: open an existing library, or create a fresh one in an empty/other folder. No merging."""
    st = inspect_folder(target)
    if st.state == "library":
        return Library.open(target)
    if st.state in ("empty", "missing", "foreign"):
        err = None
        if st.state != "missing":
            err = _writable(Path(target))
        if err:
            raise RelocationError(err)
        return Library.create(target)    # in a non-empty folder only documents/ + .pdfsf/ + marker are added
    raise RelocationError(st.detail or f"Folder not usable ({st.state})")


def cleanup_old_library(old_root) -> List[str]:
    """Delete ONLY library-owned content (documents/, .pdfsf/, marker) of a verified-moved library."""
    old_root = Path(old_root)
    if inspect_folder(old_root).state != "library":
        raise RelocationError("Not a library folder; nothing was deleted")
    removed = []
    for name in (DOCS_DIR, META_DIR):
        p = old_root / name
        if p.exists():
            shutil.rmtree(p)
            removed.append(name)
    (old_root / MARKER).unlink(missing_ok=True)
    removed.append(MARKER)
    try:
        old_root.rmdir()          # only if now empty
    except OSError:
        pass
    return removed
