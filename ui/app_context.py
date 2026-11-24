from typing import Optional

from PySide6.QtCore import QObject, Signal

from config.app_config import AppConfig
from core.library_manager import Library, LibraryUnavailable
from database.db_manager import DatabaseCorruptError


class AppContext(QObject):
    data_changed = Signal()

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.library: Optional[Library] = None
        self.problem = ""

    def open_active(self) -> str:
        """none | ok | unavailable | corrupt. Never creates or switches a library by itself."""
        path = self.config.active_library_path
        self.library, self.problem = None, ""
        if not path:
            return "none"
        return self.open_path(path)

    def open_path(self, path) -> str:
        try:
            self.library = Library.open(path)
            return "ok"
        except LibraryUnavailable as e:
            self.problem = str(e)
            return "unavailable"
        except DatabaseCorruptError as e:
            self.problem = str(e)
            return "corrupt"

    def activate(self, library: Library) -> None:
        self.library = library
        self.config.set_active_library(library.root)
