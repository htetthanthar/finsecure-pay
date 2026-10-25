"""Thread-safe, time-bounded store of recently approved transaction IDs.

Used to decline duplicate submissions (double charging) within a window.

Limitation (documented in the design doc): the store is per-pod memory, so
with several replicas a duplicate routed to a different pod is not caught.
Production would back this with Redis/ElastiCache using SET NX with a TTL.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class DuplicateStore:
    def __init__(
        self,
        window_seconds: int,
        max_entries: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._max = max_entries
        self._clock = clock
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        while self._seen:
            key, seen_at = next(iter(self._seen.items()))
            if now - seen_at < self._window and len(self._seen) <= self._max:
                break
            self._seen.pop(key)

    def seen_recently(self, key: str) -> bool:
        if self._window <= 0:
            return False
        with self._lock:
            now = self._clock()
            self._purge(now)
            return key in self._seen

    def record(self, key: str) -> None:
        if self._window <= 0:
            return
        with self._lock:
            now = self._clock()
            self._seen[key] = now
            self._seen.move_to_end(key)
            self._purge(now)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)
