"""On-demand page rendering with a disk thumbnail cache."""
import os
import time
from pathlib import Path
from typing import Optional, Tuple

from utils.fitz_compat import fitz
from utils.logging_utils import get_logger

log = get_logger("preview")


def render_page_png(pdf_path, page_number: int, zoom: Optional[float] = None, fit_width_px: Optional[int] = None,
                    highlight: Optional[str] = None) -> Tuple[bytes, float, int]:
    """Render 1-based page_number. Returns (png_bytes, zoom_used, page_count).
    Raises FileNotFoundError when the PDF is gone."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(str(pdf_path))
    doc = fitz.open(str(pdf_path))
    try:
        if doc.needs_pass:
            raise ValueError("Password-protected PDF")
        n = doc.page_count
        page = doc.load_page(max(0, min(page_number, n) - 1))
        if fit_width_px:
            zoom = max(0.1, fit_width_px / max(page.rect.width, 1))
        zoom = zoom or 1.0
        zoom = max(0.1, min(zoom, 8.0))
        if highlight:
            try:
                for text in {highlight, highlight.upper(), highlight.lower()}:
                    quads = page.search_for(text, quads=True)
                    if quads:
                        page.add_highlight_annot(quads)
                        break
            except Exception:
                pass  # highlighting is best-effort (only works on pages with selectable text)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=True)
        return pix.tobytes("png"), zoom, n
    finally:
        doc.close()


def thumbnail_path(library, document_id: str, page_number: int, width: int, size: int, mtime_ns: int) -> Path:
    return library.thumbs_dir / f"{document_id}_{page_number}_{width}_{size}_{mtime_ns}.png"


def get_thumbnail(library, document_id: str, pdf_path, page_number: int, width: int = 220,
                  size: int = 0, mtime_ns: int = 0) -> bytes:
    """PNG bytes of a page thumbnail; cached on disk, regenerated if the cache entry is missing/unreadable."""
    cp = thumbnail_path(library, document_id, page_number, width, size, mtime_ns)
    try:
        if cp.is_file() and cp.stat().st_size > 0:
            return cp.read_bytes()
    except OSError:
        pass
    data, _, _ = render_page_png(pdf_path, page_number, fit_width_px=width)
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cp)
    except OSError:
        pass  # cache is optional
    return data


def enforce_cache_limit(library, max_mb: int) -> int:
    """Delete least-recently-written thumbnails until the cache is under the limit."""
    limit = max_mb * 1024 * 1024
    files = []
    if library.thumbs_dir.exists():
        for p in library.thumbs_dir.iterdir():
            try:
                s = p.stat()
                files.append((s.st_mtime, s.st_size, p))
            except OSError:
                pass
    total = sum(f[1] for f in files)
    removed = 0
    for _, size, p in sorted(files):
        if total <= limit:
            break
        try:
            p.unlink()
            total -= size
            removed += 1
        except OSError:
            pass
    return removed
