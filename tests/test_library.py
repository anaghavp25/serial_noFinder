import os
import shutil
import time
from pathlib import Path

import pytest

from core.library_manager import Library, LibraryUnavailable
from core.library_sync import sync_library
from core.serial_search import SerialSearcher
from database.db_manager import DatabaseCorruptError, integrity_problems, set_setting
from database.search_index import tokenize
from tests.conftest import make_pdf


def search(lib, q, fuzzy=False):
    c = lib.connect()
    try:
        return SerialSearcher(c, lib.root, lib.settings(c)).search(q, fuzzy)
    finally:
        c.close()


def exact(lib, q):
    return search(lib, q)["exact"]


def doc(lib, name):
    c = lib.connect()
    try:
        r = c.execute("SELECT * FROM documents WHERE filename=?", (name,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def load(lib, no_ocr, **files):
    """Create PDFs in the library folder and sync (= what the app does automatically)."""
    for name, pages in files.items():
        make_pdf(lib.root / name.replace("__", " ").replace("_pdf", ".pdf"), pages)
    return sync_library(lib, no_ocr)


# ---------------------------------------------------------------- loading
def test_folder_is_loaded_automatically_with_odd_names_and_subfolders(lib, no_ocr):
    (lib.root / "sub dir").mkdir()
    make_pdf(lib.root / "Cert (1) & co #2.pdf", ["Serial A-1"])
    make_pdf(lib.root / "sub dir" / "Zeugnis äöü.pdf", ["Serial A-2"])
    (lib.root / "notes.txt").write_text("not a pdf")
    rep = sync_library(lib, no_ocr)
    assert len(rep.added) == 2 and not rep.failed
    assert exact(lib, "A-1")[0].filename == "Cert (1) & co #2.pdf"
    assert exact(lib, "A-2")[0].relative_path == "sub dir/Zeugnis äöü.pdf"
    assert sync_library(lib, no_ocr).processed == 0                    # incremental
    assert not (lib.root / ".pdfsf" / "x").exists()


def test_same_content_twice_does_not_break_and_originals_untouched(lib, no_ocr):
    a = make_pdf(lib.root / "a.pdf", ["Serial D-1"])
    shutil.copy(a, lib.root / "b.pdf")
    before = a.read_bytes()
    sync_library(lib, no_ocr)
    assert a.read_bytes() == before
    assert len(exact(lib, "D-1")) == 2


def test_unreadable_pdfs_are_flagged_not_fatal(lib, no_ocr):
    (lib.root / "broken.pdf").write_bytes(b"%PDF-1.4 garbage")
    (lib.root / "empty.pdf").write_bytes(b"")
    make_pdf(lib.root / "good.pdf", ["Serial G-1"])
    rep = sync_library(lib, no_ocr)
    assert doc(lib, "broken.pdf")["processing_status"] == "failed"
    assert doc(lib, "empty.pdf")["processing_status"] == "failed"
    assert exact(lib, "G-1")


# ---------------------------------------------------------------- search
def test_exact_matching_rules(lib, no_ocr):
    load(lib, no_ocr, t_pdf=["Certificate A-3837 here", "Other: A-38370 and XA-3837B and BA-3837 and CERT-A-3837"])
    res = exact(lib, "A-3837")
    assert len(res) == 1 and [m.page_number for m in res[0].matches] == [1]
    assert exact(lib, "a-3837") and exact(lib, "  A-3837.  ")
    assert not exact(lib, "A3837") and not exact(lib, "3837")
    assert [m.page_number for m in exact(lib, "A-38370")[0].matches] == [2]


def test_serials_on_any_page_and_in_several_documents(lib, no_ocr):
    load(lib, no_ocr, a_pdf=["SN 7788-ZZ first", "nothing", "again 7788-ZZ page three"],
         b_pdf=["filler", "SN 7788-ZZ second"], c_pdf=["unrelated 1234"])
    res = exact(lib, "7788-zz")
    assert [d.filename for d in res] == ["a.pdf", "b.pdf"]
    assert [m.page_number for m in res[0].matches] == [1, 3]
    assert [m.page_number for m in res[1].matches] == [2] and res[0].page_count == 3


def test_header_repeated_on_every_page(lib, no_ocr):
    make_pdf(lib.root / "h.pdf", ["x", "y", "z"], extra_header="HDR 5150-Q")
    sync_library(lib, no_ocr)
    assert [m.page_number for m in exact(lib, "5150-q")[0].matches] == [1, 2, 3]


def test_tokenizer_unit():
    assert tokenize("No.A-3837, (B_12/7)") == ["no", "a-3837", "b_12/7"]
    assert tokenize("A\u20133837") == ["a-3837"]


# ---------------------------------------------------------------- folder changes
def test_added_modified_renamed_deleted_files_follow_the_folder(lib, no_ocr):
    f = make_pdf(lib.root / "a.pdf", ["Serial K-9001"])
    sync_library(lib, no_ocr)
    make_pdf(lib.root / "new.pdf", ["Serial D-5555"])                       # added
    assert sync_library(lib, no_ocr).added == ["new.pdf"] and exact(lib, "D-5555")
    time.sleep(0.01)
    make_pdf(f, ["Replaced M-7777", "page two M-8888"])                     # modified
    rep = sync_library(lib, no_ocr)
    assert rep.modified == ["a.pdf"] and not exact(lib, "K-9001") and exact(lib, "M-8888")
    f.rename(lib.root / "renamed.pdf")                                      # renamed keeps its index
    rep = sync_library(lib, no_ocr)
    assert rep.renamed and rep.processed == 0 and exact(lib, "M-7777")[0].filename == "renamed.pdf"
    (lib.root / "renamed.pdf").unlink()                                     # deleted -> forgotten at once
    rep = sync_library(lib, no_ocr)
    assert rep.purged == ["renamed.pdf"] and not exact(lib, "M-7777") and not doc(lib, "renamed.pdf")
    c = lib.connect()
    assert c.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 1   # only new.pdf's page remains
    c.close()


def test_optional_grace_period_still_works(lib, no_ocr):
    c = lib.connect(); set_setting(c, "missing_file_recovery_days", 7); c.close()
    f = make_pdf(lib.root / "a.pdf", ["Serial K-1"]); data = f.read_bytes()
    sync_library(lib, no_ocr)
    f.unlink()
    assert sync_library(lib, no_ocr).marked_missing == ["a.pdf"] and not exact(lib, "K-1")
    f.write_bytes(data)
    rep = sync_library(lib, no_ocr)
    assert rep.restored == ["a.pdf"] and rep.processed == 0 and exact(lib, "K-1")


def test_touch_is_not_reindexed_and_interrupted_docs_resume(lib, no_ocr):
    f = make_pdf(lib.root / "a.pdf", ["Serial K-9001"])
    sync_library(lib, no_ocr)
    os.utime(f, (time.time() + 100, time.time() + 100))
    assert sync_library(lib, no_ocr).processed == 0
    c = lib.connect()
    c.execute("UPDATE documents SET processing_status='processing'")
    c.execute("DELETE FROM pages_fts"); c.execute("DELETE FROM pages")
    c.close()
    assert not exact(lib, "K-9001")
    sync_library(lib, no_ocr)
    assert exact(lib, "K-9001")


def test_delete_removes_file_records_and_index(lib, no_ocr):
    f = make_pdf(lib.root / "a.pdf", ["Serial K-1"])
    keep = make_pdf(lib.root / "b.pdf", ["Serial K-2"])
    sync_library(lib, no_ocr)
    c = lib.connect()
    out = lib.delete_documents(c, [doc(lib, "a.pdf")["document_id"]])
    c.close()
    assert out["deleted"] == ["a.pdf"] and not f.exists() and keep.exists()
    assert not exact(lib, "K-1") and exact(lib, "K-2")
    assert sync_library(lib, no_ocr).summary() == "No changes"


def test_delete_never_touches_files_outside_the_folder(lib, no_ocr, tmp_path):
    outside = make_pdf(tmp_path / "outside.pdf", ["x"])
    sync_library(lib, no_ocr)
    c = lib.connect()
    c.execute("INSERT INTO documents(document_id,filename,relative_path) VALUES('evil','outside.pdf','../outside.pdf')")
    out = lib.delete_documents(c, ["evil"])
    c.close()
    assert outside.exists() and out["errors"]


# ---------------------------------------------------------------- directory handling / robustness
def test_missing_folder_is_never_silently_recreated(tmp_path):
    root = tmp_path / "drive" / "certs"; root.mkdir(parents=True)
    Library.open(root)
    shutil.rmtree(root)
    with pytest.raises(LibraryUnavailable):
        Library.open(root)
    assert not root.exists()


def test_reopening_keeps_index(lib, no_ocr):
    load(lib, no_ocr, a_pdf=["Serial K-9001"])
    again = Library.open(lib.root)
    assert exact(again, "K-9001") and sync_library(again, no_ocr).processed == 0


def test_corrupt_database_is_rebuilt_from_the_folder(lib, no_ocr):
    load(lib, no_ocr, a_pdf=["Serial K-9001"])
    for s in ("-wal", "-shm"):
        Path(str(lib.db_path) + s).unlink(missing_ok=True)
    lib.db_path.write_bytes(b"not a sqlite database" * 100)
    with pytest.raises(DatabaseCorruptError):
        Library.open(lib.root)
    l2 = Library(lib.root); l2.recover_database()
    assert len(sync_library(l2, no_ocr).added) == 1 and exact(l2, "K-9001")
    c = l2.connect(); assert integrity_problems(c) == []; c.close()


def test_fuzzy_core_still_separates_results(lib, no_ocr):
    load(lib, no_ocr, a_pdf=["Serial AB-12345 ok"], b_pdf=["Serial AB-12346 ok"])
    r = search(lib, "AB-12345", fuzzy=True)
    assert [d.filename for d in r["exact"]] == ["a.pdf"] and [d.filename for d in r["approximate"]] == ["b.pdf"]
