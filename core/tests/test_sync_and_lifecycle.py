import os
import shutil
import time
from pathlib import Path

import pytest

from core.importer import import_documents
from core.library_manager import Library, LibraryUnavailable, NotALibrary, inspect_folder
from core.library_sync import purge_missing, sync_library
from core.serial_search import SerialSearcher
from database.db_manager import integrity_problems, set_setting
from tests.conftest import make_pdf


def count(lib, q):
    c = lib.connect()
    try:
        return len(SerialSearcher(c, lib.root).search_exact(q))
    finally:
        c.close()


def status(lib, filename):
    c = lib.connect()
    try:
        r = c.execute("SELECT * FROM documents WHERE filename=?", (filename,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def setup_one(lib, make, no_ocr, name="a.pdf", text="Serial K-9001"):
    import_documents(lib, [make(name, [text])], ocr=no_ocr)
    return lib.docs_dir / name


def test_external_addition_is_indexed(lib, no_ocr):
    make_pdf(lib.docs_dir / "dropped.pdf", ["Serial D-5555"])
    rep = sync_library(lib, no_ocr)
    assert rep.added == ["documents/dropped.pdf"]
    assert count(lib, "D-5555") == 1
    assert sync_library(lib, no_ocr).summary() == "No changes"        # incremental: nothing reprocessed
    assert sync_library(lib, no_ocr).processed == 0


def test_external_deletion_missing_restore_and_purge(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    backup = f.read_bytes()
    f.unlink()
    rep = sync_library(lib, no_ocr)
    assert rep.marked_missing == ["a.pdf"]
    assert count(lib, "K-9001") == 0                                   # gone from search at once
    d = status(lib, "a.pdf")
    assert d["availability_status"] == "missing"
    # restoration (unchanged) keeps existing index without re-processing
    f.write_bytes(backup)
    rep = sync_library(lib, no_ocr)
    assert rep.restored == ["a.pdf"] and rep.processed == 0
    assert count(lib, "K-9001") == 1
    # delete again, purge explicitly
    f.unlink()
    sync_library(lib, no_ocr)
    assert purge_missing(lib) == ["a.pdf"]
    assert status(lib, "a.pdf") is None
    c = lib.connect()
    assert c.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM page_tokens").fetchone()[0] == 0
    c.close()
    # restored after purge => brand new document, indexed again
    f.write_bytes(backup)
    rep = sync_library(lib, no_ocr)
    assert len(rep.added) == 1 and count(lib, "K-9001") == 1


def test_grace_period_expiry_purges_automatically(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    f.unlink()
    sync_library(lib, no_ocr)
    c = lib.connect()
    c.execute("UPDATE missing_documents SET recovery_deadline = ?", (time.time() - 5,))
    c.close()
    rep = sync_library(lib, no_ocr)
    assert rep.purged == ["a.pdf"] and status(lib, "a.pdf") is None


def test_restore_with_changed_content_is_reprocessed(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    f.unlink()
    sync_library(lib, no_ocr)
    make_pdf(f, ["Now serial N-0001"])
    rep = sync_library(lib, no_ocr)
    assert rep.modified == ["a.pdf"]
    assert count(lib, "N-0001") == 1 and count(lib, "K-9001") == 0


def test_external_modification_replaces_old_index(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    time.sleep(0.01)
    make_pdf(f, ["Replaced M-7777", "page two M-8888"])
    rep = sync_library(lib, no_ocr)
    assert rep.modified == ["a.pdf"]
    assert count(lib, "K-9001") == 0 and count(lib, "M-8888") == 1
    assert status(lib, "a.pdf")["page_count"] == 2


def test_touch_without_change_is_not_reindexed(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    os.utime(f, (time.time() + 100, time.time() + 100))
    rep = sync_library(lib, no_ocr)
    assert rep.processed == 0 and not rep.modified


def test_rename_detected_and_index_kept(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    f.rename(f.with_name("renamed.pdf"))
    rep = sync_library(lib, no_ocr)
    assert rep.renamed and rep.processed == 0 and not rep.marked_missing
    assert count(lib, "K-9001") == 1 and status(lib, "renamed.pdf")


def test_interrupted_processing_is_resumed_by_sync(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    c = lib.connect()
    c.execute("UPDATE documents SET processing_status='processing'")
    c.execute("DELETE FROM pages_fts"); c.execute("DELETE FROM pages")
    c.close()
    assert count(lib, "K-9001") == 0
    sync_library(lib, no_ocr)
    assert count(lib, "K-9001") == 1


def test_remove_from_app_keeps_file_and_sync_does_not_readd(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    c = lib.connect()
    did = status(lib, "a.pdf")["document_id"]
    lib.remove_documents(c, [did], delete_files=False)
    c.close()
    assert f.exists() and count(lib, "K-9001") == 0
    assert sync_library(lib, no_ocr).added == []
    c = lib.connect(); lib.clear_ignored(c); c.close()
    assert len(sync_library(lib, no_ocr).added) == 1


def test_remove_with_permanent_delete(lib, make, no_ocr):
    f = setup_one(lib, make, no_ocr)
    c = lib.connect()
    lib.remove_documents(c, [status(lib, "a.pdf")["document_id"]], delete_files=True)
    c.close()
    assert not f.exists() and status(lib, "a.pdf") is None


def test_rebuild_index_and_integrity(lib, make, no_ocr):
    setup_one(lib, make, no_ocr)
    from database.search_index import rebuild_index
    c = lib.connect()
    c.execute("DELETE FROM pages_fts")
    assert integrity_problems(c)                       # missing FTS rows are detected
    rebuild_index(c)
    assert integrity_problems(c) == []
    c.close()
    assert count(lib, "K-9001") == 1


def test_missing_library_never_silently_recreated(tmp_path):
    root = tmp_path / "drive" / "lib"
    lib = Library.create(root)
    shutil.rmtree(root)
    with pytest.raises(LibraryUnavailable):
        Library.open(root)
    assert not root.exists()                            # nothing was created behind the user's back
    root.mkdir(parents=True)
    with pytest.raises(NotALibrary):                    # right folder name, wrong content
        Library.open(root)
    assert inspect_folder(root).state == "empty"


def test_corrupt_database_detected_and_recoverable(lib, make, no_ocr):
    from database.db_manager import DatabaseCorruptError
    setup_one(lib, make, no_ocr)
    for s in ("-wal", "-shm"):
        Path(str(lib.db_path) + s).unlink(missing_ok=True)
    lib.db_path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(DatabaseCorruptError):
        Library.open(lib.root)
    l2 = Library(lib.root)
    l2.recover_database()
    rep = sync_library(l2, no_ocr)
    assert len(rep.added) == 1
    c = l2.connect()
    assert len(SerialSearcher(c, l2.root).search_exact("K-9001")) == 1
    c.close()
