import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog
from config.app_config import AppConfig
from ui.app_context import AppContext
from ui.dialogs.library_dialogs import LibrarySetupDialog
from ui.main_window import MainWindow
import ui.main_window as mw


def test_first_run_choose_library_then_search_enabled(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    target = tmp_path / "MyLibrary"

    def fake_exec(self):               # simulate: user types a path and presses OK
        self.edit.setText(str(target))
        self._accept()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(LibrarySetupDialog, "exec", fake_exec)
    ctx = AppContext(AppConfig(tmp_path / "cfg"))
    win = MainWindow(ctx)
    win.show()
    import time
    t0 = time.time()
    while time.time() - t0 < 10 and not win.ready:
        app.processEvents(); time.sleep(0.02)
    assert win.ready and ctx.library is not None
    assert ctx.config.active_library_path == str(target)
    assert win.search_page.input.isEnabled()
    t0 = time.time()
    while time.time() - t0 < 10 and win.worker is not None:
        app.processEvents(); time.sleep(0.02)
    win.close()
