"""Open a PDF (optionally at a page) or reveal it in the file manager - locally, no network."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def _win_candidates(path: Path, page: int) -> List[Tuple[str, List[str]]]:
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
          os.environ.get("LOCALAPPDATA", "")]
    out: List[Tuple[str, List[str]]] = []
    for base in pf:
        if not base:
            continue
        for exe in (r"SumatraPDF\SumatraPDF.exe",):
            p = Path(base) / exe
            if p.is_file():
                out.append(("SumatraPDF", [str(p), "-page", str(page), str(path)]))
        for exe in (r"Adobe\Acrobat Reader DC\Reader\AcroRd32.exe", r"Adobe\Acrobat DC\Acrobat\Acrobat.exe",
                    r"Adobe\Acrobat Reader\Reader\AcroRd32.exe"):
            p = Path(base) / exe
            if p.is_file():
                out.append(("Adobe Acrobat", [str(p), "/A", f"page={page}", str(path)]))
    url = f"file:///{path.as_posix()}#page={page}"
    for base in pf:
        if not base:
            continue
        for name, exe in (("Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe"),
                          ("Google Chrome", r"Google\Chrome\Application\chrome.exe")):
            p = Path(base) / exe
            if p.is_file():
                out.append((name, [str(p), url]))
    return out


def open_pdf(path) -> Tuple[bool, str]:
    path = Path(path)
    if not path.is_file():
        return False, "The file no longer exists."
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True, "Opened with the default PDF viewer."
    except Exception as e:
        return False, f"Could not open the PDF: {e}"


def open_pdf_at_page(path, page: int) -> Tuple[bool, str]:
    """Jump to a page using a viewer with a page-argument (Sumatra, Adobe, Edge, Chrome).
    Falls back to the default viewer (opening on page 1) and says so."""
    path = Path(path)
    if not path.is_file():
        return False, "The file no longer exists."
    if sys.platform.startswith("win"):
        for name, cmd in _win_candidates(path, page):
            try:
                subprocess.Popen(cmd)
                return True, f"Opened page {page} in {name}."
            except OSError:
                continue
    elif sys.platform == "darwin":
        pass
    else:
        for viewer, cmd in (("Evince", ["evince", f"--page-index={page}", str(path)]),
                            ("Okular", ["okular", "-p", str(page), str(path)])):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd)
                return True, f"Opened page {page} in {viewer}."
    ok, msg = open_pdf(path)
    if ok:
        msg = f"Opened with the default viewer; go to page {page} manually (no page-aware viewer found)."
    return ok, msg


def reveal_in_file_manager(path) -> Tuple[bool, str]:
    path = Path(path)
    if not path.exists():
        return False, "The file no longer exists."
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
        return True, ""
    except Exception as e:
        return False, str(e)
