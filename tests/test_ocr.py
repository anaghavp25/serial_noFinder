from core.library_sync import sync_library
from core.serial_search import SerialSearcher
from database import schema as S
from tests.conftest import make_pdf


def hits(lib, q):
    c = lib.connect()
    try:
        return SerialSearcher(c, lib.root).search_exact(q)
    finally:
        c.close()


def status(lib, name):
    c = lib.connect()
    try:
        return dict(c.execute("SELECT * FROM documents WHERE filename=?", (name,)).fetchone())
    finally:
        c.close()


def test_scanned_pdf_is_ocrd_and_searchable(lib, real_ocr):
    make_pdf(lib.root / "scan.pdf", ["Certificate No: AB-48213", "Second page Ref CD-99120"], scanned=True)
    sync_library(lib, real_ocr)
    d = status(lib, "scan.pdf")
    assert d["processing_status"] == S.ST_INDEXED and d["ocr_pages"] == 2
    assert [m.page_number for m in hits(lib, "AB-48213")[0].matches] == [1]
    assert [m.page_number for m in hits(lib, "CD-99120")[0].matches] == [2]


def test_mixed_text_and_scanned_pages(lib, real_ocr, tmp_path):
    from utils.fitz_compat import fitz
    a = make_pdf(tmp_path / "t.pdf", ["Text page TX-1111"])
    b = make_pdf(tmp_path / "s.pdf", ["Scanned SC-2222"], scanned=True)
    d = fitz.open(); d.insert_pdf(fitz.open(str(a))); d.insert_pdf(fitz.open(str(b)))
    d.save(str(lib.root / "mixed.pdf"))
    sync_library(lib, real_ocr)
    assert status(lib, "mixed.pdf")["ocr_pages"] == 1 and hits(lib, "TX-1111") and hits(lib, "SC-2222")


def test_without_tesseract_scans_wait_and_are_picked_up_later(lib, no_ocr, real_ocr):
    make_pdf(lib.root / "scan.pdf", ["Certificate No: ZZ-70707"], scanned=True)
    sync_library(lib, no_ocr)
    assert status(lib, "scan.pdf")["processing_status"] == S.ST_WAITING
    assert sync_library(lib, no_ocr).processed == 0
    sync_library(lib, real_ocr)
    assert status(lib, "scan.pdf")["processing_status"] == S.ST_INDEXED and hits(lib, "ZZ-70707")
