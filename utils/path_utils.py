import os
import re
import shutil
from pathlib import Path
from typing import Iterator

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
TMP_SUFFIX = ".pdfsf-tmp"


def sanitize_filename(name: str) -> str:
    name = _INVALID.sub("_", name).strip().rstrip(". ")
    if not name:
        name = "document.pdf"
    stem, ext = os.path.splitext(name)
    if stem.lower() in _RESERVED:
        stem = f"_{stem}"
    if ext.lower() != ".pdf":
        ext = ".pdf"
    return (stem[:150] or "document") + ext


def unique_destination(directory: Path, filename: str) -> Path:
    """Collision-safe destination (case-insensitive so it is also safe on Windows)."""
    filename = sanitize_filename(filename)
    existing = {n.lower() for n in os.listdir(directory)} if directory.exists() else set()
    stem, ext = os.path.splitext(filename)
    candidate, n = filename, 1
    while candidate.lower() in existing:
        n += 1
        candidate = f"{stem} ({n}){ext}"
    return directory / candidate


def is_within(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def to_rel(root: Path, absolute: Path) -> str:
    return Path(absolute).resolve().relative_to(Path(root).resolve()).as_posix()


def resolve_rel(root: Path, rel: str) -> Path:
    """Resolve a stored relative path, refusing anything that escapes the library root."""
    root = Path(root).resolve()
    p = (root / rel).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise ValueError(f"Path escapes library root: {rel!r}")
    return p


def iter_pdfs(root: Path, recursive: bool = True) -> Iterator[Path]:
    """Yield PDF files under root; hidden directories (e.g. .pdfsf) are skipped."""
    root = Path(root)
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if fn.lower().endswith(".pdf") and not fn.startswith("."):
                    yield Path(dirpath) / fn
    else:
        for fn in sorted(os.listdir(root)):
            p = root / fn
            if fn.lower().endswith(".pdf") and p.is_file():
                yield p


def format_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def free_space(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def dir_size(path: Path) -> int:
    total = 0
    for dp, _, fns in os.walk(path):
        for f in fns:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total
