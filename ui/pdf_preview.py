from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from core.external_open import open_pdf, open_pdf_at_page, reveal_in_file_manager
from core.preview_renderer import get_thumbnail, render_page_png

POOL = QThreadPool.globalInstance()
POOL.setMaxThreadCount(4)


class _Signals(QObject):
    done = Signal(int, bytes, float, int)
    failed = Signal(int, str)


class RenderTask(QRunnable):
    """Render one page off the GUI thread."""

    def __init__(self, signals, token, path, page, zoom, fit_px, highlight):
        super().__init__()
        self.token, self.path, self.page, self.zoom, self.fit_px, self.highlight = token, path, page, zoom, fit_px, highlight
        self.signals = signals          # owned by a long-lived widget, never by the runnable

    def run(self):
        try:
            png, z, n = render_page_png(self.path, self.page, self.zoom, self.fit_px, self.highlight)
            self.signals.done.emit(self.token, png, z, n)
        except FileNotFoundError:
            self.signals.failed.emit(self.token, "This PDF is no longer available (moved or deleted). Run Sync Library.")
        except Exception as e:
            self.signals.failed.emit(self.token, f"Cannot render page: {e}")


class ThumbTask(QRunnable):
    def __init__(self, signals, token, library, doc_id, path, page, width, size, mtime_ns):
        super().__init__()
        self.args = (library, doc_id, path, page, width, size, mtime_ns)
        self.token = token
        self.signals = signals

    def run(self):
        lib, doc_id, path, page, width, size, mtime = self.args
        try:
            data = get_thumbnail(lib, doc_id, path, page, width, size, mtime)
            self.signals.done.emit(self.token, data, 0.0, 0)
        except FileNotFoundError:
            self.signals.failed.emit(self.token, "File unavailable")
        except Exception as e:
            self.signals.failed.emit(self.token, "Preview unavailable")


class PdfPreview(QWidget):
    """Single-document viewer: page navigation, zoom, jump between first/matching pages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.res = None            # DocumentResult
        self.page = 1
        self.page_count = 1
        self.zoom: Optional[float] = None     # None = fit width
        self.last_zoom = 1.0
        self.highlight = ""
        self.token = 0
        self.match_pages: List[int] = []
        self.sig = _Signals(self)
        self.sig.done.connect(self._done)
        self.sig.failed.connect(self._failed)

        self.title = QLabel("Select a result to preview the PDF")
        self.title.setStyleSheet("font-weight:600;")
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#666;")

        def btn(text, tip="", slot=None):
            b = QPushButton(text)
            b.setToolTip(tip)
            if slot:
                b.clicked.connect(slot)
            return b

        self.b_first = btn("First page", "Go to the first page of this PDF", lambda: self.goto(1))
        self.b_match = btn("Matching page", "Go to the first matching page", self.goto_match)
        self.b_pm = btn("\u25C0 Match", "Previous matching page", lambda: self.step_match(-1))
        self.b_nm = btn("Match \u25B6", "Next matching page", lambda: self.step_match(1))
        self.b_prev = btn("\u25C0", "Previous page", lambda: self.goto(self.page - 1))
        self.b_next = btn("\u25B6", "Next page", lambda: self.goto(self.page + 1))
        self.spin = QSpinBox(); self.spin.setMinimum(1)
        self.spin.editingFinished.connect(lambda: self.goto(self.spin.value()))
        self.of = QLabel("/ 1")
        self.b_zo = btn("\u2212", "Zoom out", lambda: self.zoom_by(1 / 1.25))
        self.b_zi = btn("+", "Zoom in", lambda: self.zoom_by(1.25))
        self.b_fit = btn("Fit width", "", self.fit)
        self.zlabel = QLabel("")
        self.b_open = btn("Open PDF", "Open in your PDF viewer", self.open_pdf)
        self.b_openat = btn("Open at page", "Open in your PDF viewer at the current page", self.open_at)
        self.b_reveal = btn("Show in folder", "Reveal the file in the file manager", self.reveal)

        row1 = QHBoxLayout()
        for w in (self.b_first, self.b_match, self.b_pm, self.b_nm):
            row1.addWidget(w)
        row1.addStretch()
        row2 = QHBoxLayout()
        for w in (self.b_prev, self.spin, self.of, self.b_next, self.b_zo, self.zlabel, self.b_zi, self.b_fit):
            row2.addWidget(w)
        row2.addStretch()
        row3 = QHBoxLayout()
        for w in (self.b_open, self.b_openat, self.b_reveal):
            row3.addWidget(w)
        row3.addStretch()

        self.canvas = QLabel("")
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setStyleSheet("background:#e9e9ec;")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.canvas)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        for w in (self.title, self.sub):
            lay.addWidget(w)
        lay.addLayout(row1); lay.addLayout(row2); lay.addLayout(row3)
        lay.addWidget(self.scroll, 1)
        self._enable(False)

    # ---- public API -----------------------------------------------------------
    def show_document(self, res, page: int, highlight: str = ""):
        self.res = res
        self.highlight = highlight
        self.page_count = max(1, res.page_count)
        self.match_pages = sorted({m.page_number for m in res.matches})
        self.title.setText(res.filename)
        self.spin.setMaximum(self.page_count)
        self.of.setText(f"/ {self.page_count}")
        self._enable(True)
        self.b_pm.setEnabled(len(self.match_pages) > 1)
        self.b_nm.setEnabled(len(self.match_pages) > 1)
        self.goto(page)

    def clear(self):
        self.res = None
        self.canvas.clear()
        self.title.setText("Select a result to preview the PDF")
        self.sub.setText("")
        self._enable(False)

    # ---- navigation ---------------------------------------------------------------
    def goto(self, page: int):
        if not self.res:
            return
        self.page = max(1, min(int(page), self.page_count))
        self.spin.setValue(self.page)
        self.b_prev.setEnabled(self.page > 1)
        self.b_next.setEnabled(self.page < self.page_count)
        self._render()

    def goto_match(self):
        if self.match_pages:
            self.goto(self.match_pages[0])

    def step_match(self, d: int):
        if not self.match_pages:
            return
        if d > 0:
            nxt = [p for p in self.match_pages if p > self.page]
            self.goto(nxt[0] if nxt else self.match_pages[0])
        else:
            prv = [p for p in self.match_pages if p < self.page]
            self.goto(prv[-1] if prv else self.match_pages[-1])

    def zoom_by(self, f: float):
        self.zoom = max(0.2, min(6.0, (self.zoom or self.last_zoom) * f))
        self._render()

    def fit(self):
        self.zoom = None
        self._render()

    def resizeEvent(self, e):
        super().resizeEvent(e)

    # ---- rendering -------------------------------------------------------------------
    def _render(self):
        if not self.res:
            return
        self.token += 1
        is_match = self.page in self.match_pages
        tag = "  \u2022  MATCHING PAGE" if is_match else ("  \u2022  first page" if self.page == 1 else "")
        self.sub.setText(f"Page {self.page} of {self.page_count}{tag}")
        self.canvas.setText("Rendering\u2026")
        fit_px = None if self.zoom else max(200, self.scroll.viewport().width() - 24)
        t = RenderTask(self.sig, self.token, self.res.abs_path, self.page, self.zoom, fit_px,
                       self.highlight if is_match else "")
        POOL.start(t)

    def _done(self, token, png, zoom, n):
        if token != self.token:
            return
        pm = QPixmap()
        pm.loadFromData(png, "PNG")
        self.canvas.setPixmap(pm)
        self.last_zoom = zoom
        self.zlabel.setText(f"{int(zoom * 100)}%")

    def _failed(self, token, msg):
        if token == self.token:
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText(msg)

    # ---- external actions -------------------------------------------------------------
    def _notify(self, ok, msg):
        if msg:
            self.sub.setText(msg)

    def open_pdf(self):
        if self.res:
            self._notify(*open_pdf(self.res.abs_path))

    def open_at(self):
        if self.res:
            self._notify(*open_pdf_at_page(self.res.abs_path, self.page))

    def reveal(self):
        if self.res:
            self._notify(*reveal_in_file_manager(self.res.abs_path))

    def _enable(self, on: bool):
        for w in (self.b_first, self.b_match, self.b_pm, self.b_nm, self.b_prev, self.b_next, self.spin,
                  self.b_zo, self.b_zi, self.b_fit, self.b_open, self.b_openat, self.b_reveal):
            w.setEnabled(on)
