import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.library_manager import Library          # noqa: E402
from core.ocr_engine import OcrEngine             # noqa: E402
from utils.fitz_compat import fitz                # noqa: E402


def make_pdf(path: Path, pages, scanned=False, extra_header=None):
    """pages: list of strings (one per page). scanned=True renders text into an image-only PDF."""
    doc = fitz.open()
    for i, text in enumerate(pages):
        page = doc.new_page()
        if extra_header:
            page.insert_text((50, 30), extra_header, fontsize=9)
        page.insert_text((72, 120), text, fontsize=26 if scanned else 14)
    if scanned:
        img_doc = fitz.open()
        for page in doc:
            pix = page.get_pixmap(dpi=200, alpha=False)
            p = img_doc.new_page(width=page.rect.width, height=page.rect.height)
            p.insert_image(p.rect, stream=pix.tobytes("png"))
        doc.close()
        doc = img_doc
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def lib(tmp_path):
    d = tmp_path / "certs"
    d.mkdir()
    return Library.open(d)


@pytest.fixture
def src(lib):
    return lib.root          # PDFs live directly in the selected folder


@pytest.fixture
def no_ocr():
    class _Off(OcrEngine):
        def available(self):
            self.error = "disabled for test"
            return False
    return _Off()


@pytest.fixture
def real_ocr():
    e = OcrEngine("eng")
    if not e.available():
        pytest.skip("Tesseract not installed")
    return e


@pytest.fixture
def make(src):
    def _make(name, pages, **kw):
        return make_pdf(src / name, pages, **kw)
    return _make
