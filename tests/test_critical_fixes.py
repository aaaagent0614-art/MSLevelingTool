"""Regression tests for the critical fixes (2026-09-02, v1.8.1):

1. finalize() while paused used raw wall-clock end_time, so the wait between
   Stop (which pauses) and the eventual commit inflated duration_s and diluted
   every per-minute metric. SessionSummary.paused_s now records the paused
   time and duration_s subtracts it.
2. A rejected garbage tick still updated Session._hp_cur/_mp_cur, so the
   *next* session's baseline started from the garbage (the tracker's guarded
   _last is discarded by reset()). Only accepted readings update the fields.
3. _version_is_newer fell back to lexicographic comparison on any unparseable
   input, so '1.8.0-beta' > '1.8.0' ranked True. Only the x.y.z triple counts.
"""
from __future__ import annotations

import pytest

from maple_analyzer import overlay as overlay_module
from maple_analyzer.rate import Session


# ---- 1. paused finalize duration -----------------------------------------

def test_finalize_while_paused_excludes_the_wait_from_duration():
    """The stop-into-pending flow pauses a session and commits it later, still
    paused. duration_s must be the ACTIVE time (60s of play, not 60s + a
    5-minute wait), or History/compare/per-minute metrics are all diluted."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s._start_time = 0.0
    s.record(exp_cur=1600, hp_cur=450, mp_cur=150)  # +600 EXP over the first 60s
    s.pause(now=60.0)
    # Committed 5 minutes later (sale recorded / next Start / app close).
    summary = s.finalize(interval_minutes=10, now=360.0)
    assert summary.paused_s == pytest.approx(300.0)
    assert summary.duration_s == pytest.approx(60.0)
    assert summary.exp_per_min == pytest.approx(600.0)  # not 600*60/360=100


def test_finalize_after_resume_counts_only_real_pause_time():
    """Pause -> resume -> pause accumulates exactly the paused spans; a session
    finalized while running (timer rollover, no pending flow) keeps paused_s 0."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s._start_time = 0.0
    s.pause(now=10.0)
    s.resume(now=30.0)  # 20s paused
    s.record(exp_cur=1100, hp_cur=500, mp_cur=200)  # rebaseline tick
    summary = s.finalize(interval_minutes=10, now=100.0)
    # wall span 100s, paused 20s -> duration 80s
    assert summary.paused_s == pytest.approx(20.0)
    assert summary.duration_s == pytest.approx(80.0)


def test_finalize_normal_run_has_zero_paused_s():
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s._start_time = 0.0
    s.record(exp_cur=1600, hp_cur=450, mp_cur=150)
    summary = s.finalize(interval_minutes=10, now=120.0)
    assert summary.paused_s == 0.0
    assert summary.duration_s == pytest.approx(120.0)


def test_old_history_without_paused_s_field_loads():
    """Summaries persisted before paused_s existed have no such field; the
    dataclass default keeps their duration unchanged (old JSON round-trips)."""
    from dataclasses import asdict

    from maple_analyzer.rate import SessionSummary

    s = SessionSummary(
        start_time=0.0, end_time=120.0, start_exp=1000, end_exp=1600,
        hp_loss=50, mp_loss=50, total_exp=2000.0, interval_minutes=10.0,
    )
    d = asdict(s)
    d.pop("paused_s")  # simulate a pre-fix persisted record
    loaded = SessionSummary(**d)
    assert loaded.paused_s == 0.0
    assert loaded.duration_s == pytest.approx(120.0)


# ---- 2. rejected garbage tick vs next-session baseline --------------------

def test_rejected_garbage_last_tick_does_not_poison_next_session_baseline():
    """A garbage final tick (misread '/' shifts a digit: max 816 vs real 2816)
    must not become the next session's baseline. Before the fix it updated
    Session._mp_cur unconditionally; the restart then booked ~14k of phantom
    MP loss on the first two clean readings."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2800, hp_max=824, mp_max=2816)  # baseline
    # Garbage: '16632/816' -- max outside the plausible band -> rejected whole.
    s.record(exp_cur=1000, hp_cur=400, mp_cur=16632, hp_max=824, mp_max=816)
    assert s.mp_loss == 0  # the in-session guard already rejects it
    # Restart: baseline must be the last ACCEPTED value (2800), not 16632.
    s.start()
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2800, hp_max=824, mp_max=2816)
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2750, hp_max=824, mp_max=2816)
    assert s.mp_loss == 50  # only the real 50 MP drop, no phantom ~14k


def test_rejected_cur_over_max_does_not_poison_next_baseline():
    """Same boundary for a tick rejected because cur > max."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2600, hp_max=824, mp_max=2816)
    s.record(exp_cur=1000, hp_cur=400, mp_cur=99999, hp_max=824, mp_max=9999)
    assert s.mp_loss == 0
    s.start()
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2600, hp_max=824, mp_max=2816)
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2500, hp_max=824, mp_max=2816)
    assert s.mp_loss == 100


def test_uncorroborated_outlier_does_not_poison_next_baseline():
    """A held outlier (no corroborating tick) must not leak into the next
    session's baseline either -- only committed readings carry over."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2800, hp_max=824, mp_max=2816)
    # Big drop to 100 is > OUTLIER_FRACTION away -> held, never committed.
    s.record(exp_cur=1000, hp_cur=400, mp_cur=100, hp_max=824, mp_max=2816)
    assert s.mp_loss == 0
    s.start()
    # Next session's first reading is the true value; no phantom loss.
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2800, hp_max=824, mp_max=2816)
    assert s.mp_loss == 0
    s.record(exp_cur=1000, hp_cur=400, mp_cur=2750, hp_max=824, mp_max=2816)
    assert s.mp_loss == 50


# ---- 3. _version_is_newer ------------------------------------------------

def test_version_is_newer_ignores_prerelease_suffix():
    newer = overlay_module._version_is_newer
    # Same x.y.z with a -beta suffix is NOT newer than the release itself.
    assert newer("1.8.0-beta", "1.8.0") is False
    assert newer("1.8.0", "1.8.0-beta") is False
    # Real bumps still rank correctly.
    assert newer("1.9.0", "1.8.0") is True
    assert newer("1.10.0", "1.9.0") is True
    assert newer("1.8.0", "1.8.1") is False
    assert newer("1.8.1-beta", "1.8.0") is True


def test_version_is_newer_unparseable_never_claims_an_update():
    newer = overlay_module._version_is_newer
    assert newer("garbage", "1.8.0") is False
    assert newer("1.8.0", "garbage") is False
    assert newer("v2", "1.8.0") is False  # no full x.y.z triple


# ---- 2026-09-02 feature fixes: potion used on History, net meso -----------

def test_finalize_stamps_quick_slot_potion_used():
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200, hp_max=824, mp_max=2816)
    s.record_potion("hp", 30)
    s.record_potion("hp", 25)  # 5 HP bottles gone
    s.record_potion("mp", 10)
    s.record_potion("mp", 7)   # 3 MP bottles gone
    summary = s.finalize(interval_minutes=10, now=100.0)
    assert summary.hp_potion_used == 5
    assert summary.mp_potion_used == 3


def test_finalize_potion_used_none_without_slot_reads():
    """Sessions that never tracked quick-slot counts keep the fields None so
    History can render '--' instead of a fabricated 0."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200, hp_max=824, mp_max=2816)
    summary = s.finalize(interval_minutes=10, now=100.0)
    assert summary.hp_potion_used is None
    assert summary.mp_potion_used is None


def test_history_net_total():
    """History meso headline: drop + item sale - potion spend."""
    net = overlay_module._history_net_total

    class FakeSummary:
        pass

    def mk(meso_gained, sale_meso=0, potion_cost=0):
        s = FakeSummary()
        s.meso_gained = meso_gained
        s.sale_meso = sale_meso
        s.potion_cost = potion_cost
        return s

    assert net(mk(10_000)) == 10_000
    assert net(mk(10_000, sale_meso=5_000)) == 15_000
    assert net(mk(10_000, sale_meso=5_000, potion_cost=3_200)) == 11_800
    assert net(mk(10_000, potion_cost=12_000)) == -2_000
    assert net(mk(None, sale_meso=5_000, potion_cost=1_000)) == 4_000
    assert net(mk(None)) is None


def test_summary_new_potion_fields_survive_round_trip_without_them():
    """Summaries persisted before the potion fields existed load fine (the
    dataclass defaults kick in) and the fields are None/0, not errors."""
    from dataclasses import asdict

    from maple_analyzer.rate import SessionSummary

    s = SessionSummary(
        start_time=0.0, end_time=60.0, start_exp=1000, end_exp=1100,
        hp_loss=0, mp_loss=0, total_exp=2000.0, interval_minutes=10.0,
    )
    d = asdict(s)
    for field in ("hp_potion_used", "mp_potion_used", "potion_cost"):
        d.pop(field)
    loaded = SessionSummary(**d)
    assert loaded.hp_potion_used is None
    assert loaded.mp_potion_used is None
    assert loaded.potion_cost == 0


# ---- 2026-09-02: session baseline must reflect the CURRENT screen ---------

def test_sync_known_seeds_baseline_from_ui_readings_before_start():
    """The UI OCRs while stopped; sync_known hands those readings over so a
    restart baseline is the current screen, not the previous session's end."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)  # old session's end values
    # Screen moved on while stopped (player kept playing / relogged): UI's
    # _last now reads 2000/800/300, the engine still remembers 1000/500/200.
    s.sync_known(exp_cur=2000, hp_cur=800, mp_cur=300)
    s.start()
    assert s.start_exp == 2000  # baseline is the CURRENT value, not 1000
    s.record(exp_cur=2100, hp_cur=780, mp_cur=290)  # 100 EXP, 20 HP, 10 MP gained
    assert s.exp_diff == 100   # no phantom 1100 from the stale 1000 baseline
    assert s.hp_loss == 20
    assert s.mp_loss == 10


def test_sync_known_ignores_none_fields():
    """Partial readings (some fields momentarily OCR-missing) must not wipe
    the values the engine already holds."""
    s = Session(require_calibration=False)
    s.record(exp_cur=1000, hp_cur=500, mp_cur=200)
    s.sync_known(exp_cur=1100, hp_cur=None, mp_cur=None)  # only EXP synced
    s.start()
    assert s.start_exp == 1100  # synced EXP became the baseline
    # HP/MP kept their last engine values (sync didn't null them): a normal
    # reading after start books no phantom loss.
    s.record(exp_cur=1150, hp_cur=500, mp_cur=200)
    assert s.hp_loss == 0
    assert s.mp_loss == 0
