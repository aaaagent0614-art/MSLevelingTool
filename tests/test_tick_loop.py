"""Regression tests for the tick loop's survival guarantees.

Both of these cover the same field-reported bug: the release .exe would stop
updating at random and never recover -- the window stayed responsive, so
clicking "Restart Session" appeared to do nothing. Root cause was an
exception escaping _tick *before* it rescheduled itself, which permanently
ended the only thing driving the HUD.

These poke OverlayApp's unbound methods with a stub `self` rather than
constructing the real app: OverlayApp.__init__ needs a live Tk display, and
what's under test here is control flow, not widgets.
"""
from __future__ import annotations

import io

import pytest

from maple_analyzer.overlay import TARGET_MS, OverlayApp


class _TickStub:
    """Minimal stand-in for OverlayApp's collaborators used by _tick."""

    def __init__(self, raises: Exception | None = None, delay: int = 123):
        self._raises = raises
        self._delay = delay
        self.scheduled: list[int] = []
        self.status_errors: list[str] = []
        self.root = self  # so self.root.after resolves back here

    def after(self, delay: int, _callback) -> None:
        self.scheduled.append(delay)

    def _do_tick(self) -> int:
        if self._raises is not None:
            raise self._raises
        return self._delay

    def _t(self, _key: str, **kwargs: object) -> str:
        return str(kwargs.get("detail", ""))

    def _set_status_error(self, text: str) -> None:
        self.status_errors.append(text)

    # _log is a @staticmethod; re-wrap it, or assigning the plain function
    # into this class body would rebind it as an instance method.
    _log = staticmethod(OverlayApp._log)
    _tick = OverlayApp._tick


def test_tick_reschedules_on_success():
    stub = _TickStub(delay=200)
    OverlayApp._tick(stub)
    assert stub.scheduled == [200]


def test_tick_reschedules_when_do_tick_raises():
    stub = _TickStub(raises=ValueError("boom"))
    OverlayApp._tick(stub)
    assert stub.scheduled == [TARGET_MS]
    assert stub.status_errors == ["boom"]


def test_tick_reschedules_even_if_the_error_handler_itself_fails():
    """The actual production failure: _do_tick raised UnicodeEncodeError out
    of a debug print, and the handler's own logging re-raised on the same
    unencodable text. The reschedule must survive that."""
    stub = _TickStub(raises=ValueError("boom"))
    stub._set_status_error = lambda _text: (_ for _ in ()).throw(RuntimeError("handler broke"))
    OverlayApp._tick(stub)
    assert stub.scheduled == [TARGET_MS]


def test_log_never_propagates(monkeypatch, capsys):
    """Debug logging is the least important thing the app does; it must not be
    able to take the tick loop down."""
    def _explode(*_a, **_kw):
        raise UnicodeEncodeError("cp950", "别", 0, 1, "illegal multibyte sequence")

    monkeypatch.setattr("builtins.print", _explode)
    OverlayApp._log("anything")  # must not raise


def test_windowed_sink_encodes_simplified_chinese():
    """The windowed (console=False) build's stdout sink must not be the
    locale codepage with errors='strict'. PP-OCR's recognition dictionary is
    largely Simplified Chinese, and cp950/Big5 cannot encode most of it -- a
    single garbage OCR read used to raise straight out of a debug print.
    """
    with pytest.raises(UnicodeEncodeError):
        # What the old code effectively built (locale default on a zh-TW box).
        io.TextIOWrapper(io.BytesIO(), encoding="cp950").write("别这样")

    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")
    sink.write("别这样")  # must not raise
