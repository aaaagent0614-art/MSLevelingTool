"""Comparison-baseline behaviour (2026-08-26): picking a History record to
compare live sessions against, clearing it, and the EXP/min percentage math
that drives both the Dashboard card and the compact overlay line.

Same technique as test_run_state.py: poke OverlayApp's unbound methods
against a stub `self` (reusing its _StubApp) rather than constructing the
real app, which needs a live Tk display.
"""
from __future__ import annotations

import time

from maple_analyzer import overlay as overlay_module
from maple_analyzer.rate import SessionSummary

from test_run_state import _StubApp

# Bind the comparison methods under test -- _StubApp only binds what the
# run-state tests exercise.
_StubApp._on_compare_clicked = overlay_module.OverlayApp._on_compare_clicked
_StubApp._compare_base = overlay_module.OverlayApp._compare_base
_StubApp._compare_base_name = overlay_module.OverlayApp._compare_base_name
_StubApp._compare_exp_pct = overlay_module.OverlayApp._compare_exp_pct
_StubApp._on_clear_history_clicked = overlay_module.OverlayApp._on_clear_history_clicked


def _summary(start: float, name: str | None = None, exp_gained: int = 100) -> SessionSummary:
    return SessionSummary(
        start_time=start, end_time=start + 60, start_exp=100, end_exp=200,
        hp_loss=0, mp_loss=0, total_exp=None, interval_minutes=10,
        name=name, exp_gained=exp_gained,
    )


def test_no_baseline_returns_none():
    app = _StubApp()
    assert app._compare_base() is None


def test_click_sets_baseline_then_click_again_clears():
    app = _StubApp()
    app._session_history[:] = [_summary(1.0, "old"), _summary(2.0, "new")]
    app._on_compare_clicked(1)  # top card = index 1
    assert app._settings.compare_start_time == 1.0
    # Same click again clears it.
    app._on_compare_clicked(1)
    assert app._settings.compare_start_time is None


def test_click_different_record_replaces_baseline():
    app = _StubApp()
    app._session_history[:] = [_summary(1.0), _summary(2.0)]
    app._on_compare_clicked(2)
    assert app._settings.compare_start_time == 2.0
    app._on_compare_clicked(1)
    assert app._settings.compare_start_time == 1.0


def test_compare_base_matches_by_start_time():
    app = _StubApp()
    app._session_history[:] = [_summary(1.0), _summary(2.0, "chosen")]
    app._settings.compare_start_time = 2.0
    base = app._compare_base()
    assert base is not None
    assert base.name == "chosen"


def test_compare_base_ignores_stale_start_time():
    app = _StubApp()
    app._session_history[:] = [_summary(1.0)]
    app._settings.compare_start_time = 999.0  # record was deleted
    assert app._compare_base() is None


def test_compare_base_name_prefers_custom_name():
    app = _StubApp()
    app._session_history[:] = [_summary(1.0, "練功點 A"), _summary(2.0)]
    assert app._compare_base_name(app._session_history[0]) == "練功點 A"
    # Unnamed record falls back to its 1-based History index (newest first).
    assert app._compare_base_name(app._session_history[1]) == "紀錄 #2"


def test_compare_exp_pct_math():
    app = _StubApp()
    # Baseline: 100 EXP in 60s = 100 EXP/min.
    app._session_history[:] = [_summary(0.0, exp_gained=100)]
    app._settings.compare_start_time = 0.0
    # Live session: 1000 EXP in 120s = 500 EXP/min -> +400%.
    app._session._start_time = time.time() - 120
    app._session._start_exp = 0
    app._session._exp_cur = 1000
    app._session._segment_start = 0
    app._session._banked = 0
    pct = app._compare_exp_pct(app._compare_base())
    assert pct is not None
    assert abs(pct - 4.0) < 1e-6


def test_compare_exp_pct_none_without_live_data():
    app = _StubApp()
    app._session_history[:] = [_summary(0.0, exp_gained=100)]
    app._settings.compare_start_time = 0.0
    # Session never started -> no exp_diff, no elapsed.
    assert app._compare_exp_pct(app._compare_base()) is None


def test_update_compare_card_noop_without_widget():
    # The run-state stub has no Dashboard card; the method must no-op rather
    # than AttributeError (it is called from _render on every tick).
    app = _StubApp()
    app._update_compare_card()


def test_update_compact_compare_line_noop_without_widget():
    app = _StubApp()
    app._update_compact_compare_line()


def test_delete_baseline_clears_reference(monkeypatch):
    app = _StubApp()
    app._session_history[:] = [_summary(1.0, "keep"), _summary(2.0, "baseline")]
    app._on_compare_clicked(2)
    assert app._settings.compare_start_time == 2.0
    monkeypatch.setattr(overlay_module.messagebox, "askyesno", lambda *a, **kw: True)
    app._on_delete_history_clicked(2)
    assert app._settings.compare_start_time is None
    assert [s.name for s in app._session_history] == ["keep"]


def test_delete_non_baseline_keeps_reference(monkeypatch):
    app = _StubApp()
    app._session_history[:] = [_summary(1.0, "keep"), _summary(2.0, "baseline")]
    app._on_compare_clicked(2)
    monkeypatch.setattr(overlay_module.messagebox, "askyesno", lambda *a, **kw: True)
    app._on_delete_history_clicked(1)
    assert app._settings.compare_start_time == 2.0


def test_clear_history_clears_reference(monkeypatch):
    app = _StubApp()
    app._session_history[:] = [_summary(1.0)]
    app._on_compare_clicked(1)
    monkeypatch.setattr(overlay_module.messagebox, "askyesno", lambda *a, **kw: True)
    app._on_clear_history_clicked()
    assert app._settings.compare_start_time is None
