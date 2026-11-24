"""PDF Serial Finder - search a folder of certificate PDFs by serial number. Fully offline."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from config.app_config import APP_NAME, AppConfig
from ui.app_context import AppContext
from ui.main_window import MainWindow
from ui.theme import BASE_PT, STYLE
from utils.logging_utils import setup_logging


def main() -> int:
    config = AppConfig()
    log = setup_logging(config.log_dir)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    font = QFont("Segoe UI" if sys.platform.startswith("win") else app.font().family())
    font.setPointSize(BASE_PT)
    app.setFont(font)
    app.setStyleSheet(STYLE)
    ctx = AppContext(config)
    win = MainWindow(ctx)
    win.show()
    log.info("%s started", APP_NAME)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
