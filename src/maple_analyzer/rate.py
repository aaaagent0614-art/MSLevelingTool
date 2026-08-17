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


@dataclass(frozen=True)
class SessionSummary:
    """A finalized, immutable record of one completed session.

    Every field is a JSON/SQLite-primitive (float, int, or None) and there
    are no references to Session, StatSnapshot, or anything OCR/UI-related --
    this is deliberately the shape a future persistence layer would store and
    a future UI would read, decoupled from how it's produced or displayed.
    See ~/.claude/notes/maplestory-analyzer/ui-plan-2026-08-17.md for the
    plan to add that persistence layer and a session-history browser on top
    of this struct, without touching the capture/OCR/parser engine.
    """

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
    # The session-length *setting* in effect when this session ended -- not
    # necessarily equal to duration_s. They differ whenever a session is
    # manually restarted before the timer fires, or (once the settings UI
    # exists) the interval setting is changed between sessions -- recording
    # it here means a saved/displayed session stays self-describing even if
    # the live setting has since changed.
    interval_minutes: float | None
    # User-assigned label, e.g. "grinding spot A". None until renamed via the
    # History tab -- UI-layer concern only, the engine never sets this.
    name: str | None = None

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


class _LossTracker:
    """Accumulates the downward side of one stat's per-tick deltas, with two
    guards against OCR noise.

    Why guards are needed here specifically: HP/MP loss is a *running total*
    that only ever increases, so a single bad reading is baked in permanently
    -- unlike EXP, which is end-minus-start and self-corrects on the next
    tick. Observed live: an idle character (zero real MP spend) accumulated
    142,258 MP of "loss", ~50x the character's max MP, purely from misreads.

    Guard 1 -- max stability. `max` is constant except at level-up, so a tick
    reporting a different one was misparsed and is discarded whole. This
    catches the expensive modes, where a misread '/' shifts a digit across the
    divider: '1663/2816' -> '16632/816' reads *high*, costs nothing that tick,
    then books a 14,969 phantom loss when the next correct read "drops" back.
    A genuinely new max (level-up) is accepted once a second tick corroborates
    it, so this can't wedge the tracker permanently.

    Guard 2 -- outlier hold. A reading within OUTLIER_FRACTION of max from the
    last accepted value is normal play and is taken immediately (no lag). A
    reading further out than that has to be corroborated by a second reading
    near it before it becomes the new baseline; otherwise it is held aside and
    forgotten the moment a normal reading arrives.

    Two earlier designs failed here and are worth not repeating:

    - Guarding only *drops* leaks badly (48k of phantom loss in a 3-minute
      idle simulation), because a phantom *high* read poisons the baseline
      instantly and the true values then "drop" back to reality and
      corroborate each other perfectly. The guard must be symmetric.
    - A median-of-3 despike is symmetric but only survives *isolated* spikes.
      Under the alternating good/bad pattern real OCR produces
      (1663, 3, 1663, 16, ...) the median window itself is half garbage and
      the filter passes the noise straight through.

    Outlier hold survives both: the alternating case never corroborates, and
    a genuine large change (a one-shot, a full-heal) simply lands one tick
    late. Normal-sized changes aren't delayed at all.

    Cost: a real, large, single-tick dip that fully recovers before the next
    read isn't counted -- but at 2Hz such a dip was already invisible half the
    time. A *persistently* misread value is booked once, then becomes the
    baseline; bounded, unlike the old unbounded ratchet.
    """

    # Fraction of max MP/HP a single 500ms tick may move before the reading
    # is treated as suspect. Generous on purpose: this only decides what needs
    # corroboration, not what counts, so a real big hit still lands (one tick
    # late) while digit-truncation misreads -- which are always wrong by most
    # of the bar -- are the ones held.
    OUTLIER_FRACTION = 0.5

    def __init__(self) -> None:
        self.loss = 0
        self._last: int | None = None       # last accepted value
        self._max: int | None = None        # established max for this stat
        self._max_candidate: int | None = None
        self._candidate: int | None = None  # outlier awaiting corroboration

    def reset(self, last: int | None) -> None:
        """New session: zero the total, keep the established max (it survives
        session boundaries), baseline off the last known value."""
        self.loss = 0
        self._last = last
        self._candidate = None

    def record(self, cur: int | None, maximum: int | None = None) -> None:
        if cur is None:
            return
        if maximum is not None:
            if not self._accept_max(maximum):
                return  # misparsed tick -- don't let it near the loss math
            if cur > maximum:
                return  # cur can never exceed max; this reading is garbage
        if self._last is None:
            self._last = cur
            return
        tolerance = (self._max or self._last) * self.OUTLIER_FRACTION
        if abs(cur - self._last) <= tolerance:
            self._commit(cur)
        elif self._candidate is not None and abs(cur - self._candidate) <= tolerance:
            self._commit(cur)  # corroborated -- a real jump after all
        else:
            self._candidate = cur  # hold; a normal reading next tick discards it

    def _commit(self, cur: int) -> None:
        if cur < self._last:
            self.loss += self._last - cur
        self._last = cur
        self._candidate = None

    # A level-up nudges max HP/MP; nothing in the game multiplies it. A
    # proposed max outside this factor of the established one is garbage and
    # is never adopted, no matter how many ticks repeat it -- corroboration
    # alone is not enough, because when a window covers the panel the OCR
    # garbage is *static*: the identical wrong text every tick. That is how a
    # max of 281616 (from '281616' misread out of '2816') got installed in a
    # live capture, after which a bogus cur of 28163 passed every check and
    # booked 25,347 of phantom loss when the panel came back.
    MAX_CHANGE_FACTOR = 2.0

    def _accept_max(self, maximum: int) -> bool:
        if maximum <= 0:
            return False
        if self._max is None:
            self._max = maximum
            return True
        if maximum == self._max:
            self._max_candidate = None
            return True
        if not (self._max / self.MAX_CHANGE_FACTOR <= maximum <= self._max * self.MAX_CHANGE_FACTOR):
            return False  # implausible -- never adopt, however often it repeats
        if maximum == self._max_candidate:
            self._max = maximum  # corroborated twice -- a real level-up
            self._max_candidate = None
            return True
        self._max_candidate = maximum
        return False


class Session:
    def __init__(self) -> None:
        self._start_time: float | None = None
        self._start_exp: int | None = None
        self._hp = _LossTracker()
        self._mp = _LossTracker()
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
        self._hp.reset(self._hp_cur)
        self._mp.reset(self._mp_cur)
        self._total_exp = None  # re-derived fresh -- could differ after a level-up

    def record(
        self, exp_cur: int | None, hp_cur: int | None, mp_cur: int | None, exp_pct: float | None = None,
        hp_max: int | None = None, mp_max: int | None = None,
    ) -> None:
        """hp_max/mp_max are optional but strongly recommended: passing them
        enables _LossTracker's max-stability guard, which is what catches the
        high-magnitude OCR misreads (see that class's docstring)."""
        if self._start_time is None:
            self.start()
        if self._start_exp is None and exp_cur is not None:
            self._start_exp = exp_cur
        self._hp.record(hp_cur, hp_max)
        self._mp.record(mp_cur, mp_max)
        if hp_cur is not None:
            self._hp_cur = hp_cur
        if mp_cur is not None:
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
        return self._hp.loss

    @property
    def mp_loss(self) -> int:
        return self._mp.loss

    @property
    def total_exp(self) -> float | None:
        return self._total_exp

    def finalize(self, interval_minutes: float | None = None, now: float | None = None) -> SessionSummary:
        end_time = now if now is not None else time.time()
        return SessionSummary(
            start_time=self._start_time if self._start_time is not None else end_time,
            end_time=end_time,
            start_exp=self._start_exp,
            end_exp=self._exp_cur,
            hp_loss=self._hp.loss,
            mp_loss=self._mp.loss,
            total_exp=self._total_exp,
            interval_minutes=interval_minutes,
        )
