def extract_page_text(page) -> str:
    """Embedded text of one PDF page (PyMuPDF)."""
    return page.get_text("text") or ""


def alnum_count(text: str) -> int:
    return sum(1 for c in text if c.isalnum())


def page_needs_ocr(text: str, min_chars: int, has_images: bool = True) -> bool:
    """OCR is needed when the embedded text layer is missing or insufficient.

    A short page that has a few words of real text and no images at all (e.g. a one-line
    certificate stub) is accepted as-is; any page that carries images but little text is
    treated as a scan."""
    n = alnum_count(text)
    if n >= max(1, int(min_chars)):
        return False
    if not has_images and n >= 5:
        return False
    return True
