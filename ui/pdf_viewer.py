"""Small PDF viewer: page navigation, zoom, back, print.  Also home of the background render tasks."""
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from core.preview_renderer import get_thumbnail, render_page_png
from ui.printing import print_document

POOL = QThreadPool.globalInstance()
POOL.setMaxThreadCount(4)


class RenderSignals(QObject):
    done = Signal(int, bytes, float)
    failed = Signal(int, str)


class RenderTask(QRunnable):
    def __init__(self, signals, token, path, page, zoom, fit_px, highlight):
        super().__init__()
        self.signals, self.token = signals, token        # signals live in a long-lived widget
        self.a = (path, page, zoom, fit_px, highlight)

    def run(self):
        try:
            png, z, _ = render_page_png(*self.a)
            self.signals.done.emit(self.token, png, z)
        except FileNotFoundError:
            self.signals.failed.emit(self.token, "This PDF is no longer in the folder.")
        except Exception as e:
            self.signals.failed.emit(self.token, f"This page cannot be shown ({e}).")


class ThumbTask(QRunnable):
    def __init__(self, signals, token, library, doc_id, path, page, width, size, mtime_ns):
        super().__init__()
        self.signals, self.token = signals, token
        self.a = (library, doc_id, path, page, width, size, mtime_ns)

    def run(self):
        try:
            self.signals.done.emit(self.token, get_thumbnail(*self.a), 0.0)
        except Exception:
            self.signals.failed.emit(self.token, "Preview not available")


class PdfViewer(QWidget):
    back = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.path: Optional[Path] = None
        self.name = ""
        self.page, self.page_count = 1, 1
        self.zoom: Optional[float] = None       # None = fit width
        self.last_zoom = 1.0
        self.highlight = ""
        self.token = 0
        self.sig = RenderSignals(self)
        self.sig.done.connect(self._done)
        self.sig.failed.connect(self._failed)

        self.b_back = QPushButton("\u2190  Back"); self.b_back.setProperty("kind", "secondary")
        self.b_back.clicked.connect(self.back.emit)
        self.title = QLabel(""); self.title.setObjectName("h2")
        self.b_print = QPushButton("Print"); self.b_print.clicked.connect(self.print)
        self.b_prev = QPushButton("\u25C0"); self.b_prev.setProperty("kind", "secondary")
        self.b_next = QPushButton("\u25B6"); self.b_next.setProperty("kind", "secondary")
        self.b_prev.setToolTip("Previous page"); self.b_next.setToolTip("Next page")
        self.b_prev.clicked.connect(lambda: self.goto(self.page - 1))
        self.b_next.clicked.connect(lambda: self.goto(self.page + 1))
        self.spin = QSpinBox(); self.spin.setMinimum(1); self.spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.spin.setFixedWidth(80); self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.editingFinished.connect(lambda: self.goto(self.spin.value()))
        self.of = QLabel("of 1")
        self.b_zo = QPushButton("\u2212"); self.b_zi = QPushButton("+"); self.b_fit = QPushButton("Fit")
        for b in (self.b_zo, self.b_zi, self.b_fit):
            b.setProperty("kind", "secondary")
        self.b_zo.setToolTip("Zoom out"); self.b_zi.setToolTip("Zoom in"); self.b_fit.setToolTip("Fit page width")
        self.b_zo.clicked.connect(lambda: self.zoom_by(1 / 1.25))
        self.b_zi.clicked.connect(lambda: self.zoom_by(1.25))
        self.b_fit.clicked.connect(self.fit)
        self.msg = QLabel(""); self.msg.setObjectName("muted")

        top = QHBoxLayout(); top.addWidget(self.b_back); top.addWidget(self.title, 1); top.addWidget(self.b_print)
        nav = QHBoxLayout()
        for w in (self.b_prev, self.spin, self.of, self.b_next):
            nav.addWidget(w)
        nav.addSpacing(24)
        for w in (self.b_zo, self.b_zi, self.b_fit):
            nav.addWidget(w)
        nav.addSpacing(12); nav.addWidget(self.msg, 1)

        self.canvas = QLabel("")
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setStyleSheet("background:#dfe4ea;")
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(self.canvas)
        lay = QVBoxLayout(self); lay.setContentsMargins(24, 20, 24, 20); lay.setSpacing(12)
        lay.addLayout(top); lay.addLayout(nav); lay.addWidget(self.scroll, 1)
        QShortcut(QKeySequence("Left"), self, activated=lambda: self.goto(self.page - 1))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self.goto(self.page + 1))
        QShortcut(QKeySequence("Escape"), self, activated=self.back.emit)

    def open(self, path, name: str, page_count: int, page: int = 1, highlight: str = ""):
        self.path, self.name, self.highlight = Path(path), name, highlight
        self.page_count = max(1, page_count)
        self.zoom = None
        self.title.setText(name)
        self.spin.setMaximum(self.page_count)
        self.of.setText(f"of {self.page_count}")
        self.goto(page)

    def goto(self, page: int):
        if not self.path:
            return
        self.page = max(1, min(int(page), self.page_count))
        self.spin.setValue(self.page)
        self.b_prev.setEnabled(self.page > 1)
        self.b_next.setEnabled(self.page < self.page_count)
        self._render()

    def zoom_by(self, f: float):
        self.zoom = max(0.2, min(6.0, (self.zoom or self.last_zoom) * f))
        self._render()

    def fit(self):
        self.zoom = None
        self._render()

    def _render(self):
        self.token += 1
        self.msg.setText("")
        fit_px = None if self.zoom else max(200, self.scroll.viewport().width() - 24)
        POOL.start(RenderTask(self.sig, self.token, self.path, self.page, self.zoom, fit_px, self.highlight))

    def _done(self, token, png, zoom):
        if token != self.token:
            return
        pm = QPixmap(); pm.loadFromData(png, "PNG")
        self.canvas.setPixmap(pm)
        self.last_zoom = zoom

    def _failed(self, token, msg):
        if token == self.token:
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText(msg)

    def print(self):
        if not self.path or not self.path.is_file():
            QMessageBox.warning(self, "Print", "This PDF is no longer in the folder.")
            return
        print_document(self, self.path, self.page_count, self.page, self.name)
