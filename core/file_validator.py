import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from utils.fitz_compat import fitz


@dataclass
class ValidationResult:
    ok: bool
    code: str = "ok"          # ok | missing | empty | permission | not_pdf | corrupt | encrypted | zero_pages
    reason: str = ""
    page_count: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)


def validate_pdf(path) -> ValidationResult:
    path = Path(path)
    try:
        if not path.is_file():
            return ValidationResult(False, "missing", "File does not exist")
        size = path.stat().st_size
        if size == 0:
            return ValidationResult(False, "empty", "File is empty (0 bytes)")
        with open(path, "rb") as f:
            head = f.read(1024)
    except PermissionError:
        return ValidationResult(False, "permission", "Permission denied")
    except OSError as e:
        return ValidationResult(False, "permission", f"Cannot read file: {e}")
    if b"%PDF" not in head:
        return ValidationResult(False, "not_pdf", "Not a PDF file (missing %PDF header)")
    doc = None
    try:
        doc = fitz.open(str(path))
        if doc.needs_pass:
            return ValidationResult(False, "encrypted", "Password-protected PDF")
        n = doc.page_count
        if n == 0:
            return ValidationResult(False, "zero_pages", "PDF has no pages")
        doc.load_page(0)
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        return ValidationResult(True, "ok", "", n, meta)
    except Exception as e:  # MuPDF raises several exception types for damaged files
        return ValidationResult(False, "corrupt", f"Unreadable or corrupted PDF: {e}")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
