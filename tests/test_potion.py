"""Potion cost / net income (2026-08-28, simplified 2026-09-03): configured
HP/MP potion unit prices turn consumed bottles into 藥水成本 and 淨收益
(meso − potion cost) on the Dashboard and the compact overlay's meso cell.
Since 2026-09-03 the cost comes ONLY from the quick-slot counter (bottles
actually consumed × price): HP/MP readings retired, so the old loss/restore
estimate has no input and is gone."""
from __future__ import annotations

from test_run_state import _StubApp


def _app_with_prices(hp: int = 0, mp: int = 0) -> _StubApp:
    app = _StubApp()
    app._settings.hp_potion_price = hp
    app._settings.mp_potion_price = mp
    return app


def test_potion_disabled_by_default():
    app = _StubApp()
    assert app._potion_enabled() is False
    assert app._potion_cost() == 0


def test_potion_enabled_by_price_only():
    # A unit price alone turns the stat's cost on; no restore amount anymore.
    assert _app_with_prices(hp=100)._potion_enabled() is True
    assert _app_with_prices(mp=500)._potion_enabled() is True


def test_potion_cost_zero_without_quick_slot_reads():
    # Prices configured but no quickbar slot was read (or none picked): cost
    # stays 0 -- there is no loss-based estimate to fall back on (2026-09-03).
    app = _app_with_prices(hp=100, mp=200)
    app._session._calibrated = True
    app._session.start()
    assert app._potion_cost() == 0


def test_potion_cost_uses_consumed_bottles_only():
    app = _StubApp()
    app._session._calibrated = True
    app._session.start()
    app._settings.hp_quick_slot_index = 3
    app._settings.mp_quick_slot_index = 7
    app._settings.hp_potion_price = 320
    app._settings.mp_potion_price = 200
    app._session.record_potion("hp", 50)
    app._session.record_potion("hp", 42)  # 8 HP bottles
    app._session.record_potion("mp", 30)
    app._session.record_potion("mp", 25)  # 5 MP bottles
    assert app._potion_cost() == 8 * 320 + 5 * 200


def test_potion_cost_hp_only_configuration():
    # MP has a price but no slot picked -> contributes nothing.
    app = _StubApp()
    app._session._calibrated = True
    app._session.start()
    app._settings.hp_quick_slot_index = 1
    app._settings.hp_potion_price = 150
    app._settings.mp_potion_price = 500
    app._session.record_potion("hp", 10)
    app._session.record_potion("hp", 6)  # 4 HP bottles
    assert app._potion_cost() == 4 * 150


# ---- quick-slot potion counter engine (2026-09-02, reworked 2026-09-03) ----


def _running_app() -> _StubApp:
    app = _StubApp()
    app._session._calibrated = True
    app._session.start()
    return app


def test_potion_consumed_needs_both_endpoints():
    s = _running_app()._session
    assert s.hp_potion_consumed is None
    s.record_potion("hp", 50)
    assert s.hp_potion_consumed is None  # baseline only, no end yet
    s.record_potion("hp", 42)
    assert s.hp_potion_consumed == 8


def test_potion_consumed_clamps_refill():
    s = _running_app()._session
    s.record_potion("mp", 50)
    s.record_potion("mp", 60)  # refilled mid-session -> end > start
    assert s.mp_potion_consumed == 0  # unseen refill, honest 0


def test_potion_noop_before_start():
    app = _StubApp()  # session never started
    app._session.record_potion("hp", 50)
    assert app._session.hp_potion_consumed is None


def test_read_slot_counts_off_when_unconfigured():
    app = _StubApp()
    # No quickbar image on this platform -> empty dict, never crashes.
    assert app._read_slot_counts() == {}
    app._settings.hp_quick_slot_index = 1
    assert app._read_slot_counts() == {}
