"""Local OCR through Tesseract. Nothing here ever touches the network."""
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from utils.fitz_compat import fitz
from utils.logging_utils import get_logger

log = get_logger("ocr")

_WIN_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


class OcrUnavailable(Exception):
    pass


def locate_tesseract(configured: str = "") -> Optional[str]:
    candidates = []
    if configured:
        candidates.append(configured)
    meipass = getattr(sys, "_MEIPASS", None)  # bundled copy inside a PyInstaller build
    if meipass:
        candidates.append(str(Path(meipass) / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")))
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)
    if sys.platform.startswith("win"):
        candidates.extend(_WIN_CANDIDATES)
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(str(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe"))
    for c in candidates:
        if c and Path(c).is_file():
            td = Path(c).parent / "tessdata"      # a bundled copy ships its own language data
            if meipass and str(c).startswith(str(meipass)) and td.is_dir():
                os.environ["TESSDATA_PREFIX"] = str(td)
            return c
    return None


class OcrEngine:
    def __init__(self, lang: str = "eng", tesseract_cmd: str = "", dpi: int = 300, min_chars: int = 25):
        self.lang = lang or "eng"
        self.dpi = int(dpi)
        self.min_chars = min_chars
        self.cmd = locate_tesseract(tesseract_cmd)
        self._checked: Optional[bool] = None
        self.error = ""

    def available(self) -> bool:
        if self._checked is None:
            self._checked = False
            if not self.cmd:
                self.error = "Tesseract executable not found. Install Tesseract OCR or set its path in Settings."
            else:
                try:
                    import pytesseract
                    pytesseract.pytesseract.tesseract_cmd = self.cmd
                    pytesseract.get_tesseract_version()
                    langs = set(pytesseract.get_languages(config=""))
                    wanted = set(self.lang.split("+"))
                    if not wanted <= langs:
                        self.error = f"OCR language data missing: {', '.join(sorted(wanted - langs))}"
                    else:
                        self._checked = True
                except Exception as e:
                    self.error = f"Tesseract is not usable: {e}"
            if not self._checked:
                log.warning("OCR unavailable: %s", self.error)
        return bool(self._checked)

    def version(self) -> str:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = self.cmd or "tesseract"
            return str(pytesseract.get_tesseract_version())
        except Exception:
            return "not available"

    # -- image preparation -----------------------------------------------------
    def _render_gray(self, page):
        import numpy as np
        rect = page.rect
        dpi = self.dpi
        px = (rect.width / 72 * dpi) * (rect.height / 72 * dpi)
        if px > 5e7:  # very large page: keep memory in check
            dpi = max(100, int(dpi * (5e7 / px) ** 0.5))
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
        return np.ascontiguousarray(arr)

    @staticmethod
    def _deskew(gray):
        import cv2
        import numpy as np
        inv = cv2.bitwise_not(gray)
        ys, xs = np.where(inv > 128)
        if len(xs) < 500:
            return gray
        pts = np.column_stack((xs, ys)).astype(np.float32)
        angle = cv2.minAreaRect(pts)[-1]
        if angle > 45:
            angle -= 90
        elif angle < -45:
            angle += 90
        if abs(angle) < 0.4 or abs(angle) > 15:
            return gray
        h, w = gray.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    def _prepare(self, gray, attempt: int):
        import cv2
        if attempt == 1:       # denoise + Otsu threshold
            img = cv2.medianBlur(gray, 3)
            _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return self._deskew(img)
        if attempt == 2:       # adaptive threshold on an enlarged image (uneven lighting, stamps)
            img = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            img = cv2.GaussianBlur(img, (3, 3), 0)
            return cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
        return gray            # attempt 3: untouched grayscale

    def _auto_rotate(self, img):
        import cv2
        import pytesseract
        try:
            osd = pytesseract.image_to_osd(img, config="--psm 0", output_type=pytesseract.Output.DICT, timeout=30)
            rot = int(osd.get("rotate", 0))
            conf = float(osd.get("orientation_conf", 0))
            if rot and conf >= 2.0:
                code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(rot)
                if code is not None:
                    return cv2.rotate(img, code)
        except Exception:
            pass  # OSD data may be missing or the page may have too little text
        return img

    def ocr_page(self, page, attempts: int = 3) -> str:
        """OCR one fitz page, retrying with different preprocessing until text is found."""
        if not self.available():
            raise OcrUnavailable(self.error)
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = self.cmd
        gray = self._render_gray(page)
        best, last_err = "", None
        psms = {1: 3, 2: 11, 3: 6}
        for attempt in range(1, attempts + 1):
            try:
                img = self._prepare(gray, attempt)
                if attempt == 1:
                    img = self._auto_rotate(img)
                text = pytesseract.image_to_string(
                    img, lang=self.lang, config=f"--oem 3 --psm {psms[attempt]}", timeout=180)
                if sum(c.isalnum() for c in text) > sum(c.isalnum() for c in best):
                    best = text
                if sum(c.isalnum() for c in best) >= self.min_chars:
                    break
            except Exception as e:
                last_err = e
                log.warning("OCR attempt %d failed: %s", attempt, e)
        if not best.strip() and last_err is not None:
            raise RuntimeError(f"OCR failed: {last_err}")
        return best
