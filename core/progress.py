import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Progress:
    stage: str = ""
    done: int = 0
    total: int = 0
    current: str = ""
    success: int = 0
    failed: int = 0
    skipped: int = 0
    message: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.done)


ProgressCB = Optional[Callable[[Progress], None]]


class Cancelled(Exception):
    pass


def check_cancel(cancel: Optional[threading.Event]) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()
