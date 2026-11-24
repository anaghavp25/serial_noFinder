"""Printing through the system print dialog (native on Windows)."""
from typing import List, Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPageRanges, QPainter, QTransform
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from utils.fitz_compat import fitz


def print_pages(printer: QPrinter, pdf_path, pages: List[int], copies: int = 1) -> None:
    """Render the given 1-based pages and send them to `printer` (a real printer or a PDF-file printer)."""
    doc = fitz.open(str(pdf_path))
    painter = QPainter()
    try:
        if not painter.begin(printer):
            raise RuntimeError("Could not start the print job. Check that the printer is available.")
        first = True
        for _ in range(max(1, copies)):
            for pno in pages:
                if not first:
                    printer.newPage()
                first = False
                page = doc.load_page(pno - 1)
                dpi = min(max(printer.resolution(), 150), 300)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                area = painter.viewport()
                if (img.width() > img.height()) != (area.width() > area.height()):
                    img = img.transformed(QTransform().rotate(90))      # landscape page on portrait paper (or vice versa)
                size = img.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRect(area.x() + (area.width() - size.width()) // 2,
                               area.y() + (area.height() - size.height()) // 2, size.width(), size.height())
                painter.drawImage(target, img)
    finally:
        if painter.isActive():
            painter.end()
        doc.close()


def selected_pages(printer: QPrinter, page_count: int, current: int) -> List[int]:
    r = printer.printRange()
    if r == QPrinter.PrintRange.PageRange:
        ranges = printer.pageRanges().toRangeList()
        if ranges:
            return [page for page_range in ranges
                    for page in range(max(1, page_range.from_), min(page_count, page_range.to) + 1)]
        a = max(1, printer.fromPage() or 1)
        b = min(page_count, printer.toPage() or page_count)
        return list(range(a, max(a, b) + 1))
    if r in (QPrinter.PrintRange.CurrentPage, QPrinter.PrintRange.Selection):
        return [current]
    return list(range(1, page_count + 1))


def print_document(parent, pdf_path, page_count: int, current_page: int, title: str,
                   initial_pages: Optional[List[int]] = None) -> bool:
    """Show the system print dialog (printer, pages, copies, printer settings) and submit the job."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setDocName(title)
    if initial_pages is not None:
        pages = sorted({p for p in initial_pages if 1 <= p <= page_count})
        if not pages:
            raise ValueError("At least one valid page must be selected for printing.")
        page_ranges = QPageRanges()
        for page in pages:
            page_ranges.addPage(page)
        printer.setPageRanges(page_ranges)
        printer.setPrintRange(QPrinter.PrintRange.PageRange)
    dlg = QPrintDialog(printer, parent)
    dlg.setWindowTitle(f"Print \u2013 {title}")
    dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
    dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage, True)
    dlg.setMinMax(1, page_count)
    if initial_pages is None:
        dlg.setFromTo(current_page, current_page)
        dlg.setPrintRange(QAbstractPrintDialog.PrintRange.CurrentPage)   # default: the page being looked at
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    pages = selected_pages(printer, page_count, current_page)
    copies = 1 if printer.supportsMultipleCopies() else max(1, printer.copyCount())
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        print_pages(printer, pdf_path, pages, copies)
    except Exception as e:
        QApplication.restoreOverrideCursor()
        QMessageBox.warning(parent, "Printing failed", str(e))
        return False
    QApplication.restoreOverrideCursor()
    return True
