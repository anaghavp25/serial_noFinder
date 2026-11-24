from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QHeaderView)

from workers.import_worker import ImportWorker


class ImportPage(QWidget):
    def __init__(self, ctx, win):
        super().__init__()
        self.ctx, self.win = ctx, win
        title = QLabel("<h3>Import PDF documents</h3>")
        info = QLabel("PDFs are <b>copied</b> into your library. Original files are never moved, changed or deleted. "
                      "Text is extracted automatically; scanned pages are OCR'd locally.")
        info.setWordWrap(True)
        self.b_files = QPushButton("Select PDF files\u2026")
        self.b_folder = QPushButton("Select a folder\u2026")
        self.sub = QCheckBox("Include nested subfolders")
        self.dups = QCheckBox("Import identical files again (keep extra copies)")
        self.b_files.clicked.connect(self.pick_files)
        self.b_folder.clicked.connect(self.pick_folder)
        self.summary = QLabel("")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Result", "Source file", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        row = QHBoxLayout()
        for w in (self.b_files, self.b_folder, self.sub, self.dups):
            row.addWidget(w)
        row.addStretch()
        lay = QVBoxLayout(self)
        lay.addWidget(title); lay.addWidget(info); lay.addLayout(row); lay.addWidget(self.summary); lay.addWidget(self.table, 1)
        self.refresh()

    def refresh(self):
        s = self.ctx.settings() if self.ctx.library else {}
        self.sub.setChecked(bool(s.get("include_subfolders", True)))
        for w in (self.b_files, self.b_folder):
            w.setEnabled(bool(self.ctx.library))

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDF files", "", "PDF files (*.pdf)")
        if files:
            self.start(files)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Select a folder containing PDFs")
        if d:
            self.start([d])

    def start(self, sources):
        if not self.ctx.library:
            return
        w = ImportWorker(self.ctx.library, sources, self.sub.isChecked(), self.dups.isChecked(), self.ctx.config.tesseract_cmd)
        self.summary.setText("Importing\u2026")
        self.win.run_task(w, self.finished, "Importing")

    def finished(self, report):
        if report is None:
            return
        self.table.setRowCount(0)
        colors = {"imported": "#2e8b57", "duplicate": "#b8860b", "skipped": "#777", "failed": "#c0392b"}
        from PySide6.QtGui import QColor
        for it in report.items:
            r = self.table.rowCount(); self.table.insertRow(r)
            a = QTableWidgetItem(it.outcome.capitalize()); a.setForeground(QColor(colors.get(it.outcome, "#000")))
            self.table.setItem(r, 0, a)
            self.table.setItem(r, 1, QTableWidgetItem(Path(it.source).name))
            self.table.setItem(r, 2, QTableWidgetItem(it.detail))
            self.table.item(r, 1).setToolTip(it.source)
        self.summary.setText(f"Imported {report.count('imported')}  \u2022  duplicates {report.count('duplicate')}  \u2022  "
                             f"skipped {report.count('skipped')}  \u2022  failed {report.count('failed')}"
                             + ("  \u2022  CANCELLED" if report.cancelled else ""))
