import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from config.app_config import APP_NAME, APP_VERSION
from core.library_sync import purge_missing
from core.preview_renderer import cache_size, clear_cache, enforce_cache_limit
from utils.path_utils import format_size


class SettingsPage(QScrollArea):
    def __init__(self, ctx, win):
        super().__init__()
        self.ctx, self.win = ctx, win
        self.setWidgetResizable(True)
        body = QWidget(); self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.addWidget(QLabel("<h3>Settings</h3>"))
        self._loading = False

        # -- location
        g = QGroupBox("Library location"); gl = QVBoxLayout(g)
        self.path = QLabel(); self.path.setTextInteractionFlags(Qt.TextSelectableByMouse); self.path.setWordWrap(True)
        row = QHBoxLayout()
        b1 = QPushButton("Change Library Location\u2026"); b1.clicked.connect(self.win.change_library_location)
        b2 = QPushButton("Open library folder"); b2.clicked.connect(self.open_folder)
        row.addWidget(b1); row.addWidget(b2); row.addStretch()
        gl.addWidget(self.path); gl.addLayout(row); lay.addWidget(g)

        # -- OCR
        g = QGroupBox("OCR (local, offline)"); f = QFormLayout(g)
        self.ocr_on = QCheckBox("Run OCR on pages without usable text"); f.addRow(self.ocr_on)
        self.lang = QLineEdit(); self.lang.setPlaceholderText("eng  (combine with +, e.g. eng+deu)"); f.addRow("OCR language:", self.lang)
        self.tess = QLineEdit(); self.tess.setPlaceholderText("Auto-detect")
        bb = QPushButton("Browse\u2026"); bb.clicked.connect(self.browse_tess)
        tr = QHBoxLayout(); tr.addWidget(self.tess, 1); tr.addWidget(bb)
        f.addRow("Tesseract executable:", tr)
        self.ocr_status = QLabel(); self.ocr_status.setWordWrap(True); f.addRow(self.ocr_status)
        self.dpi = QSpinBox(); self.dpi.setRange(150, 600); self.dpi.setSingleStep(50); f.addRow("OCR resolution (DPI):", self.dpi)
        lay.addWidget(g)

        # -- behaviour
        g = QGroupBox("Import, search and synchronization"); f = QFormLayout(g)
        self.sub = QCheckBox("Include subfolders when importing a folder"); f.addRow(self.sub)
        self.auto = QCheckBox("Synchronize automatically at startup"); f.addRow(self.auto)
        self.mon = QCheckBox("Monitor the library folder and sync when files change"); f.addRow(self.mon)
        self.exact = QCheckBox("Exact matching is the default (approximate results only when requested)"); f.addRow(self.exact)
        self.fuzzy = QCheckBox("Offer approximate (fuzzy) search option"); f.addRow(self.fuzzy)
        self.days = QSpinBox(); self.days.setRange(0, 3650); self.days.setSuffix(" days"); f.addRow("Keep records of missing files for:", self.days)
        pb = QPushButton("Purge all missing documents now\u2026"); pb.clicked.connect(self.purge_all); f.addRow(pb)
        lay.addWidget(g)

        # -- cache / index
        g = QGroupBox("Cache and index"); f = QFormLayout(g)
        self.cache_lbl = QLabel()
        self.cache_max = QSpinBox(); self.cache_max.setRange(10, 100000); self.cache_max.setSuffix(" MB")
        cb = QPushButton("Clear preview cache"); cb.clicked.connect(self.clear_cache)
        rb = QPushButton("Rebuild search index"); rb.clicked.connect(lambda: self.win.rebuild_index(False))
        xb = QPushButton("Clear index and re-process all documents\u2026"); xb.clicked.connect(self.clear_index)
        f.addRow("Preview cache:", self.cache_lbl); f.addRow("Cache size limit:", self.cache_max)
        f.addRow(cb); f.addRow(rb); f.addRow(xb)
        lay.addWidget(g)

        # -- about
        g = QGroupBox("Application information"); gl = QVBoxLayout(g)
        self.about = QLabel(); self.about.setTextInteractionFlags(Qt.TextSelectableByMouse); self.about.setWordWrap(True)
        gl.addWidget(self.about); lay.addWidget(g)
        lay.addStretch()

        for w, key in ((self.ocr_on, "ocr_enabled"), (self.sub, "include_subfolders"), (self.auto, "auto_sync_enabled"),
                       (self.mon, "folder_monitoring_enabled"), (self.exact, "exact_search_default"), (self.fuzzy, "fuzzy_search_enabled")):
            w.toggled.connect(lambda v, k=key: self._save(k, bool(v)))
        self.lang.editingFinished.connect(lambda: self._save("ocr_language", self.lang.text().strip() or "eng"))
        self.dpi.valueChanged.connect(lambda v: self._save("ocr_dpi", int(v)))
        self.days.valueChanged.connect(lambda v: self._save("missing_file_recovery_days", int(v)))
        self.cache_max.valueChanged.connect(lambda v: self._save("preview_cache_max_mb", int(v)))
        self.tess.editingFinished.connect(self.save_tess)
        self.mon.toggled.connect(lambda _: self.win.update_watcher())

    def _save(self, key, value):
        if not self._loading and self.ctx.library:
            self.ctx.set_setting(key, value)
            if key in ("ocr_language", "ocr_dpi", "ocr_enabled"):
                self.update_ocr_status()

    def refresh(self):
        lib = self.ctx.library
        if not lib:
            return
        self._loading = True
        s = lib.settings()
        self.path.setText(f"<b>{lib.root}</b>")
        self.ocr_on.setChecked(s["ocr_enabled"]); self.lang.setText(s["ocr_language"]); self.dpi.setValue(int(s["ocr_dpi"]))
        self.tess.setText(self.ctx.config.tesseract_cmd)
        self.sub.setChecked(s["include_subfolders"]); self.auto.setChecked(s["auto_sync_enabled"])
        self.mon.setChecked(s["folder_monitoring_enabled"]); self.exact.setChecked(s["exact_search_default"])
        self.fuzzy.setChecked(s["fuzzy_search_enabled"]); self.days.setValue(int(s["missing_file_recovery_days"]))
        self.cache_max.setValue(int(s["preview_cache_max_mb"]))
        self.cache_lbl.setText(format_size(cache_size(lib)))
        self.about.setText(f"{APP_NAME} {APP_VERSION}\nPython {platform.python_version()} on {platform.system()} {platform.release()}\n"
                           f"Configuration: {self.ctx.config.path}\nLogs: {self.ctx.config.log_dir}\n"
                           "All processing, OCR, indexing and search run locally. No network connections, no telemetry.")
        self._loading = False
        self.update_ocr_status()

    def update_ocr_status(self):
        e = self.ctx.ocr_engine()
        if e.available():
            self.ocr_status.setText(f"\u2705 Tesseract {e.version()} found at {e.cmd} (languages OK)")
            self.ocr_status.setStyleSheet("color:#2e8b57;")
        else:
            self.ocr_status.setText(f"\u26A0 OCR unavailable: {e.error}")
            self.ocr_status.setStyleSheet("color:#c0392b;")

    def save_tess(self):
        if not self._loading:
            self.ctx.config.set_tesseract_cmd(self.tess.text().strip())
            self.update_ocr_status()

    def browse_tess(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select tesseract executable", "", "Executables (*.exe);;All files (*)")
        if f:
            self.tess.setText(f)
            self.save_tess()

    def open_folder(self):
        from core.external_open import reveal_in_file_manager
        if self.ctx.library:
            reveal_in_file_manager(self.ctx.library.root)

    def clear_cache(self):
        n = clear_cache(self.ctx.library)
        self.win.flash(f"Removed {n} cached preview(s)")
        self.refresh()

    def purge_all(self):
        if self.win.busy():
            return
        if QMessageBox.question(self, "Purge missing documents",
                                "Permanently remove ALL records, OCR text and previews of documents whose files are missing?\n"
                                "This skips the recovery period.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            names = purge_missing(self.ctx.library)
            self.win.flash(f"Purged {len(names)} missing document(s)")
            self.ctx.data_changed.emit()

    def clear_index(self):
        if QMessageBox.question(self, "Clear index",
                                "This deletes all extracted text, OCR results and the search index, then re-reads every PDF "
                                "(OCR can take a long time).\nYour PDF files are not touched. Continue?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.win.rebuild_index(True)
