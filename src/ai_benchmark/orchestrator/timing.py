"""Monotonic timing primitives used to measure trials."""

from __future__ import annotations

import time
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Stopwatch:
    """Measures elapsed time with a monotonic clock and keeps named checkpoints.

    Wall-clock timestamps are recorded separately for reporting; durations
    always come from ``time.monotonic`` so clock adjustments cannot skew them.
    """

    def __init__(self) -> None:
        self._start: float | None = None
        self._stop: float | None = None
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.marks: dict[str, float] = {}

    def start(self) -> None:
        if self._start is not None:
            raise RuntimeError("stopwatch already started")
        self.started_at = utcnow()
        self._start = time.monotonic()

    def mark(self, name: str) -> float:
        """Record the elapsed seconds at a named checkpoint (e.g. 'first_implementation')."""
        elapsed = self.elapsed
        self.marks[name] = elapsed
        return elapsed

    def stop(self) -> float:
        if self._start is None:
            raise RuntimeError("stopwatch not started")
        if self._stop is None:
            self._stop = time.monotonic()
            self.stopped_at = utcnow()
        return self.elapsed

    @property
    def elapsed(self) -> float:
        if self._start is None:
            raise RuntimeError("stopwatch not started")
        end = self._stop if self._stop is not None else time.monotonic()
        return end - self._start
