"""Single import point for PyMuPDF (new name `pymupdf`, legacy name `fitz`)."""
try:
    import pymupdf as fitz  # PyMuPDF >= 1.24.3
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

try:
    fitz.TOOLS.mupdf_display_errors(False)  # keep MuPDF warnings off stderr; we log ourselves
except Exception:  # pragma: no cover
    pass

__all__ = ["fitz"]
