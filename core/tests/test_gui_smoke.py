"""Headless GUI smoke test (offscreen Qt): first library, import, search, preview, library page, sync."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config.app_config import AppConfig  # noqa: E402
from core.library_relocator import switch_library  # noqa: E402
from tests.conftest import make_pdf  # noqa: E402
from ui.app_context import AppContext  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def wait_for(app, cond, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_gui_end_to_end(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    cfg = AppConfig(tmp_path / "cfg")
    lib = switch_library(tmp_path / "lib")
    cfg.set_active_library(lib.root)
    ctx = AppContext(cfg)
    win = MainWindow(ctx)
    win.show()
    assert wait_for(app, lambda: win.ready and win.worker is None)

    src = tmp_path / "src"; src.mkdir()
    pdf = make_pdf(src / "cert.pdf", ["First page", "Certificate serial GUI-4242 here", "tail"])
    win.import_page.start([str(pdf)])
    assert wait_for(app, lambda: win.worker is None and win.import_page.table.rowCount() == 1)
    assert win.import_page.table.item(0, 0).text() == "Imported"

    sp = win.search_page
    sp.input.setText("gui-4242")
    sp.run_search()
    assert wait_for(app, lambda: "document(s)" in sp.status.text())
    assert "1 document(s), 1 matching page(s)" in sp.status.text()
    from ui.results_widget import ResultCard
    cards = sp.results.findChildren(ResultCard)
    assert len(cards) == 1 and cards[0].cur.page_number == 2
    cards[0].preview_requested.emit(cards[0].res, 2)
    assert wait_for(app, lambda: sp.preview.canvas.pixmap() is not None and not sp.preview.canvas.pixmap().isNull())
    assert "MATCHING PAGE" in sp.preview.sub.text()
    assert wait_for(app, lambda: cards[0].t_first.pixmap() is not None and not cards[0].t_first.pixmap().isNull())
    assert wait_for(app, lambda: not cards[0].t_match.pixmap().isNull())

    sp.input.setText("GUI-424")          # partial must not match
    sp.run_search()
    assert wait_for(app, lambda: "0 document(s)" in sp.status.text())

    win.library_page.refresh()
    assert win.library_page.table.rowCount() == 1

    (lib.docs_dir / "cert.pdf").unlink()
    win.start_sync()
    assert wait_for(app, lambda: win.worker is None)
    win.library_page.combo.setCurrentIndex(6)   # Missing
    win.library_page.refresh()
    assert win.library_page.table.rowCount() == 1
    win.settings_page.refresh()
    win.close()
