import hashlib
import threading
from pathlib import Path
from typing import Optional


class HashCancelled(Exception):
    pass


def sha256_file(path, cancel: Optional[threading.Event] = None, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise HashCancelled()
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
