"""Compare tab (2026-08-28): pick two History records via dropdowns and diff
their per-minute metrics. The old Dashboard baseline comparison was replaced
by this dedicated tab -- these tests cover the label/round-trip logic and the
no-widget guards (full widget behaviour is exercised in the Xvfb smoke test).

Same technique as test_run_state.py: poke OverlayApp's unbound methods
against a stub `self` (reusing its _StubApp).
"""
from __future__ import annotations

from maple_analyzer import overlay as overlay_module
from maple_analyzer.rate import SessionSummary

from test_run_state import _StubApp

_StubApp._compare_label_for = overlay_module.OverlayApp._compare_label_for
_StubApp._summary_from_label = overlay_module.OverlayApp._summary_from_label
_StubApp._on_compare_select = overlay_module.OverlayApp._on_compare_select
_StubApp._refresh_compare_tab = overlay_module.OverlayApp._refresh_compare_tab


def _summary(start: float, name: str | None = None, exp_gained: int = 100) -> SessionSummary:
    return SessionSummary(
        start_time=start, end_time=start + 60, start_exp=100, end_exp=200,
        hp_loss=0, mp_loss=0, total_exp=None, interval_minutes=10,
        name=name, exp_gained=exp_gained,
    )


def test_compare_label_uses_custom_name():
    app = _StubApp()
    s = _summary(1000.0, "練功點 A")
    app._session_history.append(s)
    label = app._compare_label_for(s)
    assert label.startswith("練功點 A（")
    assert label.endswith("）")  # includes the record's start time for disambiguation


def test_compare_label_falls_back_to_history_index():
    app = _StubApp()
    s1 = _summary(1000.0)
    s2 = _summary(2000.0)
    app._session_history[:] = [s1, s2]
    assert app._compare_label_for(s1).startswith("紀錄 #1（")
    assert app._compare_label_for(s2).startswith("紀錄 #2（")


def test_summary_from_label_roundtrip():
    app = _StubApp()
    s = _summary(1000.0, "A")
    app._session_history.append(s)
    assert app._summary_from_label(app._compare_label_for(s)) is s


def test_summary_from_label_unknown_returns_none():
    app = _StubApp()
    app._session_history.append(_summary(1000.0))
    assert app._summary_from_label("不存在的紀錄") is None


def test_compare_select_noop_without_widgets():
    """The run-state stub has no Compare-tab widgets; the method must no-op
    rather than AttributeError (it is called from _refresh_compare_tab)."""
    app = _StubApp()
    app._on_compare_select()


def test_refresh_compare_tab_noop_without_widgets():
    app = _StubApp()
    app._refresh_compare_tab()
