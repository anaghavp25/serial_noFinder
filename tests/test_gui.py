"""Headless GUI tests (Qt offscreen): first run, automatic loading, search, view, print, delete, change directory."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox  # noqa: E402

from config.app_config import AppConfig  # noqa: E402
from tests.conftest import make_pdf  # noqa: E402
from ui.app_context import AppContext  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.search_page import ResultCard  # noqa: E402
from ui.theme import STYLE  # noqa: E402


def wait(app, cond, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def no_hit(page):
    return any("No document contains" in l.text() for l in page.findChildren(QLabel))


def start(tmp_path, monkeypatch, folder):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(folder)))
    win = MainWindow(AppContext(AppConfig(tmp_path / "cfg")))
    win.show()
    assert wait(app, lambda: win.ready and win.worker is None)
    return app, win


def test_first_launch_loads_folder_then_search_view_print_delete(tmp_path, monkeypatch):
    folder = tmp_path / "My certificates"; folder.mkdir()
    make_pdf(folder / "Alpha cert.pdf", ["Front page", "Serial GUI-4242 here", "tail"])
    make_pdf(folder / "Beta.pdf", ["Other GUI-9999"])
    app, win = start(tmp_path, monkeypatch, folder)

    # directory chosen once and persisted; PDFs loaded without any import step
    assert win.ctx.config.active_library_path == str(folder)
    win.goto(1)
    assert wait(app, lambda: win.docs_page.table.rowCount() == 2)
    assert win.search_page.btn.isEnabled() and win.search_page.input.isEnabled()

    # search by pressing Enter
    sp = win.search_page
    win.goto(0)
    sp.input.setText("gui-4242"); sp.input.returnPressed.emit()
    assert wait(app, lambda: sp.findChildren(ResultCard))
    cards = sp.findChildren(ResultCard)
    assert len(cards) == 1 and cards[0].match.page_number == 2
    assert wait(app, lambda: cards[0].thumb.pixmap() is not None and not cards[0].thumb.pixmap().isNull())

    # view page, navigate, back to results
    cards[0].view_page()
    assert win.stack.currentIndex() == 2 and win.viewer.page == 2
    assert wait(app, lambda: not win.viewer.canvas.pixmap().isNull())
    win.viewer.goto(3); assert win.viewer.page == 3
    win.viewer.zoom_by(1.25); win.viewer.back.emit()
    assert win.stack.currentIndex() == 0

    # partial serial finds nothing
    sp.input.setText("GUI-424"); sp.run_search()
    assert wait(app, lambda: no_hit(sp))

    # delete from Documents: file removed from the folder and from search
    win.goto(1)
    win.docs_page.table.selectRow(0)       # "Alpha cert.pdf"
    class FakeBox(QMessageBox):
        def exec(self): self._yes = [b for b in self.buttons() if b.text() == "Delete"][0]; return 0
        def clickedButton(self): return self._yes
    monkeypatch.setattr("ui.documents_page.QMessageBox", FakeBox)
    win.docs_page.delete_selected()
    assert not (folder / "Alpha cert.pdf").exists() and (folder / "Beta.pdf").exists()
    assert wait(app, lambda: win.docs_page.table.rowCount() == 1 and win.worker is None)
    win.close()


def test_reopen_uses_saved_directory_without_asking(tmp_path, monkeypatch):
    folder = tmp_path / "certs"; folder.mkdir(); make_pdf(folder / "a.pdf", ["Serial R-1"])
    app, win = start(tmp_path, monkeypatch, folder); win.close()
    calls = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: calls.append(1) or ""))
    win2 = MainWindow(AppContext(AppConfig(tmp_path / "cfg"))); win2.show()
    assert wait(app, lambda: win2.ready and win2.worker is None)
    assert not calls and win2.ctx.library.root == folder
    win2.close()


def test_change_directory_loads_new_folder_and_keeps_old_files(tmp_path, monkeypatch):
    f1 = tmp_path / "one"; f1.mkdir(); make_pdf(f1 / "a.pdf", ["Serial ONE-1"])
    f2 = tmp_path / "two"; f2.mkdir(); make_pdf(f2 / "b.pdf", ["Serial TWO-2"])
    app, win = start(tmp_path, monkeypatch, f1)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(f2)))
    win.b_change.click()
    assert wait(app, lambda: win.worker is None and win.docs_page.table.rowCount() == 1)
    assert win.docs_page.table.item(0, 0).text() == "b.pdf" and win.ctx.config.active_library_path == str(f2)
    assert (f1 / "a.pdf").exists()
    win.search_page.input.setText("ONE-1"); win.search_page.run_search()
    assert wait(app, lambda: no_hit(win.search_page))
    win.close()


def test_print_pages_reaches_the_printer_for_single_multiple_and_all_pages(tmp_path):
    from PySide6.QtPrintSupport import QPrinter
    from ui.printing import print_pages, selected_pages
    from utils.fitz_compat import fitz
    app = QApplication.instance() or QApplication([])
    pdf = make_pdf(tmp_path / "p.pdf", ["one", "two", "three", "four"])

    def run(pages, copies=1):
        out = tmp_path / f"out{len(pages)}x{copies}.pdf"
        pr = QPrinter(QPrinter.PrinterMode.HighResolution)
        pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat); pr.setOutputFileName(str(out))
        print_pages(pr, pdf, pages, copies)
        d = fitz.open(str(out)); n, text = d.page_count, " ".join(p.get_text() for p in d); d.close()
        return n, text
    assert run([2])[0] == 1
    assert run([1, 3, 4])[0] == 3
    assert run([1, 2, 3, 4])[0] == 4
    assert run([2], copies=2)[0] == 2
    pr = QPrinter(); pr.setPrintRange(QPrinter.PrintRange.PageRange); pr.setFromTo(2, 3)
    assert selected_pages(pr, 4, 1) == [2, 3]
    pr.setPrintRange(QPrinter.PrintRange.AllPages); assert selected_pages(pr, 4, 1) == [1, 2, 3, 4]
    pr.setPrintRange(QPrinter.PrintRange.CurrentPage); assert selected_pages(pr, 4, 3) == [3]


def test_print_button_opens_system_dialog_and_submits_job(tmp_path, monkeypatch):
    """Print button -> QPrintDialog -> job rendered to the chosen printer (dialog accepted by the test)."""
    from PySide6.QtPrintSupport import QPrintDialog, QPrinter
    from PySide6.QtWidgets import QDialog
    from utils.fitz_compat import fitz
    app = QApplication.instance() or QApplication([])
    pdf = make_pdf(tmp_path / "p.pdf", ["one", "two", "three"])
    out = tmp_path / "job.pdf"
    seen = {}

    def fake_exec(self):
        pr = self.printer()
        pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat); pr.setOutputFileName(str(out))
        pr.setPrintRange(QPrinter.PrintRange.PageRange); pr.setFromTo(2, 3)
        seen["opened"] = True
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QPrintDialog, "exec", fake_exec)
    from ui.pdf_viewer import PdfViewer
    v = PdfViewer(); v.open(pdf, "p.pdf", 3, 1)
    v.b_print.click()
    assert seen.get("opened") and fitz.open(str(out)).page_count == 2
