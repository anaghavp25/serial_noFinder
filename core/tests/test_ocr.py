from core.importer import import_documents
from core.serial_search import SerialSearcher
from core.library_sync import sync_library
from database import schema as S
from tests.conftest import make_pdf


def hits(lib, q):
    c = lib.connect()
    try:
        return SerialSearcher(c, lib.root).search_exact(q)
    finally:
        c.close()


def doc(lib, name):
    c = lib.connect()
    try:
        return dict(c.execute("SELECT * FROM documents WHERE filename=?", (name,)).fetchone())
    finally:
        c.close()


def test_scanned_pdf_is_ocrd_and_searchable(lib, src, real_ocr):
    p = make_pdf(src / "scan.pdf", ["Certificate No: AB-48213", "Second page Ref CD-99120"], scanned=True)
    rep = import_documents(lib, [p], ocr=real_ocr)
    assert rep.count("imported") == 1
    d = doc(lib, "scan.pdf")
    assert d["processing_status"] == S.ST_INDEXED and d["ocr_pages"] == 2
    assert [m.page_number for m in hits(lib, "AB-48213")[0].matches] == [1]
    assert [m.page_number for m in hits(lib, "CD-99120")[0].matches] == [2]


def test_mixed_text_and_scanned_pages(lib, src, real_ocr):
    import fitz
    from utils.fitz_compat import fitz as f
    a = make_pdf(src / "t.pdf", ["Text page TX-1111"])
    b = make_pdf(src / "s.pdf", ["Scanned SC-2222"], scanned=True)
    doc_ = f.open(); doc_.insert_pdf(f.open(str(a))); doc_.insert_pdf(f.open(str(b)))
    mixed = src / "mixed.pdf"; doc_.save(str(mixed))
    import_documents(lib, [mixed], ocr=real_ocr)
    d = doc(lib, "mixed.pdf")
    assert d["ocr_pages"] == 1 and d["processing_status"] == S.ST_INDEXED
    assert hits(lib, "TX-1111") and hits(lib, "SC-2222")


def test_missing_tesseract_gives_waiting_status_then_retry_works(lib, src, no_ocr, real_ocr):
    p = make_pdf(src / "scan.pdf", ["Certificate No: ZZ-70707"], scanned=True)
    import_documents(lib, [p], ocr=no_ocr)
    assert doc(lib, "scan.pdf")["processing_status"] == S.ST_WAITING
    assert "disabled for test" in doc(lib, "scan.pdf")["last_error"]
    assert sync_library(lib, no_ocr).processed == 0               # not retried while OCR is unavailable
    sync_library(lib, real_ocr)                                   # OCR now available -> retried
    assert doc(lib, "scan.pdf")["processing_status"] == S.ST_INDEXED
    assert hits(lib, "ZZ-70707")


def test_rotated_scan(lib, src, real_ocr):
    from utils.fitz_compat import fitz
    p = make_pdf(src / "rot.pdf", ["Certificate No: RT-31415"], scanned=True)
    d = fitz.open(str(p)); d[0].set_rotation(90)
    # rotate the image content, not just the display flag: re-render and embed rotated
    pix = d[0].get_pixmap(dpi=200)
    out = fitz.open(); pg = out.new_page(width=pix.width, height=pix.height)
    pg.insert_image(pg.rect, stream=pix.tobytes("png")); out.save(str(src / "rot2.pdf"))
    import_documents(lib, [src / "rot2.pdf"], ocr=real_ocr)
    assert hits(lib, "RT-31415")
