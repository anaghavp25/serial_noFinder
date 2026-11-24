from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

NOTES = {"failed": "Cannot be read", "waiting_retry": "Scanned \u2013 needs OCR to be searchable",
         "partial": "Some pages are not searchable"}


class DocumentsPage(QWidget):
    def __init__(self, ctx, win):
        super().__init__()
        self.setObjectName("page")
        self.ctx, self.win = ctx, win
        title = QLabel("Documents"); title.setObjectName("h1")
        self.info = QLabel(""); self.info.setObjectName("muted")
        self.b_view = QPushButton("View"); self.b_view.clicked.connect(self.view_selected)
        self.b_del = QPushButton("Delete"); self.b_del.setProperty("kind", "secondary"); self.b_del.clicked.connect(self.delete_selected)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Document", "Pages", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self.view_selected)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.empty = QLabel(""); self.empty.setObjectName("muted"); self.empty.setWordWrap(True)
        bar = QHBoxLayout(); bar.setSpacing(10); bar.addWidget(self.b_view); bar.addWidget(self.b_del); bar.addStretch()
        lay = QVBoxLayout(self); lay.setContentsMargins(32, 28, 32, 20); lay.setSpacing(12)
        lay.addWidget(title); lay.addWidget(self.info); lay.addLayout(bar); lay.addWidget(self.table, 1); lay.addWidget(self.empty)
        self._buttons()

    def refresh(self):
        lib = self.ctx.library
        if not lib:
            return
        conn = lib.connect()
        try:
            rows = conn.execute("SELECT document_id, filename, relative_path, page_count, processing_status FROM documents "
                                "WHERE availability_status='available' ORDER BY filename COLLATE NOCASE").fetchall()
        finally:
            conn.close()
        keep = set(self.selected_ids())
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["filename"], str(r["page_count"] or ""), NOTES.get(r["processing_status"], "")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, r["document_id"])
                    it.setToolTip(r["relative_path"])
                if c == 1:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, c, it)
            if r["document_id"] in keep:
                self.table.selectRow(i)
        n = len(rows)
        self.info.setText(f"{n} document{'s' if n != 1 else ''}  \u2022  {lib.root}")
        busy = self.win.worker is not None
        self.empty.setText("" if n else ("Loading documents\u2026" if busy else "No PDF files were found in this folder."))
        self._buttons()

    def selected_ids(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows]

    def _buttons(self):
        n = len(self.selected_ids())
        self.b_view.setEnabled(n == 1)
        self.b_del.setEnabled(n >= 1)

    def _row(self, doc_id):
        conn = self.ctx.library.connect()
        try:
            return conn.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone()
        finally:
            conn.close()

    def view_selected(self, *_):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        r = self._row(ids[0])
        if r:
            path = self.ctx.library.abs_path(r["relative_path"])
            if not path.is_file() or not r["page_count"]:
                QMessageBox.information(self, "View", "This document cannot be shown. It may have been moved, deleted or damaged.")
                return
            self.win.show_viewer(path, r["filename"], r["page_count"], 1, "", back_to=1)

    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        names = [self.table.item(r, 0).text() for r in sorted({i.row() for i in self.table.selectedIndexes()})]
        what = f"\u201c{names[0]}\u201d" if len(names) == 1 else f"{len(names)} documents"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete")
        box.setText(f"Delete {what}?")
        box.setInformativeText("The PDF file will be permanently deleted from your folder and removed from search. "
                               "This cannot be undone.")
        yes = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        self.win.stop_worker()                      # never delete while a background scan is reading files
        lib = self.ctx.library
        conn = lib.connect()
        try:
            out = lib.delete_documents(conn, ids)
        finally:
            conn.close()
        if out["errors"]:
            QMessageBox.warning(self, "Some files could not be deleted", "\n".join(out["errors"][:8]))
        self.ctx.data_changed.emit()
        self.win.start_sync()
