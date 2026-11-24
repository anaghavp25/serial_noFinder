import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT = "pdfsf"


def setup_logging(log_dir: Path, level=logging.INFO) -> logging.Logger:
    """Configure local file logging (no network handlers, ever)."""
    logger = logging.getLogger(_ROOT)
    if getattr(logger, "_pdfsf_configured", False):
        return logger
    logger.setLevel(level)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(sh)
    logger._pdfsf_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_ROOT}.{name}")
