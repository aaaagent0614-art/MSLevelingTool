"""Manual stat correction (2026-08-27): the player can fix an OCR misread by
typing the real LV/HP/MP/EXP value on the Dashboard. The typed value folds
into the merged snapshot every tick (so Session sees it too) until cleared
by another edit or a 辨識 pass.
"""
from __future__ import annotations

from maple_analyzer import overlay as overlay_module
from maple_analyzer.parser import StatSnapshot

from test_run_state import _StubApp

_StubApp._run_auto_detection = overlay_module.OverlayApp._run_auto_detection


def _snap() -> StatSnapshot:
    return StatSnapshot(44, 800, 824, 2800, 2816, 100, 0.02)


def test_apply_manual_overrides_folds_into_last():
    app = _StubApp()
    app._last = _snap()
    app._manual_overrides["hp"] = 500
    app._apply_manual_overrides()
    assert app._last.hp_cur == 500
    assert app._last.hp_max == 824  # max untouched
    assert app._last.level == 44  # non-overridden fields untouched


def test_apply_manual_overrides_multiple_fields():
    app = _StubApp()
    app._last = _snap()
    app._manual_overrides.update({"level": 45, "exp": 12345})
    app._apply_manual_overrides()
    assert app._last.level == 45
    assert app._last.exp_cur == 12345
    assert app._last.hp_cur == 800


def test_apply_manual_overrides_noop_when_empty():
    app = _StubApp()
    app._last = _snap()
    app._apply_manual_overrides()
    assert app._last.hp_cur == 800
    assert app._last.level == 44


def test_apply_manual_overrides_after_clear_restores_ocr_value():
    app = _StubApp()
    app._last = _snap()
    app._manual_overrides["mp"] = 999
    app._apply_manual_overrides()
    assert app._last.mp_cur == 999
    # Clearing the override restores the OCR stream (last value untouched).
    app._manual_overrides.pop("mp")
    app._apply_manual_overrides()
    assert app._last.mp_cur == 999  # _last holds the folded value until next tick


def test_read_detected_values_survives_garbage():
    """A failed 辨識 read must leave the previous values in place, never
    crash the main thread."""
    app = _StubApp()
    app._last = _snap()
    app._read_detected_values(object(), {"LV": (0.0, 0.0, 0.5, 0.2)})  # no .size/.crop
    assert app._last.level == 44
    assert app._last.hp_cur == 800


def test_detect_pass_clears_overrides(monkeypatch):
    """A fresh 辨識 re-reads the game, so hand-typed corrections from before
    are cleared (they would fight the new detection)."""
    app = _StubApp()
    app._manual_overrides["hp"] = 500
    # _StubSource has no grab_full, so detection fails harmlessly; the
    # override clear happens regardless. Stub the post-detect UI calls.
    monkeypatch.setattr(app, "_apply_locate", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(app, "_set_detect_result", lambda *a, **k: None, raising=False)
    app._run_auto_detection()
    assert app._manual_overrides == {}
