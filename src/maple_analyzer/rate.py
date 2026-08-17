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
    # EXP actually gained over the session, accumulated tick by tick so it
    # stays correct across level-ups -- end_exp - start_exp is wrong the moment
    # a session spans one, since the game's counter resets to ~0. None only for
    # summaries built before this existed, which fall back to the subtraction.
    exp_gained: int | None = None
    # User-assigned label, e.g. "grinding spot A". None until renamed via the
    # History tab -- UI-layer concern only, the engine never sets this.
    name: str | None = None

    @property
    def exp_diff(self) -> int | None:
        if self.exp_gained is not None:
            return self.exp_gained
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
        # EXP is measured per *level segment* -- see _record_exp for why it is
        # deliberately not a tick-by-tick accumulator.
        self._banked = 0                      # gain from levels completed this session
        self._segment_start: int | None = None  # EXP at the start of the current level
        self._last_exp: int | None = None
        self._last_level: int | None = None
        self._last_implied_total: float | None = None  # see _exp_reading_is_trusted

    def start(self, now: float | None = None) -> None:
        """Begin a new session. Carries forward whatever EXP/HP/MP values are
        already known as the new baseline (so a level-up-triggered or
        timer-triggered restart doesn't wait a tick to re-establish it)."""
        self._start_time = now if now is not None else time.time()
        self._start_exp = self._exp_cur
        self._hp.reset(self._hp_cur)
        self._mp.reset(self._mp_cur)
        self._total_exp = None  # re-derived fresh -- could differ after a level-up
        self._banked = 0
        self._segment_start = self._exp_cur
        self._last_exp = self._exp_cur

    def record(
        self, exp_cur: int | None, hp_cur: int | None, mp_cur: int | None, exp_pct: float | None = None,
        hp_max: int | None = None, mp_max: int | None = None, level: int | None = None,
    ) -> None:
        """hp_max/mp_max/level are optional but strongly recommended: the maxes
        enable _LossTracker's max-stability guard (which catches the
        high-magnitude OCR misreads), and the level is what tells a level-up
        apart from a misread when EXP drops -- see _record_exp."""
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
        self._record_exp(exp_cur, exp_pct, level)

    # A reading may deviate this far from the level total established by the
    # previous tick before it is treated as garbage. Deliberately loose: it is
    # meant to catch order-of-magnitude nonsense, not to police OCR jitter.
    EXP_TOTAL_BAND = 0.25

    def _exp_reading_is_trusted(self, exp_cur: int, exp_pct: float | None, level: int | None) -> bool:
        """Cross-check `cur` against `pct`.

        They are two independent OCR readings of the same quantity, so their
        ratio -- the level's total EXP, constant within a level -- validates
        them against each other. 'EXP S255[1 12%]' (a 5 read as an S) implies a
        total of 22,768 where every good reading agrees on ~468,500.

        Compared against the *previous accepted tick*, never against a total
        learned from the data: in the live capture the bad value was the
        majority (379 of 429 ticks), so anything that learned would have
        learned 22,768 and rejected every correct reading afterwards.

        This mainly protects finalize(). Segments (see _record_exp) already
        make a bad frame transient, but a summary freezes one instant, and a
        garbage frame captured there is written to History permanently.
        """
        if level is not None and level != self._last_level:
            self._last_implied_total = None  # new level, new total: re-baseline
        if exp_pct:
            implied = exp_cur / (exp_pct / 100)
            if self._last_implied_total:
                # pct is rounded to 2dp, so at small pct the implied total is
                # numerically unstable -- half a least-significant digit is
                # 0.005/pct in relative terms, i.e. +-50% at pct=0.01. A fixed
                # band would reject every legitimate reading after a level-up.
                band = self.EXP_TOTAL_BAND + (0.005 / exp_pct)
                if abs(implied / self._last_implied_total - 1) > band:
                    return False
            self._last_implied_total = implied
            return True
        # No percentage to check against: fall back to the one bound that needs
        # no cross-reference -- a single 500ms tick cannot gain a whole level.
        if self._total_exp and self._last_exp is not None:
            if abs(exp_cur - self._last_exp) > self._total_exp:
                return False
        return True

    def _record_exp(self, exp_cur: int | None, exp_pct: float | None, level: int | None) -> None:
        """Track EXP gained, per level *segment*.

        Within a level this is plain end-minus-start, which is the important
        property: it depends only on the current reading, so a garbage frame
        shows a wrong number for one tick and then self-corrects. Only a
        level-up banks a segment and opens a new one.

        It is deliberately NOT a tick-by-tick accumulator. That was tried
        (2026-08-19) to handle level-ups and it regressed badly: summing every
        rise means one absurd reading is baked in forever, exactly the ratchet
        that makes HP/MP loss fragile. A single garbage frame -- 'EXP101332182',
        no brackets, no percentage -- booked +101,322,049 of phantom gain in
        one tick and it never came back. end-minus-start showed +16,058 over
        the same run.

        HP/MP cannot be done this way and must accumulate: loss is a path
        integral, not a difference. A character who takes 5,000 damage and
        potions back to full has the same endpoints as one who stood still.
        That is why the guards in _LossTracker exist there and are not needed
        here.
        """
        if exp_cur is None:
            return
        if not self._exp_reading_is_trusted(exp_cur, exp_pct, level):
            return  # garbage -- treat it as an unreadable field and carry forward
        # Captured before the update below: _total_exp is re-derived every tick
        # from cur/pct, so by the time we see the reset it already describes
        # the *new* level. The level just finished has to be measured with the
        # pre-update value, or every level-up under-counts by a level.
        previous_total = self._total_exp

        if self._segment_start is None:
            self._segment_start = exp_cur

        levelled = (
            level is not None
            and self._last_level is not None
            and level > self._last_level
            and self._last_exp is not None
            and exp_cur < self._last_exp
        )
        if levelled:
            # Bank the level just finished, then start the new segment at 0 --
            # whatever is already banked into the new level counts as gain.
            # Requiring *both* a level increase and an EXP reset keeps a
            # one-off level misread from banking a phantom segment.
            if previous_total:
                self._banked += max(0, int(previous_total - self._segment_start))
            # Without a percentage reading the finished level's total is
            # unknown, so its remainder is dropped rather than invented: an
            # under-count, never a fabricated number.
            self._segment_start = 0

        self._last_exp = exp_cur
        self._exp_cur = exp_cur
        if level is not None:
            self._last_level = level
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
        """EXP gained since the session started, spanning level-ups (see
        _record_exp). Clamped at 0: within a level EXP only rises, so a
        negative here means a misread, and showing 0 for a tick beats
        rendering a negative behind the '+' the HUD prints."""
        if self._start_exp is None or self._exp_cur is None or self._segment_start is None:
            return None
        return max(0, self._banked + (self._exp_cur - self._segment_start))

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
            exp_gained=self.exp_diff,
        )
