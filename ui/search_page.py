import time

from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from core.serial_search import SerialSearcher
from ui.pdf_viewer import POOL, RenderSignals, ThumbTask
from ui.printing import print_document

THUMB_W = 150


class SearchSignals(QObject):
    done = Signal(int, str, object, float)
    failed = Signal(int, str)


class SearchTask(QRunnable):
    def __init__(self, signals, token, library, query):
        super().__init__()
        self.signals, self.token, self.library, self.query = signals, token, library, query

    def run(self):
        t0 = time.perf_counter()
        try:
            conn = self.library.connect()
            try:
                res = SerialSearcher(conn, self.library.root, self.library.settings(conn)).search_exact(self.query)
            finally:
                conn.close()
            self.signals.done.emit(self.token, self.query, res, time.perf_counter() - t0)
        except Exception as e:
            self.signals.failed.emit(self.token, f"The search failed: {e}")


class ResultCard(QFrame):
    """One matching page of one document."""

    def __init__(self, library, res, match, win):
        super().__init__()
        self.setObjectName("card")
        self.library, self.res, self.match, self.win = library, res, match, win
        self.token = 0
        self.sig = RenderSignals(self)
        self.sig.done.connect(self._thumb)
        self.sig.failed.connect(lambda t, m: self.thumb.setText("Preview not available"))
        self.thumb = QLabel("Loading\u2026")
        self.thumb.setFixedSize(THUMB_W + 10, int(THUMB_W * 1.42) + 10)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet("background:#eef1f4;border:1px solid #d9d2c2;")
        self.thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumb.mousePressEvent = lambda e: self.view_page()
        self.front_sig = RenderSignals(self)
        self.front_sig.done.connect(self._front_thumb)
        self.front_sig.failed.connect(lambda t, m: self.front_thumb.setText("Preview not available"))
        self.front_thumb = QLabel("Loading\u2026")
        self.front_thumb.setFixedSize(THUMB_W + 10, int(THUMB_W * 1.42) + 10)
        self.front_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.front_thumb.setStyleSheet("background:#eef1f4;border:1px solid #d9d2c2;")
        self.front_thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.front_thumb.mousePressEvent = lambda e: self.view_front_page()

        name = QLabel(res.filename); name.setObjectName("name"); name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        where = QLabel(f"Page {match.page_number} of {res.page_count}")
        front = QLabel("First page")
        match_label = QLabel(f"Matching page {match.page_number}")
        previews = QHBoxLayout()
        for label, thumb in ((front, self.front_thumb), (match_label, self.thumb)):
            column = QVBoxLayout()
            column.addWidget(label)
            column.addWidget(thumb)
            previews.addLayout(column)
        b1 = QPushButton("View First Page"); b1.clicked.connect(self.view_front_page)
        b2 = QPushButton("View Matching Page"); b2.clicked.connect(self.view_page)
        b3 = QPushButton("Full PDF"); b3.setProperty("kind", "secondary"); b3.clicked.connect(self.view_full)
        b4 = QPushButton("Print First + Match"); b4.setProperty("kind", "secondary"); b4.clicked.connect(self.print)
        row = QHBoxLayout(); row.setSpacing(10)
        for b in (b1, b2, b3, b4):
            row.addWidget(b)
        row.addStretch()
        right = QVBoxLayout(); right.addWidget(name); right.addWidget(where); right.addStretch(); right.addLayout(row)
        lay = QHBoxLayout(self); lay.setContentsMargins(14, 14, 14, 14); lay.setSpacing(18)
        lay.addLayout(previews); lay.addLayout(right, 1)
        POOL.start(ThumbTask(self.sig, 0, library, res.document_id, res.abs_path, match.page_number, THUMB_W,
                             res.file_size, res.file_mtime_ns))
        POOL.start(ThumbTask(self.front_sig, 0, library, res.document_id, res.abs_path, 1, THUMB_W,
                             res.file_size, res.file_mtime_ns))

    def _thumb(self, token, data, _z):
        pm = QPixmap(); pm.loadFromData(data, "PNG")
        self.thumb.setPixmap(pm)

    def _front_thumb(self, token, data, _z):
        pm = QPixmap(); pm.loadFromData(data, "PNG")
        self.front_thumb.setPixmap(pm)

    def view_front_page(self):
        self.win.show_viewer(self.res.abs_path, self.res.filename, self.res.page_count, 1, "", back_to=0)

    def view_page(self):
        self.win.show_viewer(self.res.abs_path, self.res.filename, self.res.page_count, self.match.page_number,
                             self.match.matched_text, back_to=0)

    def view_full(self):
        self.win.show_viewer(self.res.abs_path, self.res.filename, self.res.page_count, 1, "", back_to=0)

    def print(self):
        if not self.res.abs_path.is_file():
            QMessageBox.warning(self, "Print", "This PDF is no longer in the folder.")
            return
        print_document(self, self.res.abs_path, self.res.page_count, self.match.page_number, self.res.filename,
                       initial_pages=[1, self.match.page_number])


class SearchPage(QWidget):
    def __init__(self, ctx, win):
        super().__init__()
        self.setObjectName("page")
        self.ctx, self.win, self.token = ctx, win, 0
        self.sig = SearchSignals(self)
        self.sig.done.connect(self._done)
        self.sig.failed.connect(self._failed)

        title = QLabel("Find a certificate"); title.setObjectName("h1")
        self.input = QLineEdit(); self.input.setObjectName("search")
        self.input.setPlaceholderText("Type a serial number or certificate ID")
        self.input.setClearButtonEnabled(True)
        self.input.returnPressed.connect(self.run_search)
        self.btn = QPushButton("Search"); self.btn.setMinimumHeight(54); self.btn.setMinimumWidth(140)
        self.btn.clicked.connect(self.run_search)
        self.status = QLabel(""); self.status.setObjectName("muted")
        self.body = QWidget(); self.lay = QVBoxLayout(self.body); self.lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.lay.setSpacing(12); self.lay.setContentsMargins(0, 0, 8, 0)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(self.body)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bar = QHBoxLayout(); bar.setSpacing(12); bar.addWidget(self.input, 1); bar.addWidget(self.btn)
        lay = QVBoxLayout(self); lay.setContentsMargins(32, 28, 32, 20); lay.setSpacing(14)
        lay.addWidget(title); lay.addLayout(bar); lay.addWidget(self.status); lay.addWidget(self.scroll, 1)
        self.message("Type a serial number above and press Enter.")

    def focus_input(self):
        self.input.setFocus(); self.input.selectAll()

    def _clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def message(self, text):
        self._clear()
        l = QLabel(text); l.setWordWrap(True); l.setObjectName("muted"); l.setStyleSheet("padding:16px 4px;")
        self.lay.addWidget(l)

    def run_search(self):
        q = self.input.text().strip()
        if not self.ctx.library:
            self.status.setText("No folder is selected.")
            return
        if not q:
            self.status.setText("Enter a serial number first.")
            return
        self.token += 1
        self.status.setText("Searching\u2026")
        POOL.start(SearchTask(self.sig, self.token, self.ctx.library, q))

    def _failed(self, token, msg):
        if token == self.token:
            self.status.setText("")
            self.message(msg)

    def _done(self, token, query, results, secs):
        if token != self.token:
            return
        pages = sum(len(d.matches) for d in results)
        if not results:
            self.status.setText("")
            extra = "  Documents are still being loaded, so try again in a moment." if self.win.worker is not None else ""
            self.message(f"No document contains \u201c{query}\u201d.{extra}")
            return
        self.status.setText(f"{pages} matching page{'s' if pages != 1 else ''} in {len(results)} document{'s' if len(results) != 1 else ''}")
        self._clear()
        for d in results:
            for m in d.matches:
                self.lay.addWidget(ResultCard(self.ctx.library, d, m, self.win))
