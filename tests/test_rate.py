"""Session/SessionSummary tests -- pure logic, no OCR/images."""
import dataclasses

from maple_analyzer.rate import Session


def test_start_exp_set_on_first_record():
    s = Session()
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    assert s.start_exp == 1000
    assert s.exp_diff == 0


def test_exp_diff_tracks_gain():
    s = Session()
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.record(exp_cur=1500, hp_cur=500, mp_cur=200)
    assert s.exp_diff == 500


def test_hp_mp_loss_only_accumulates_on_decrease():
    s = Session()
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.record(exp_cur=1000, hp_cur=400, mp_cur=150)  # lost 100 HP, 50 MP
    s.record(exp_cur=1000, hp_cur=450, mp_cur=180)  # healed -- no loss added
    s.record(exp_cur=1000, hp_cur=300, mp_cur=180)  # lost another 150 HP
    assert s.hp_loss == 250
    assert s.mp_loss == 50


def test_missing_reading_does_not_corrupt_loss_tracking():
    s = Session()
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.record(exp_cur=None, hp_cur=None, mp_cur=None)  # a tick that missed everything
    s.record(exp_cur=1000, hp_cur=450, mp_cur=200)
    assert s.hp_loss == 50


def test_finalize_produces_correct_summary():
    s = Session()
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200, exp_pct=10.0)
    s.record(exp_cur=1200, hp_cur=400, mp_cur=200)
    summary = s.finalize(interval_minutes=5, now=s._start_time + 60)
    assert summary.start_exp == 1000
    assert summary.end_exp == 1200
    assert summary.exp_diff == 200
    assert summary.hp_loss == 100
    assert summary.mp_loss == 0
    assert summary.duration_s == 60
    assert summary.interval_minutes == 5
    assert summary.name is None
    assert summary.total_exp == 10000  # 1000 / (10.0/100)
    assert summary.exp_pct_diff == 2.0  # 200/10000 * 100


def test_restart_carries_forward_last_values():
    s = Session()
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.record(exp_cur=1200, hp_cur=400, mp_cur=180)
    s.start()  # simulates restart -- new session should baseline off last-known values
    assert s.start_exp == 1200
    assert s.hp_loss == 0
    assert s.mp_loss == 0
    s.record(exp_cur=1200, hp_cur=350, mp_cur=180)
    assert s.hp_loss == 50  # loss measured from the carried-forward baseline, not 0


def test_summary_is_renamable_via_dataclasses_replace():
    # SessionSummary is frozen -- the History tab's rename feature works by
    # replacing the stored summary, not mutating it in place.
    s = Session()
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    summary = s.finalize()
    renamed = dataclasses.replace(summary, name="grinding spot A")
    assert renamed.name == "grinding spot A"
    assert summary.name is None  # original untouched
    assert renamed.start_exp == summary.start_exp  # everything else preserved
