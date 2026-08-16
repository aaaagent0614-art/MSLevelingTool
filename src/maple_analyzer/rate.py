"""Time-windowed diff/rate tracking, logged as (timestamp, value) samples so it's
robust to variable OCR frequency (we only log on change) and idle periods.

Two different trackers because EXP and HP/MP behave differently:

- EXP only goes up during normal play. A drop means a level-up (EXP resets to 0
  for the new level), so `ExpRateTracker` treats a drop as a window reset and
  reports an hourly-extrapolated rate (EXP/hr), which is the meaningful unit for
  "how fast am I grinding."
- HP/MP go up *and* down constantly (damage, healing, regen, skill costs) --
  a drop is normal, not a reset condition. An hourly extrapolation of "current
  HP" wouldn't mean anything either. What's useful is total movement over a
  window: `DiffTracker.window_diff()` sums each consecutive tick's delta within
  the window (net damage taken minus healing received, e.g. -450 over the last
  minute), which is mathematically the same as last-minus-first in the window
  but computed the way the analysis is actually framed: per-tick diffs, summed.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class Sample:
    ts: float
    value: int


class _WindowedLog:
    def __init__(self, windows_minutes: tuple[int, ...], reset_on_drop: bool):
        self._history: deque[Sample] = deque()
        self._windows = windows_minutes
        self._reset_on_drop = reset_on_drop

    def record(self, value: int | None) -> None:
        if value is None:
            return
        now = time.time()
        if self._reset_on_drop and self._history and value < self._history[-1].value:
            self._history.clear()
        if not self._history or value != self._history[-1].value:
            self._history.append(Sample(now, value))
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - max(self._windows) * 60
        while self._history and self._history[0].ts < cutoff:
            self._history.popleft()

    def _window_samples(self, window_minutes: int) -> list[Sample]:
        cutoff = time.time() - window_minutes * 60
        return [s for s in self._history if s.ts >= cutoff]

    def is_idle(self, idle_after_minutes: float = 5.0) -> bool:
        if not self._history:
            return True
        return (time.time() - self._history[-1].ts) > idle_after_minutes * 60


class ExpRateTracker(_WindowedLog):
    def __init__(self, windows_minutes: tuple[int, ...] = (1, 10, 60), idle_after_minutes: float = 5.0):
        super().__init__(windows_minutes, reset_on_drop=True)
        self._idle_after_minutes = idle_after_minutes

    def record(self, exp_cur: int | None) -> None:
        super().record(exp_cur)

    def is_idle(self) -> bool:
        return super().is_idle(self._idle_after_minutes)

    def rate_per_hour(self, window_minutes: int) -> float | None:
        samples = self._window_samples(window_minutes)
        if len(samples) < 2:
            return None
        elapsed = samples[-1].ts - samples[0].ts
        if elapsed <= 0:
            return None
        delta = samples[-1].value - samples[0].value
        return delta / elapsed * 3600

    def rates(self) -> dict[int, float | None]:
        return {w: self.rate_per_hour(w) for w in self._windows}


class DiffTracker(_WindowedLog):
    def __init__(self, windows_minutes: tuple[int, ...] = (1, 10, 60)):
        super().__init__(windows_minutes, reset_on_drop=False)

    def window_diff(self, window_minutes: int) -> int | None:
        samples = self._window_samples(window_minutes)
        if len(samples) < 2:
            return None
        return sum(b.value - a.value for a, b in zip(samples, samples[1:]))

    def diffs(self) -> dict[int, int | None]:
        return {w: self.window_diff(w) for w in self._windows}
