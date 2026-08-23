"""Pausing a session: freeze accounting and the clock, while the caller keeps
polling/rendering live OCR independently of Session -- see Session.pause's
docstring. Uses require_calibration=False throughout so these tests are
about pause/resume specifically, not calibration (see test_baseline_confirmation.py
for that)."""
from __future__ import annotations

from maple_analyzer.rate import Session


def test_pause_freezes_elapsed():
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.pause(now=100.0)
    assert s.elapsed(now=100.0) == s.elapsed(now=200.0)


def test_pause_stops_accounting():
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.pause()
    s.record(exp_cur=1500, hp_cur=100, mp_cur=50)  # would be a big gain/loss if counted
    assert s.exp_diff == 0
    assert s.hp_loss == 0
    assert s.mp_loss == 0


def test_resume_rebaselines_exp_gained_during_the_pause():
    """Nothing recorded into the session while paused (per the pause design),
    so EXP gained during the gap must not appear as a jump the instant the
    next tick arrives after resume."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.pause()
    s.resume()
    s.record(exp_cur=50_000, hp_cur=500, mp_cur=200)  # big real gain while "paused"
    assert s.exp_diff == 0  # the pause-period gain is excluded, not counted
    s.record(exp_cur=50_100, hp_cur=500, mp_cur=200)  # gain after resume DOES count
    assert s.exp_diff == 100


def test_resume_rebaselines_hp_mp_without_booking_a_phantom_loss():
    """A big HP/MP swing during the pause (fighting while the session is
    parked) must not land as a loss the moment the next tick arrives."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=800, mp_cur=2800, hp_max=824, mp_max=2816)
    s.pause()
    s.resume()
    s.record(exp_cur=1000, hp_cur=100, mp_cur=200, hp_max=824, mp_max=2816)  # took a beating while paused
    assert s.hp_loss == 0
    assert s.mp_loss == 0
    s.record(exp_cur=1000, hp_cur=50, mp_cur=150, hp_max=824, mp_max=2816)
    assert s.hp_loss == 50  # loss after resume counts normally
    assert s.mp_loss == 50


def test_repeated_pause_resume_accumulates_paused_time_correctly():
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s._start_time = 0.0  # pin the clock so the math below is exact
    s.pause(now=10.0)
    s.resume(now=20.0)  # 10s paused so far
    s.record(exp_cur=1100, hp_cur=500, mp_cur=200)  # rebaseline tick -- doesn't itself consume time
    s.pause(now=40.0)  # 20s of running (10 -> 40, minus the 10s pause) since start
    # frozen at the second pause instant: (40 - 0) - 10s paused_total = 30
    assert s.elapsed(now=999.0) == 30.0


def test_pause_is_a_noop_before_the_session_has_started():
    s = Session(require_calibration=False)
    s.pause()
    assert not s.is_paused


def test_resume_is_a_noop_when_not_paused():
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.resume()  # never paused -- must not raise or misbehave
    assert not s.is_paused
    s.record(exp_cur=1100, hp_cur=500, mp_cur=200)
    assert s.exp_diff == 100
