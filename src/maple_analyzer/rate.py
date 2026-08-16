"""Fixed-epoch session tracking: values accumulate from an explicit start
point until the session is finalized and a new one begins, rather than a
sliding window.

A rolling window (the original design) shrinks in a confusing way once it's
been running longer than the window: e.g. a big EXP gain 4 minutes ago ages
out of a 5-min window even though nothing changed just now, making the
displayed diff decrease with no corresponding in-game event. A session fixes
that -- the start values (EXP, HP, MP) are set once and held constant until
`finalize()` is called, so 'EXP diff' unambiguously means 'since the session
started', full stop.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SessionSummary:
    start_time: float
    end_time: float
    start_exp: int | None
    end_exp: int | None
    hp_loss: int
    mp_loss: int
    # EXP required for the whole current level, derived once during the
    # session from a tick that had both cur and pct (cur / (pct/100)) -- see
    # Session._total_exp. None if no such tick occurred during the session.
    total_exp: float | None

    @property
    def exp_diff(self) -> int | None:
        if self.start_exp is None or self.end_exp is None:
            return None
        return self.end_exp - self.start_exp

    @property
    def exp_pct_diff(self) -> float | None:
        diff = self.exp_diff
        if diff is None or not self.total_exp:
            return None
        return diff / self.total_exp * 100

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time


class Session:
    def __init__(self) -> None:
        self._start_time: float | None = None
        self._start_exp: int | None = None
        self._last_hp: int | None = None
        self._last_mp: int | None = None
        self._hp_loss = 0
        self._mp_loss = 0
        self._exp_cur: int | None = None
        self._hp_cur: int | None = None
        self._mp_cur: int | None = None
        self._total_exp: float | None = None

    def start(self, now: float | None = None) -> None:
        """Begin a new session. Carries forward whatever EXP/HP/MP values are
        already known as the new baseline (so a level-up-triggered or
        timer-triggered restart doesn't wait a tick to re-establish it)."""
        self._start_time = now if now is not None else time.time()
        self._start_exp = self._exp_cur
        self._last_hp = self._hp_cur
        self._last_mp = self._mp_cur
        self._hp_loss = 0
        self._mp_loss = 0
        self._total_exp = None  # re-derived fresh -- could differ after a level-up

    def record(
        self, exp_cur: int | None, hp_cur: int | None, mp_cur: int | None, exp_pct: float | None = None,
    ) -> None:
        if self._start_time is None:
            self.start()
        if self._start_exp is None and exp_cur is not None:
            self._start_exp = exp_cur
        if hp_cur is not None:
            if self._last_hp is not None:
                self._hp_loss += max(0, self._last_hp - hp_cur)
            self._last_hp = hp_cur
            self._hp_cur = hp_cur
        if mp_cur is not None:
            if self._last_mp is not None:
                self._mp_loss += max(0, self._last_mp - mp_cur)
            self._last_mp = mp_cur
            self._mp_cur = mp_cur
        if exp_cur is not None:
            self._exp_cur = exp_cur
        if exp_cur and exp_pct:
            self._total_exp = exp_cur / (exp_pct / 100)

    def elapsed(self, now: float | None = None) -> float:
        if self._start_time is None:
            return 0.0
        return (now if now is not None else time.time()) - self._start_time

    @property
    def start_exp(self) -> int | None:
        return self._start_exp

    @property
    def exp_diff(self) -> int | None:
        if self._start_exp is None or self._exp_cur is None:
            return None
        return self._exp_cur - self._start_exp

    @property
    def hp_loss(self) -> int:
        return self._hp_loss

    @property
    def mp_loss(self) -> int:
        return self._mp_loss

    @property
    def total_exp(self) -> float | None:
        return self._total_exp

    def finalize(self, now: float | None = None) -> SessionSummary:
        end_time = now if now is not None else time.time()
        return SessionSummary(
            start_time=self._start_time if self._start_time is not None else end_time,
            end_time=end_time,
            start_exp=self._start_exp,
            end_exp=self._exp_cur,
            hp_loss=self._hp_loss,
            mp_loss=self._mp_loss,
            total_exp=self._total_exp,
        )
