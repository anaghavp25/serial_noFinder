from typing import Callable, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from core.external_open import open_pdf, open_pdf_at_page, reveal_in_file_manager
from core.serial_search import DocumentResult
from database.schema import STATUS_LABELS
from ui.pdf_preview import POOL, ThumbTask, _Signals
from utils.path_utils import format_size

THUMB_W = 170


class ThumbLabel(QLabel):
    clicked = Signal()

    def __init__(self, caption_color="#444"):
        super().__init__("Loading\u2026")
        self.setFixedSize(THUMB_W + 8, int(THUMB_W * 1.38) + 8)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"background:#f3f3f5;border:1px solid #c9c9d0;color:{caption_color};")
        self.setCursor(Qt.PointingHandCursor)
        self._token = 0
        self.sig = _Signals(self)
        self.sig.done.connect(self._done)
        self.sig.failed.connect(self._failed)

    def mousePressEvent(self, e):
        self.clicked.emit()

    def load(self, library, res: DocumentResult, page: int):
        self._token += 1
        self.setPixmap(QPixmap())
        self.setText("Loading\u2026")
        t = ThumbTask(self.sig, self._token, library, res.document_id, res.abs_path, page, THUMB_W, res.file_size, res.file_mtime_ns)
        POOL.start(t)

    def _done(self, token, data, *_):
        if token == self._token:
            pm = QPixmap()
            pm.loadFromData(data, "PNG")
            self.setPixmap(pm)

    def _failed(self, token, msg):
        if token == self._token:
            self.setPixmap(QPixmap())
            self.setText(msg)


class ResultCard(QFrame):
    preview_requested = Signal(object, int)   # DocumentResult, page
    message = Signal(str)

    def __init__(self, library, res: DocumentResult, query: str):
        super().__init__()
        self.library, self.res, self.query, self.idx = library, res, query, 0
        approx = not res.exact
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("ResultCard{background:%s;border:1px solid %s;border-radius:6px;}" %
                           ("#fff8ec" if approx else "#ffffff", "#e0b060" if approx else "#cfcfd6"))

        name = QLabel(f"<b>{_esc(res.filename)}</b>")
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        badge = QLabel("APPROXIMATE MATCH" if approx else "EXACT MATCH")
        badge.setStyleSheet("padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;color:white;background:%s;" %
                            ("#d98a00" if approx else "#2e8b57"))
        status = QLabel(f"{res.page_count} page(s)  \u2022  {STATUS_LABELS.get(res.status, res.status)}")
        status.setStyleSheet("color:#666;")
        head = QHBoxLayout(); head.addWidget(name); head.addWidget(badge); head.addStretch(); head.addWidget(status)

        self.match_info = QLabel(); self.match_info.setWordWrap(True)
        self.snippet = QLabel(); self.snippet.setWordWrap(True); self.snippet.setStyleSheet("color:#333;")
        self.snippet.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.b_prev = QPushButton("\u25C0"); self.b_next = QPushButton("\u25B6")
        self.b_prev.setFixedWidth(30); self.b_next.setFixedWidth(30)
        self.b_prev.setToolTip("Previous matching page in this PDF"); self.b_next.setToolTip("Next matching page in this PDF")
        self.b_prev.clicked.connect(lambda: self._step(-1)); self.b_next.clicked.connect(lambda: self._step(1))
        nav = QHBoxLayout(); nav.addWidget(self.b_prev); nav.addWidget(self.match_info); nav.addWidget(self.b_next); nav.addStretch()
        if len(res.matches) < 2:
            self.b_prev.hide(); self.b_next.hide()

        self.t_first = ThumbLabel(); self.t_match = ThumbLabel("#2e8b57")
        self.t_first.setToolTip("First page of this PDF"); self.t_match.setToolTip("Matching page")
        self.t_first.clicked.connect(lambda: self.preview_requested.emit(self.res, 1))
        self.t_match.clicked.connect(lambda: self.preview_requested.emit(self.res, self.cur.page_number))
        self.cap_first = QLabel("<b>First page</b>"); self.cap_match = QLabel()
        col1 = QVBoxLayout(); col1.addWidget(self.cap_first); col1.addWidget(self.t_first)
        col2 = QVBoxLayout(); col2.addWidget(self.cap_match); col2.addWidget(self.t_match)
        right = QVBoxLayout(); right.addLayout(nav); right.addWidget(self.snippet); right.addStretch()
        mid = QHBoxLayout(); mid.addLayout(col1); mid.addLayout(col2); mid.addLayout(right, 1)

        self.path = QLabel(str(res.abs_path)); self.path.setStyleSheet("color:#888;font-size:11px;")
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse); self.path.setWordWrap(True)

        def btn(text, slot, tip=""):
            b = QPushButton(text); b.setToolTip(tip); b.clicked.connect(slot); return b
        row = QHBoxLayout()
        row.addWidget(btn("View First Page", lambda: self.preview_requested.emit(self.res, 1)))
        row.addWidget(btn("View Matching Page", lambda: self.preview_requested.emit(self.res, self.cur.page_number)))
        row.addWidget(btn("Open PDF", lambda: self._act(open_pdf(self.res.abs_path))))
        row.addWidget(btn("Open PDF at Matching Page", lambda: self._act(open_pdf_at_page(self.res.abs_path, self.cur.page_number))))
        row.addWidget(btn("Show in Folder", lambda: self._act(reveal_in_file_manager(self.res.abs_path))))
        row.addStretch()

        lay = QVBoxLayout(self)
        lay.addLayout(head); lay.addLayout(mid); lay.addWidget(self.path); lay.addLayout(row)
        self.t_first.load(library, res, 1)
        self._show_match()

    @property
    def cur(self):
        return self.res.matches[self.idx]

    def _show_match(self):
        m = self.cur
        n = len(self.res.matches)
        kind = "" if self.res.exact else f" (similarity {int(m.similarity * 100)}%)"
        self.match_info.setText(f"<b>{_esc(m.matched_text)}</b>{kind} \u2014 page {m.page_number} of {self.res.page_count}"
                                f"   [match {self.idx + 1} of {n}]")
        self.cap_match.setText(f"<b style='color:#2e8b57'>Matching page {m.page_number}</b>")
        self.snippet.setText("\u2026" + _esc(m.snippet) + "\u2026")
        self.t_match.load(self.library, self.res, m.page_number)

    def _step(self, d):
        self.idx = (self.idx + d) % len(self.res.matches)
        self._show_match()

    def _act(self, result):
        ok, msg = result
        if msg:
            self.message.emit(msg)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ResultsWidget(QScrollArea):
    preview_requested = Signal(object, int, str)
    message = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setAlignment(Qt.AlignTop)
        self.setWidget(self.body)

    def clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def show_text(self, text: str):
        self.clear()
        lab = QLabel(text); lab.setWordWrap(True); lab.setStyleSheet("color:#555;padding:12px;")
        self.lay.addWidget(lab)

    def set_results(self, library, query: str, exact: List[DocumentResult], approx: List[DocumentResult]):
        self.clear()
        if not exact and not approx:
            self.show_text(f"No documents contain the serial number \u201c{query}\u201d.")
            return
        if exact:
            self._section(f"Exact matches \u2014 {len(exact)} document(s), {sum(len(d.matches) for d in exact)} page(s)", "#2e8b57")
            for r in exact:
                self._card(library, r, query)
        if approx:
            self._section("Approximate matches (not exact \u2014 please verify)", "#d98a00")
            for r in approx:
                self._card(library, r, query)
        elif not exact:
            pass

    def _section(self, text, color):
        l = QLabel(f"<b>{text}</b>")
        l.setStyleSheet(f"color:{color};padding:4px 2px;font-size:13px;")
        self.lay.addWidget(l)

    def _card(self, library, res, query):
        c = ResultCard(library, res, query)
        c.preview_requested.connect(lambda r, p: self.preview_requested.emit(
            r, p, next((m.matched_text for m in r.matches if m.page_number == p), "")))
        c.message.connect(self.message)
        self.lay.addWidget(c)
