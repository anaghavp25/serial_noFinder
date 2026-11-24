import os
from pathlib import Path

from core.importer import import_documents
from core.serial_search import SerialSearcher
from core.file_validator import validate_pdf
from database.search_index import tokenize
from tests.conftest import make_pdf


def run_import(lib, files, ocr, **kw):
    return import_documents(lib, files, ocr=ocr, **kw)


def search(lib, q, fuzzy=False):
    conn = lib.connect()
    try:
        return SerialSearcher(conn, lib.root, lib.settings(conn)).search(q, fuzzy)
    finally:
        conn.close()


def test_import_copies_and_keeps_original(lib, make, no_ocr, src):
    p = make("Certificate.pdf", ["Serial A-3837 issued"])
    before = p.read_bytes()
    rep = run_import(lib, [p], no_ocr)
    assert rep.count("imported") == 1
    assert p.exists() and p.read_bytes() == before            # original untouched
    assert len(list(lib.docs_dir.glob("*.pdf"))) == 1
    p.unlink()                                                  # deleting the source does not affect the library
    assert search(lib, "A-3837")["exact"]


def test_duplicate_filenames_both_kept_and_exact_duplicates_skipped(lib, tmp_path, no_ocr):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    make_pdf(a / "Certificate.pdf", ["Serial X-1111"])
    make_pdf(b / "Certificate.pdf", ["Serial X-2222"])
    rep = run_import(lib, [a / "Certificate.pdf", b / "Certificate.pdf"], no_ocr)
    assert rep.count("imported") == 2
    names = sorted(p.name for p in lib.docs_dir.glob("*.pdf"))
    assert names == ["Certificate (2).pdf", "Certificate.pdf"]
    # identical content again -> skipped as duplicate, unless explicitly allowed
    rep2 = run_import(lib, [a / "Certificate.pdf"], no_ocr)
    assert rep2.count("duplicate") == 1
    rep3 = run_import(lib, [a / "Certificate.pdf"], no_ocr, allow_duplicates=True)
    assert rep3.count("imported") == 1
    assert len(list(lib.docs_dir.glob("*.pdf"))) == 3


def test_bulk_import_nested_folders_and_failures_do_not_stop_batch(lib, src, no_ocr):
    (src / "sub" / "deeper").mkdir(parents=True)
    make_pdf(src / "one.pdf", ["ID Q-100"])
    make_pdf(src / "sub" / "two.pdf", ["ID Q-200"])
    make_pdf(src / "sub" / "deeper" / "three.pdf", ["ID Q-300"])
    (src / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    (src / "empty.pdf").write_bytes(b"")
    rep = run_import(lib, [src], no_ocr, include_subfolders=True)
    assert rep.count("imported") == 3 and rep.count("failed") == 2
    flat = run_import(lib, [src], no_ocr, include_subfolders=False)
    assert flat.count("duplicate") == 1


def test_validation_encrypted_and_zero_page(tmp_path):
    from utils.fitz_compat import fitz
    d = fitz.open(); d.new_page(); enc = tmp_path / "enc.pdf"
    d.save(str(enc), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner"); d.close()
    r = validate_pdf(enc)
    assert not r.ok and r.code == "encrypted"
    assert validate_pdf(tmp_path / "nope.pdf").code == "missing"


def test_exact_matching_rules(lib, make, no_ocr):
    pages = ["Certificate A-3837 here",
             "Other ids: A-38370 and XA-3837B and BA-3837 and CERT-A-3837"]
    run_import(lib, [make("t.pdf", pages)], no_ocr)
    res = search(lib, "A-3837")["exact"]
    assert len(res) == 1
    assert [m.page_number for m in res[0].matches] == [1]          # page 2 only has look-alikes
    assert search(lib, "a-3837")["exact"]                           # case-insensitive
    assert search(lib, "  A-3837.  ")["exact"]                      # surrounding punctuation/space ignored
    assert not search(lib, "A3837")["exact"]                        # hyphen is significant
    assert not search(lib, "3837")["exact"]                         # no partial matches
    assert [m.page_number for m in search(lib, "A-38370")["exact"][0].matches] == [2]


def test_serial_positions_multiple_pages_and_documents(lib, make, no_ocr):
    a = make("a.pdf", ["SN 7788-ZZ first page", "nothing", "again 7788-ZZ on page three"], extra_header="HDR 7788-ZZ")
    b = make("b.pdf", ["filler", "SN 7788-ZZ on second"])
    c = make("c.pdf", ["unrelated 1234"])
    run_import(lib, [a, b, c], no_ocr)
    res = search(lib, "7788-zz")["exact"]
    assert [d.filename for d in res] == ["a.pdf", "b.pdf"]
    assert [m.page_number for m in res[0].matches] == [1, 2, 3]   # header repeats the serial on every page
    assert [m.page_number for m in res[1].matches] == [2]
    assert res[0].page_count == 3
    assert res[0].matches[0].snippet


def test_fuzzy_is_separate_and_labelled(lib, make, no_ocr):
    run_import(lib, [make("a.pdf", ["Serial AB-12345 ok"]), make("b.pdf", ["Serial AB-12346 ok"])], no_ocr)
    r = search(lib, "AB-12345", fuzzy=True)
    assert [d.filename for d in r["exact"]] == ["a.pdf"] and all(d.exact for d in r["exact"])
    assert [d.filename for d in r["approximate"]] == ["b.pdf"] and not r["approximate"][0].exact
    assert not search(lib, "AB-12345")["approximate"]


def test_tokenizer_unit():
    assert tokenize("No.A-3837, (B_12/7)") == ["no", "a-3837", "b_12/7"]
    assert tokenize("A\u20133837") == ["a-3837"]
    assert tokenize("v 3.5 end.") == ["v", "3.5", "end"]
