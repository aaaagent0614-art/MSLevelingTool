"""End-to-end regression against real captured OCR, not synthetic values.

The unit tests in test_rate.py pin each guard against hand-written inputs.
This one replays 95 verbatim frames from the live failure (see
captured_frames.py) through the same path overlay.py drives -- parse, carry
forward missing fields, feed Session -- and asserts the phantom loss is gone.

Kept separate so it stays honest: if a future refactor makes the unit tests
pass by construction, this one still fails, because its input is a recording
of the bug rather than a description of it.
"""
from __future__ import annotations

from maple_analyzer.parser import StatSnapshot, parse_fields
from maple_analyzer.rate import Session

from captured_frames import COVERED_PANEL_FRAMES


def _merged_stream(frames):
    """Reproduces overlay._do_tick's carry-forward: a field that fails to OCR
    on one tick keeps its last known value rather than flashing blank."""
    last = StatSnapshot(None, None, None, None, None, None, None)
    for frame in frames:
        snap = parse_fields(frame)
        merged = StatSnapshot(*(
            new if new is not None else old
            for new, old in zip(vars(snap).values(), vars(last).values())
        ))
        last = merged
        yield snap, merged


def _unguarded_mp_loss(frames) -> int:
    """What shipped in v1.0: sum every downward delta, no questions asked."""
    loss = 0
    previous = None
    for _snap, merged in _merged_stream(frames):
        if merged.mp_cur is None:
            continue
        if previous is not None and merged.mp_cur < previous:
            loss += previous - merged.mp_cur
        previous = merged.mp_cur
    return loss


def _guarded_session(frames) -> Session:
    session = Session(require_calibration=False)
    session.start()
    for _snap, merged in _merged_stream(frames):
        session.record(
            merged.exp_cur, merged.hp_cur, merged.mp_cur, merged.exp_pct,
            hp_max=merged.hp_max, mp_max=merged.mp_max,
        )
    return session


def test_the_recording_still_reproduces_the_bug():
    """Guards the fixture itself. If this stops failing-by-old-logic, the
    recording has been damaged and the test below proves nothing."""
    assert _unguarded_mp_loss(COVERED_PANEL_FRAMES) > 100_000


def test_captured_failure_no_longer_inflates_mp_loss():
    session = _guarded_session(COVERED_PANEL_FRAMES)
    # Real MP movement across these two ~30s windows is small; the character
    # was near full MP for much of it. The old logic booked 136,491 here.
    assert session.mp_loss < 3_000


def test_no_single_tick_books_a_large_phantom_loss():
    """The failure was concentrated: two frames accounted for almost all of
    it (+114,486 and +21,931). No individual tick should move the total by
    anything like a full MP bar again."""
    session = Session(require_calibration=False)
    session.start()
    previous = 0
    jumps = []
    for _snap, merged in _merged_stream(COVERED_PANEL_FRAMES):
        session.record(
            merged.exp_cur, merged.hp_cur, merged.mp_cur, merged.exp_pct,
            hp_max=merged.hp_max, mp_max=merged.mp_max,
        )
        if session.mp_loss - previous > 500:
            jumps.append(session.mp_loss - previous)
        previous = session.mp_loss
    assert jumps == []


def test_established_max_survives_the_garbage():
    """The garbage frames propose maxima of 2310 and 2272310. Whatever else
    happens, the tracker must still believe the real one afterwards --
    adopting a wrong max is what let cur=114497 look legitimate."""
    session = _guarded_session(COVERED_PANEL_FRAMES)
    assert session._mp._max == 2816
    assert session._hp._max == 824
