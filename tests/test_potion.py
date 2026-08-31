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
