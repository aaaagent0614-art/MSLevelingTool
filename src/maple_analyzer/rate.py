"""Time-windowed diff/loss tracking, logged as (timestamp, value) samples so it's
robust to variable OCR frequency (we only log on change) and idle periods.

Two different trackers because EXP and HP/MP behave differently:

- EXP only goes up during normal play. A drop means a level-up (EXP resets to 0
  for the new level), so `DiffTracker(reset_on_drop=True)` treats a drop as a
  window reset. Reports the plain window diff (value now minus value at the
  start of the window) -- e.g. "EXP gained in the last 1 min" -- no hourly
  extrapolation.
- HP/MP go up *and* down constantly (damage, healing, regen, skill costs). A
  plain diff would net damage against healing and hide how much was actually
  lost. `LossTracker` only accumulates the negative side of each per-tick
  delta -- total HP/MP lost to damage/spend within the window, ignoring gains.
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


class DiffTracker(_WindowedLog):
    def __init__(self, windows_minutes: tuple[int, ...] = (1, 10, 60), reset_on_drop: bool = False):
        super().__init__(windows_minutes, reset_on_drop=reset_on_drop)

    def window_diff(self, window_minutes: int) -> int | None:
        samples = self._window_samples(window_minutes)
        if len(samples) < 2:
            return None
        return samples[-1].value - samples[0].value


class LossTracker(_WindowedLog):
    def __init__(self, windows_minutes: tuple[int, ...] = (1, 10, 60)):
        super().__init__(windows_minutes, reset_on_drop=False)

    def window_loss(self, window_minutes: int) -> int | None:
        samples = self._window_samples(window_minutes)
        if len(samples) < 2:
            return None
        return sum(max(0, a.value - b.value) for a, b in zip(samples, samples[1:]))
