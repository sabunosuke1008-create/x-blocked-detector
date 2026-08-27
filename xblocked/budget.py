from __future__ import annotations

import threading
import time
from typing import Optional


class PageBudget:
    """Thread-safe shared budget limiting total collection pages and wall time.

    max_pages == 0 means unlimited pages; seconds <= 0 means no time cap.
    try_take() returns False once either budget is exhausted.
    """

    def __init__(self, max_pages: int = 0, seconds: float = 0.0):
        self.max_pages = int(max_pages or 0)
        self.deadline: Optional[float] = (time.monotonic() + float(seconds)) if seconds and seconds > 0 else None
        self._mu = threading.Lock()
        self.used = 0
        self.stopped_by = ""

    def _time_left(self) -> bool:
        return self.deadline is None or time.monotonic() < self.deadline

    def try_take(self) -> bool:
        with self._mu:
            if self.max_pages > 0 and self.used >= self.max_pages:
                if not self.stopped_by:
                    self.stopped_by = f"page budget ({self.max_pages})"
                return False
            if not self._time_left():
                if not self.stopped_by:
                    self.stopped_by = "time budget"
                return False
            self.used += 1
            return True

    @property
    def exhausted(self) -> bool:
        with self._mu:
            return bool(self.stopped_by)

    def summary(self) -> str:
        with self._mu:
            base = f"pages used={self.used}"
            if self.stopped_by:
                return f"{base} (stopped by {self.stopped_by})"
            return base
