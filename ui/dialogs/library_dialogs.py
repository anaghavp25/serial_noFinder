from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton, QVBoxLayout)

from core.library_manager import inspect_folder
from core.library_relocator import validate_target
from utils.path_utils import format_size


class LibrarySetupDialog(QDialog):
    """Pick (or create) the folder for a permanent library. Used on first launch and for 'switch library'."""

    def __init__(self, parent=None, title="Choose your PDF library", intro="", known: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.path: Optional[Path] = None
        lay = QVBoxLayout(self)
        lbl = QLabel(intro or "Choose the folder where your permanent PDF library will live. It can be anywhere "
                              "(Desktop, Documents, another drive, an external disk). You can select an existing "
                              "library, an empty folder, or create a new folder.")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        if known:
            self.known = QComboBox()
            self.known.addItem("Previously used libraries\u2026", "")
            for k in known:
                self.known.addItem(k, k)
            self.known.currentIndexChanged.connect(lambda: self.edit.setText(self.known.currentData() or self.edit.text()))
            lay.addWidget(self.known)
        row = QHBoxLayout()
        self.edit = QLineEdit(); self.edit.setPlaceholderText("Library folder")
        self.edit.textChanged.connect(self._check)
        b = QPushButton("Browse\u2026"); b.clicked.connect(self._browse)
        row.addWidget(self.edit, 1); row.addWidget(b)
        lay.addLayout(row)
        self.info = QLabel(""); self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept); self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        lay.addWidget(self.buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select library folder", self.edit.text() or "")
        if d:
            self.edit.setText(d)

    def _check(self):
        t = self.edit.text().strip()
        ok = False
        if not t:
            self.info.setText("")
        else:
            st = inspect_folder(t) if Path(t).exists() else None
            if st is None:
                self.info.setText("\u2795 This folder does not exist yet and will be created as a new, empty library.")
                ok = Path(t).anchor != "" and Path(t).parent.exists()
                if not ok:
                    self.info.setText("The parent folder or drive does not exist.")
            elif st.state == "library":
                self.info.setText("\u2705 An existing library was found here and will be opened.")
                ok = True
            elif st.state == "empty":
                self.info.setText("\u2795 Empty folder: a new library will be created here.")
                ok = True
            elif st.state == "foreign":
                self.info.setText("\u26A0 This folder already contains other files. A library (documents/ + .pdfsf/ folders) "
                                  "will be added inside it; your existing files are left untouched and are not indexed.")
                ok = True
            else:
                self.info.setText(f"\u274C {st.detail or 'Folder cannot be used'}")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def _accept(self):
        self.path = Path(self.edit.text().strip())
        self.accept()


class LibraryProblemDialog(QDialog):
    """Shown when the active library cannot be opened. Nothing is created or reset automatically."""
    RETRY, LOCATE, CHANGE, QUIT = "retry", "locate", "change", "quit"

    def __init__(self, path: str, reason: str, parent=None, can_recover_db=False):
        super().__init__(parent)
        self.setWindowTitle("Library not available")
        self.choice = self.QUIT
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        t = QLabel(f"<b>Your document library cannot be opened.</b><br><br>Location:<br><code>{path}</code><br><br>{reason}"
                   "<br><br>Nothing has been changed or deleted. If the folder is on an external drive, reconnect it and press "
                   "<i>Retry</i>. Your configuration is kept until you decide what to do.")
        t.setWordWrap(True)
        lay.addWidget(t)
        row = QVBoxLayout()
        for text, ch in (("Retry (reconnect the drive / folder)", self.RETRY),
                         ("Locate the library in a different folder\u2026", self.LOCATE),
                         ("Choose another library or create a new one\u2026", self.CHANGE),
                         ("Quit", self.QUIT)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, c=ch: self._pick(c))
            row.addWidget(b)
        lay.addLayout(row)

    def _pick(self, c):
        self.choice = c
        self.accept()

    def reject(self):
        self.choice = self.QUIT
        super().reject()


class ChangeLocationDialog(QDialog):
    MOVE, SWITCH = "move", "switch"

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Change library location")
        self.setMinimumWidth(620)
        self.mode, self.path = self.MOVE, None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Current library: <b>{library.root}</b>"))
        self.r_move = QRadioButton("Option A \u2014 Move my existing library to a new folder")
        self.r_switch = QRadioButton("Option B \u2014 Switch to a different library (existing or new/empty folder)")
        self.r_move.setChecked(True)
        d1 = QLabel("All PDFs and the search index are copied to the new folder and verified. The old library stays untouched "
                    "until the move is verified; afterwards you decide whether to delete it. If anything fails, you keep using the "
                    "current library.")
        d2 = QLabel("The selected folder becomes the active library. Libraries are never merged. Your current library stays where it "
                    "is, and you can switch back to it at any time.")
        for d in (d1, d2):
            d.setWordWrap(True); d.setStyleSheet("color:#555;margin-left:22px;")
        for w in (self.r_move, d1, self.r_switch, d2):
            lay.addWidget(w)
        grp = QButtonGroup(self); grp.addButton(self.r_move); grp.addButton(self.r_switch)
        self.r_move.toggled.connect(self._check); self.r_switch.toggled.connect(self._check)
        row = QHBoxLayout()
        self.edit = QLineEdit(); self.edit.setPlaceholderText("New folder")
        self.edit.textChanged.connect(self._check)
        b = QPushButton("Browse\u2026"); b.clicked.connect(self._browse)
        row.addWidget(self.edit, 1); row.addWidget(b)
        lay.addLayout(row)
        self.msg = QLabel(""); self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept); self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        lay.addWidget(self.buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select folder", self.edit.text() or "")
        if d:
            self.edit.setText(d)

    def _check(self):
        t = self.edit.text().strip()
        mode = self.MOVE if self.r_move.isChecked() else self.SWITCH
        if not t:
            self.msg.setText(""); self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        chk = validate_target(self.library, t, mode)
        if chk.ok:
            extra = f" Required space about {format_size(chk.required_bytes)}, free {format_size(chk.free_bytes)}." if mode == self.MOVE else ""
            self.msg.setStyleSheet("color:#2e8b57;")
            self.msg.setText("\u2705 Folder is valid." + extra)
        else:
            self.msg.setStyleSheet("color:#c0392b;")
            self.msg.setText("\u274C " + "\n".join(chk.problems))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(chk.ok)

    def _accept(self):
        self.mode = self.MOVE if self.r_move.isChecked() else self.SWITCH
        self.path = Path(self.edit.text().strip())
        self.accept()
