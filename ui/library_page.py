from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.external_open import open_pdf
from core.library_sync import purge_missing
from database.schema import STATUS_LABELS
from utils.path_utils import format_size
from workers.base_worker import FuncWorker
from workers.indexing_worker import RebuildIndexWorker
from workers.ocr_worker import RetryFailedWorker

FILTERS = [("All documents", None), ("Indexed", "indexed"), ("Partially indexed", "partial"), ("Failed", "failed"),
           ("Waiting for retry", "waiting_retry"), ("Pending / modified", "pending"), ("Missing", "missing")]


class LibraryPage(QWidget):
    def __init__(self, ctx, win):
        super().__init__()
        self.ctx, self.win = ctx, win
        self.location = QLabel(); self.location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.stats = QLabel(); self.stats.setStyleSheet("color:#444;")
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter by file name\u2026"); self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.refresh)
        self.combo = QComboBox()
        for label, _ in FILTERS:
            self.combo.addItem(label)
        self.combo.currentIndexChanged.connect(self.refresh)

        cols = ["File name", "Size", "Imported", "Pages", "Processing status", "Availability", "Path / note"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.open_selected)

        def btn(text, slot, tip=""):
            b = QPushButton(text); b.setToolTip(tip); b.clicked.connect(slot); return b
        r1 = QHBoxLayout()
        for b in (btn("Add PDFs\u2026", self.add_files), btn("Add folder\u2026", self.add_folder),
                  btn("Remove selected\u2026", self.remove_selected), btn("Purge missing\u2026", self.purge_selected,
                      "Permanently forget selected missing documents now"),
                  btn("Sync Library", lambda: self.win.start_sync(), "Compare the library folder with the index"),
                  btn("Retry failed processing", self.retry, "Retry OCR/indexing for failed or incomplete documents"),
                  btn("Rebuild index", lambda: self.win.rebuild_index(False), "Rebuild the search index from stored text"),
                  btn("Restore ignored files", self.clear_ignored, "Re-include files you removed from the index but kept on disk")):
            r1.addWidget(b)
        r1.addStretch()
        r2 = QHBoxLayout(); r2.addWidget(self.filter, 1); r2.addWidget(self.combo)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<h3>Library</h3>")); lay.addWidget(self.location); lay.addWidget(self.stats)
        lay.addLayout(r1); lay.addLayout(r2); lay.addWidget(self.table, 1)

    # ---- data ---------------------------------------------------------------------
    def refresh(self):
        lib = self.ctx.library
        if not lib:
            return
        self.location.setText(f"<b>Location:</b> {lib.root}")
        conn = lib.connect()
        try:
            st = lib.stats(conn)
            self.stats.setText(
                f"{st['documents']} document(s) \u2022 {st['pages']} pages \u2022 {format_size(st['bytes'])} \u2022 "
                f"indexed {st['indexed']} \u2022 partial {st['partial']} \u2022 failed {st['failed']} \u2022 "
                f"waiting retry {st['waiting_retry']} \u2022 pending {st['pending']} \u2022 "
                f"missing {st['missing']} \u2022 ignored files {st['ignored']}")
            where, args = ["1=1"], []
            text = self.filter.text().strip()
            if text:
                where.append("filename LIKE ?"); args.append(f"%{text}%")
            key = FILTERS[self.combo.currentIndex()][1]
            if key == "missing":
                where.append("availability_status='missing'")
            elif key == "pending":
                where.append("availability_status='available' AND processing_status IN ('imported','processing','text_extracted','ocr_processing','modified')")
            elif key:
                where.append("availability_status='available' AND processing_status=?"); args.append(key)
            rows = conn.execute("SELECT d.*, m.recovery_deadline FROM documents d LEFT JOIN missing_documents m "
                                "ON m.document_id=d.document_id WHERE " + " AND ".join(where) +
                                " ORDER BY filename COLLATE NOCASE LIMIT 5000", args).fetchall()
        finally:
            conn.close()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            missing = r["availability_status"] == "missing"
            note = r["relative_path"]
            if missing and r["recovery_deadline"]:
                note = "Recoverable until " + datetime.fromtimestamp(r["recovery_deadline"]).strftime("%Y-%m-%d %H:%M")
            elif r["last_error"]:
                note = r["last_error"]
            vals = [r["filename"], format_size(r["file_size"] or 0),
                    datetime.fromtimestamp(r["import_timestamp"] or 0).strftime("%Y-%m-%d %H:%M"), str(r["page_count"]),
                    STATUS_LABELS.get(r["processing_status"], r["processing_status"]),
                    "Missing" if missing else r["availability_status"].capitalize(), note]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, r["document_id"])
                    it.setToolTip(r["relative_path"])
                if missing:
                    it.setForeground(QColor("#c0392b"))
                elif r["processing_status"] in ("failed",):
                    it.setForeground(QColor("#c0392b"))
                elif r["processing_status"] in ("partial", "waiting_retry"):
                    it.setForeground(QColor("#b8860b"))
                self.table.setItem(i, c, it)

    def selected_ids(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    # ---- actions ---------------------------------------------------------------------
    def add_files(self):
        self.win.import_page.pick_files()

    def add_folder(self):
        self.win.import_page.pick_folder()

    def open_selected(self):
        ids = self.selected_ids()
        if ids:
            conn = self.ctx.library.connect()
            try:
                r = conn.execute("SELECT relative_path FROM documents WHERE document_id=?", (ids[0],)).fetchone()
            finally:
                conn.close()
            if r:
                ok, msg = open_pdf(self.ctx.library.abs_path(r["relative_path"]))
                if not ok:
                    self.win.flash(msg)

    def remove_selected(self):
        ids = self.selected_ids()
        if not ids or self.win.busy():
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Remove documents")
        box.setText(f"Remove {len(ids)} selected document(s) from the application?")
        box.setInformativeText("\u2022 Remove from app: deletes the search data, OCR text and previews; the PDF stays in the "
                               "library folder but will not be re-added by Sync.\n"
                               "\u2022 Delete permanently: also deletes the library copy of the PDF.\n"
                               "Your original source files elsewhere are never touched.")
        keep = box.addButton("Remove from app (keep file)", QMessageBox.AcceptRole)
        delete = box.addButton("Delete library copy permanently", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (keep, delete):
            return
        if clicked is delete:
            if QMessageBox.question(self, "Confirm permanent deletion",
                                    f"Permanently delete {len(ids)} PDF file(s) from the library folder?\nThis cannot be undone.",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        lib = self.ctx.library
        conn = lib.connect()
        try:
            out = lib.remove_documents(conn, ids, delete_files=clicked is delete)
        finally:
            conn.close()
        if out["errors"]:
            QMessageBox.warning(self, "Some documents could not be removed", "\n".join(out["errors"][:10]))
        self.ctx.data_changed.emit()

    def purge_selected(self):
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self, "Purge missing", "Select missing documents in the list first (filter: Missing).")
            return
        conn = self.ctx.library.connect()
        try:
            q = ",".join("?" * len(ids))
            n = conn.execute(f"SELECT COUNT(*) FROM documents WHERE availability_status='missing' AND document_id IN ({q})", ids).fetchone()[0]
        finally:
            conn.close()
        if not n:
            QMessageBox.information(self, "Purge missing", "None of the selected documents is marked Missing.")
            return
        if QMessageBox.question(self, "Purge missing documents",
                                f"Permanently remove all records, OCR text and previews of {n} missing document(s)?\n"
                                "They can no longer be restored with their existing index.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            purge_missing(self.ctx.library, ids)
            self.ctx.data_changed.emit()

    def retry(self):
        ids = self.selected_ids() or None
        self.win.run_task(RetryFailedWorker(self.ctx.library, ids, self.ctx.config.tesseract_cmd), self.win.after_generic, "Retrying")

    def clear_ignored(self):
        conn = self.ctx.library.connect()
        try:
            self.ctx.library.clear_ignored(conn)
        finally:
            conn.close()
        self.win.start_sync()
