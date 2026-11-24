"""Application-level configuration (which library is active, machine-specific paths).

Per-library options (OCR language, recovery period, ...) live in the library's own
database so that every library keeps its own settings.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_NAME = "PDFSerialFinder"
APP_VERSION = "1.0.0"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "ocr_enabled": True,
    "ocr_language": "eng",
    "ocr_dpi": 300,
    "min_text_chars": 25,
    "missing_file_recovery_days": 0,   # 0 = a deleted file is forgotten at the next sync
    "preview_cache_max_mb": 500,
    "fuzzy_cutoff": 0.8,
}


def default_config_dir() -> Path:
    override = os.environ.get("PDFSF_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


class AppConfig:
    def __init__(self, config_dir: Optional[Path] = None):
        self.dir = Path(config_dir) if config_dir else default_config_dir()
        self.path = self.dir / "config.json"
        self.log_dir = self.dir / "logs"
        self.recovered_from_corrupt = False
        self.data: Dict[str, Any] = {
            "active_library_path": None,
            "known_libraries": [],
            "tesseract_cmd": "",
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("config is not an object")
            self.data.update(loaded)
        except (OSError, ValueError):
            # Keep the broken file for manual recovery; do NOT silently pick a new library.
            self.recovered_from_corrupt = True
            try:
                shutil.copy2(self.path, self.path.with_name(f"config.corrupt-{int(time.time())}.json"))
            except OSError:
                pass

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # -- library path -------------------------------------------------------
    @property
    def active_library_path(self) -> Optional[str]:
        return self.data.get("active_library_path")

    def set_active_library(self, path: Path) -> None:
        p = str(Path(path))
        self.data["active_library_path"] = p
        self.remember_library(p, save=False)
        self.save()

    def remember_library(self, path, save: bool = True) -> None:
        p = str(path)
        libs: List[str] = [x for x in self.data.get("known_libraries", []) if x != p]
        libs.insert(0, p)
        self.data["known_libraries"] = libs[:20]
        if save:
            self.save()

    @property
    def known_libraries(self) -> List[str]:
        return list(self.data.get("known_libraries", []))

    @property
    def tesseract_cmd(self) -> str:
        return self.data.get("tesseract_cmd", "") or ""

    def set_tesseract_cmd(self, cmd: str) -> None:
        self.data["tesseract_cmd"] = cmd
        self.save()
