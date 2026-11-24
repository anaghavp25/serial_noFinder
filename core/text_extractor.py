def extract_page_text(page) -> str:
    """Selectable text of one PDF page (PyMuPDF)."""
    return page.get_text("text") or ""


def alnum_count(text: str) -> int:
    return sum(1 for c in text if c.isalnum())
