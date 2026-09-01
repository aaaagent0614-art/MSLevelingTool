"""Potion cost / net income (2026-08-28): configured HP/MP potion prices turn
HP/MP loss into 藥水成本 and 淨收益 (meso − potion cost) on the Dashboard and
the compact overlay's meso cell."""
from __future__ import annotations

from test_run_state import _StubApp


def _app_with_losses(hp: int = 0, mp: int = 0) -> _StubApp:
    app = _StubApp()
    app._session._hp.loss = hp
    app._session._mp.loss = mp
    return app


def test_potion_disabled_by_default():
    app = _StubApp()
    assert app._potion_enabled() is False
    assert app._potion_cost() == 0


def test_potion_enabled_requires_price_and_restore():
    app = _app_with_losses(hp=100)
    app._settings.hp_potion_price = 100
    assert app._potion_enabled() is False  # restore still 0
    app._settings.hp_potion_restore = 50
    assert app._potion_enabled() is True


def test_potion_cost_ceil_bottles():
    app = _app_with_losses(hp=1000, mp=500)
    app._settings.hp_potion_price = 100
    app._settings.hp_potion_restore = 300  # 1000 -> 4 bottles (ceil)
    app._settings.mp_potion_price = 200
    app._settings.mp_potion_restore = 300  # 500 -> 2 bottles (ceil)
    assert app._potion_cost() == 4 * 100 + 2 * 200  # 800


def test_potion_cost_exact_bottles_no_extra():
    app = _app_with_losses(hp=600, mp=0)
    app._settings.hp_potion_price = 150
    app._settings.hp_potion_restore = 300  # exactly 2 bottles
    assert app._potion_cost() == 300


def test_potion_cost_zero_loss():
    app = _app_with_losses(hp=0, mp=0)
    app._settings.hp_potion_price = 100
    app._settings.hp_potion_restore = 300
    assert app._potion_cost() == 0


def test_mp_only_configuration():
    app = _app_with_losses(hp=1000, mp=100)
    app._settings.mp_potion_price = 500
    app._settings.mp_potion_restore = 100
    assert app._potion_enabled() is True
    assert app._potion_cost() == 1 * 500  # HP potion not configured -> ignored


# ---- quick-slot potion counter (2026-09-02) -------------------------------


def _running_app() -> _StubApp:
    app = _StubApp()
    app._session._calibrated = True
    app._session.start()
    return app


def test_quick_slot_consumed_needs_both_endpoints():
    s = _running_app()._session
    assert s.quick_slot_consumed is None
    s.record_quick_slot(50)
    assert s.quick_slot_consumed is None  # baseline only, no end yet
    s.record_quick_slot(42)
    assert s.quick_slot_consumed == 8


def test_quick_slot_consumed_clamps_refill():
    s = _running_app()._session
    s.record_quick_slot(50)
    s.record_quick_slot(60)  # refilled mid-session -> end > start
    assert s.quick_slot_consumed == 0  # unseen refill, honest 0


def test_quick_slot_noop_before_start():
    app = _StubApp()  # session never started
    app._session.record_quick_slot(50)
    assert app._session.quick_slot_consumed is None


def test_potion_cost_prefers_quick_slot_path():
    app = _running_app()
    app._settings.quick_slot_index = 3
    app._settings.manual_quick_bar_region = (100, 100, 700, 140)
    app._settings.quick_slot_kind = "hp"
    app._settings.hp_potion_price = 320
    app._session.record_quick_slot(50)
    app._session.record_quick_slot(42)  # 8 bottles
    assert app._potion_cost() == 8 * 320
    # MP kind uses the MP price instead.
    app._settings.quick_slot_kind = "mp"
    app._settings.mp_potion_price = 200
    assert app._potion_cost() == 8 * 200


def test_potion_cost_falls_back_to_loss_estimate_without_reads():
    app = _running_app()
    app._settings.quick_slot_index = 3
    app._settings.manual_quick_bar_region = (100, 100, 700, 140)
    app._settings.hp_potion_price = 100
    app._settings.hp_potion_restore = 300
    app._session._hp.loss = 1000
    assert app._potion_cost() == 4 * 100  # quick-slot has no readings yet


def test_read_quick_slot_count_off_when_unconfigured():
    app = _StubApp()
    assert app._read_quick_slot_count() is None
    app._settings.quick_slot_index = 1  # no marked region yet
    assert app._read_quick_slot_count() is None
