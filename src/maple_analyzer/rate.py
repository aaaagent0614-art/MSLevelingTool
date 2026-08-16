"""Time-windowed rate tracking for EXP (and, later, HP/MP drain).

Per spec: rate = (value_now - value_at(now - window)) / window_minutes, computed
from a log of (timestamp, value) samples rather than a fixed tick/sample count --
robust to variable OCR frequency (we only log on change) and idle periods.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class Sample:
    ts: float
    value: int


class ExpRateTracker:
    def __init__(self, windows_minutes: tuple[int, ...] = (1, 10, 60), idle_after_minutes: float = 5.0):
        self._history: deque[Sample] = deque()
        self._windows = windows_minutes
        self._idle_after = idle_after_minutes * 60
        self._level_offset = 0  # accumulated exp "lost" to level-up resets, v1 handling

    def record(self, exp_cur: int | None, level: int | None = None) -> None:
        if exp_cur is None:
            return
        now = time.time()
        if self._history and exp_cur < self._history[-1].value:
            # EXP dropped -- most likely a level-up reset (per spec v1: treat as a
            # window reset rather than trying to track cumulative EXP across levels).
            self._history.clear()
        if not self._history or exp_cur != self._history[-1].value:
            self._history.append(Sample(now, exp_cur))
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - max(self._windows) * 60
        while self._history and self._history[0].ts < cutoff:
            self._history.popleft()

    def is_idle(self) -> bool:
        if not self._history:
            return True
        return (time.time() - self._history[-1].ts) > self._idle_after

    def rate_per_hour(self, window_minutes: int) -> float | None:
        if len(self._history) < 2:
            return None
        now = time.time()
        cutoff = now - window_minutes * 60
        window_samples = [s for s in self._history if s.ts >= cutoff]
        if len(window_samples) < 2:
            return None
        elapsed = window_samples[-1].ts - window_samples[0].ts
        if elapsed <= 0:
            return None
        delta = window_samples[-1].value - window_samples[0].value
        return delta / elapsed * 3600

    def rates(self) -> dict[int, float | None]:
        return {w: self.rate_per_hour(w) for w in self._windows}
