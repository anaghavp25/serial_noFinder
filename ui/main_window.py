from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from core.library_manager import Library, LibraryUnavailable
from core.preview_renderer import enforce_cache_limit
from database.db_manager import DatabaseCorruptError
from ui.app_context import AppContext
from ui.documents_page import DocumentsPage
from ui.pdf_viewer import PdfViewer
from ui.search_page import SearchPage
from workers.base_worker import TaskWorker
from workers.sync_worker import SyncWorker

SIDE_W = 250


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("PDF Serial Finder")
        self.resize(1280, 820)
        self.worker: Optional[TaskWorker] = None
        self._zombies = []
        self.ready = False
        self.back_to = 0

        # ---- side panel
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(SIDE_W)
        brand = QLabel("PDF Serial\nFinder"); brand.setObjectName("brand")
        self.nav_search = self._nav("Search"); self.nav_docs = self._nav("Documents")
        group = QButtonGroup(self); group.setExclusive(True)
        group.addButton(self.nav_search); group.addButton(self.nav_docs)
        self.nav_search.setChecked(True)
        self.nav_search.clicked.connect(lambda: self.goto(0)); self.nav_docs.clicked.connect(lambda: self.goto(1))
        self.folder_lbl = QLabel(""); self.folder_lbl.setObjectName("sidemuted")
        self.b_change = QPushButton("Change Directory"); self.b_change.setProperty("kind", "side")
        self.b_change.clicked.connect(self.change_directory)
        self.load_lbl = QLabel(""); self.load_lbl.setObjectName("sidemuted")
        self.load_bar = QProgressBar(); self.load_bar.setTextVisible(False); self.load_bar.setFixedHeight(8)
        self.load_lbl.hide(); self.load_bar.hide()
        sl = QVBoxLayout(side); sl.setContentsMargins(16, 24, 16, 20); sl.setSpacing(8)
        sl.addWidget(brand); sl.addSpacing(18); sl.addWidget(self.nav_search); sl.addWidget(self.nav_docs); sl.addStretch()
        sl.addWidget(self.load_lbl); sl.addWidget(self.load_bar); sl.addSpacing(8)
        cap = QLabel("Folder"); cap.setObjectName("sidemuted")
        sl.addWidget(cap); sl.addWidget(self.folder_lbl); sl.addWidget(self.b_change)

        # ---- pages
        self.search_page = SearchPage(ctx, self)
        self.docs_page = DocumentsPage(ctx, self)
        self.viewer = PdfViewer()
        self.viewer.back.connect(lambda: self.goto(self.back_to))
        self.stack = QStackedWidget()
        for p in (self.search_page, self.docs_page, self.viewer):
            self.stack.addWidget(p)
        central = QWidget(); h = QHBoxLayout(central); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addWidget(side); h.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        ctx.data_changed.connect(self.docs_page.refresh)
        self.live = QTimer(self); self.live.setInterval(1500); self.live.timeout.connect(self._live_refresh)
        self.focus_timer = QTimer(self); self.focus_timer.setSingleShot(True); self.focus_timer.setInterval(600)
        self.focus_timer.timeout.connect(self._sync_if_idle)
        QApplication.instance().applicationStateChanged.connect(self._app_state)
        QTimer.singleShot(0, self.startup)

    @staticmethod
    def _nav(text):
        b = QPushButton(text); b.setCheckable(True); b.setProperty("kind", "nav"); b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # ------------------------------------------------------------ navigation
    def goto(self, index: int):
        self.stack.setCurrentIndex(index)
        if index in (0, 1):
            (self.nav_search, self.nav_docs)[index].setChecked(True)
        if index == 0:
            self.search_page.focus_input()
        elif index == 1:
            self.docs_page.refresh()

    def show_viewer(self, path, name, page_count, page, highlight, back_to):
        if not Path(path).is_file():
            QMessageBox.information(self, "PDF not found", "This PDF is no longer in the folder.")
            self.start_sync()
            return
        self.back_to = back_to
        (self.nav_search, self.nav_docs)[back_to].setChecked(True)
        self.viewer.open(path, name, page_count, page, highlight)
        self.stack.setCurrentIndex(2)

    # ------------------------------------------------------------ startup / folder selection
    def startup(self):
        state = self.ctx.open_active()
        for _ in range(50):
            if state == "ok":
                break
            if state == "none":
                if not self._pick_folder(first=True):
                    QApplication.quit()
                    return
                state = "ok"
                break
            if state == "corrupt":      # the folder is the source of truth: just rebuild the index from it
                Library(Path(self.ctx.config.active_library_path)).recover_database()
                state = self.ctx.open_active()
                continue
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Folder not available")
            box.setText("Your certificate folder cannot be opened.")
            box.setInformativeText(f"{self.ctx.problem}\n\nNothing has been changed. If the folder is on an external drive, "
                                   "reconnect it and choose Retry.")
            retry = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
            other = box.addButton("Choose Another Folder", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            c = box.clickedButton()
            if c is retry:
                state = self.ctx.open_active()
            elif c is other:
                if self._pick_folder():
                    state = "ok"
                    break
            else:
                QApplication.quit()
                return
        self.ready = True
        self._folder_changed()
        self.start_sync()

    def _pick_folder(self, first: bool = False) -> bool:
        """Native folder dialog. Opens the chosen folder; the active folder only changes on success."""
        start = self.ctx.config.active_library_path or str(Path.home())
        caption = "Select the folder that contains your certificate PDFs"
        while True:
            d = QFileDialog.getExistingDirectory(self, caption, start)
            if not d:
                return False
            try:
                lib = Library.open(d)
            except LibraryUnavailable as e:
                QMessageBox.warning(self, "This folder cannot be used", str(e))
                start = d
                continue
            except DatabaseCorruptError:
                Library(Path(d)).recover_database()
                lib = Library.open(d)
            self.ctx.activate(lib)
            return True

    def change_directory(self):
        self.stop_worker()
        if self._pick_folder():
            self.search_page._clear()
            self.search_page.message("Type a serial number above and press Enter.")
            self.search_page.status.setText("")
            self._folder_changed()
            self.goto(1)
            self.start_sync()

    def _folder_changed(self):
        lib = self.ctx.library
        fm = QFontMetrics(self.folder_lbl.font())
        self.folder_lbl.setText(fm.elidedText(str(lib.root), Qt.TextElideMode.ElideMiddle, SIDE_W - 40))
        self.folder_lbl.setToolTip(str(lib.root))
        self.docs_page.refresh()
        self.search_page.focus_input()
        try:
            enforce_cache_limit(lib, int(lib.settings().get("preview_cache_max_mb", 500)))
        except Exception:
            pass

    # ------------------------------------------------------------ background loading
    def start_sync(self):
        if not self.ctx.library or self.worker is not None:
            return
        w = SyncWorker(self.ctx.library)
        self.worker = w
        w.progress.connect(self._on_progress)
        w.succeeded.connect(lambda _r, w=w: self._task_done(w))
        w.failed.connect(lambda msg, w=w: self._task_failed(w, msg))
        self.load_lbl.setText("Checking folder\u2026"); self.load_lbl.show()
        self.load_bar.setRange(0, 0); self.load_bar.show()
        self.live.start()
        w.start()

    def _on_progress(self, p):
        if p.total:
            self.load_bar.setRange(0, p.total); self.load_bar.setValue(min(p.done, p.total))
            self.load_lbl.setText(f"Loading documents\u2026 {min(p.done, p.total)} of {p.total}")

    def _live_refresh(self):
        if self.stack.currentIndex() == 1:
            self.docs_page.refresh()

    def _finish(self, w):
        if self.worker is w:
            self.worker = None
        self._zombies.append(w)
        w.finished.connect(lambda: (self._zombies.remove(w) if w in self._zombies else None, w.deleteLater()))
        self.live.stop()
        self.load_lbl.hide(); self.load_bar.hide()

    def _task_done(self, w):
        self._finish(w)
        self.ctx.data_changed.emit()

    def _task_failed(self, w, msg):
        self._finish(w)
        self.ctx.data_changed.emit()
        QMessageBox.warning(self, "Could not read the folder", msg)

    def stop_worker(self):
        w = self.worker
        if w is not None:
            w.cancel()
            w.wait(60000)
            QApplication.processEvents()          # lets the queued 'finished' signal clear self.worker
            if self.worker is w:
                self._finish(w)

    def _app_state(self, state):
        if state == Qt.ApplicationState.ApplicationActive and self.ready:
            self.focus_timer.start()

    def _sync_if_idle(self):
        if self.worker is None and self.stack.currentIndex() != 2:
            self.start_sync()                      # cheap: unchanged files are skipped by size/date

    def closeEvent(self, e):
        w = self.worker
        if w is not None:
            w.cancel()
            w.wait(30000)
        for z in list(self._zombies):
            z.wait(5000)
        e.accept()
