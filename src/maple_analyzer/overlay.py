"""Always-on-top HUD: capture -> OCR -> parse -> session -> redraw, on a timer.

Measured against the live game (2026-08-17): capture is ~3.5ms, OCR is the real
cost at ~566ms/frame. `_tick()` runs synchronously then reschedules itself
POLL_MS later, so the actual cycle is ~566ms + 500ms =~ 1.07s (~0.93Hz), not the
2Hz the fixed 500ms constant implies on its own -- confirmed fine for this use
case (HP/MP/EXP don't change fast enough for sub-second precision to matter).
OCR runs every tick; the pixel-diff skip-check from the spec is not yet
implemented (see README "Not yet built").
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import Protocol

from .ocr import StatPanelOcr
from .parser import StatSnapshot, parse_stat_lines
from .rate import Session, SessionSummary

POLL_MS = 500
WINDOW_MIN = 1  # session length in minutes -- set short for testing


class PanelSource(Protocol):
    def grab_panel(self):
        ...


def _fmt_summary(s: SessionSummary) -> str:
    diff = s.exp_diff
    diff_s = f"+{diff:,}" if diff is not None else "?"
    start_s = f"{s.start_exp:,}" if s.start_exp is not None else "?"
    end_s = f"{s.end_exp:,}" if s.end_exp is not None else "?"
    return (
        f"Last session ({s.duration_s / 60:.1f}m): "
        f"EXP {start_s} -> {end_s} ({diff_s})  "
        f"HP -{s.hp_loss}  MP -{s.mp_loss}"
    )


class OverlayApp:
    def __init__(self, source: PanelSource):
        self._source = source
        self._ocr = StatPanelOcr()
        self._session = Session()
        self._last_summary: SessionSummary | None = None

        self._last: StatSnapshot = StatSnapshot(None, None, None, None, None, None, None)

        self.root = tk.Tk()
        self.root.title("MapleStoryAnalyer")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.geometry("340x330+40+40")

        self._labels: dict[str, tk.Label] = {}
        for key in ("level", "hp", "mp", "exp", "startexp", "session", "expdiff", "hploss", "mploss", "status"):
            lbl = tk.Label(
                self.root, text="...", fg="#c8ffb0", bg="black",
                font=("Consolas", 11), anchor="w", justify="left",
            )
            lbl.pack(fill="x", padx=8, pady=1)
            self._labels[key] = lbl

        self._summary_label = tk.Label(
            self.root, text="Last session: (none yet)", fg="#7fa8ff", bg="black",
            font=("Consolas", 10), anchor="w", justify="left", wraplength=320,
        )
        self._summary_label.pack(fill="x", padx=8, pady=(10, 1))

        self._tick()

    def _tick(self) -> None:
        try:
            frame = self._source.grab_panel()
        except RuntimeError as e:
            # Game window gone (closed/crashed) or minimized -- don't crash
            # the HUD, show it plainly and keep retrying at a slower pace in
            # case it reopens/is restored.
            self._labels["status"]["text"] = str(e)
            self.root.after(2000, self._tick)
            return
        lines = self._ocr.read(frame)
        snap = parse_stat_lines(lines)
        print(f"[{time.strftime('%H:%M:%S')}] raw={[l.text for l in lines]}", flush=True)
        print(f"          -> {snap}", flush=True)
        # A single tick occasionally misses a field (combat effects/floating
        # damage numbers over the HP/MP bars, transient OCR confidence dips) --
        # observed live: HP briefly read as None while MP/EXP/LV parsed fine on
        # the same frame. Carry forward the last known value per field instead
        # of flickering to '--' on every miss; a field that's genuinely gone
        # (e.g. OCR permanently broken) will just show stale data, which is a
        # more honest failure mode than a blank field for a live number.
        merged = StatSnapshot(*(
            new if new is not None else old
            for new, old in zip(vars(snap).values(), vars(self._last).values())
        ))
        self._last = merged
        self._session.record(merged.exp_cur, merged.hp_cur, merged.mp_cur)

        if self._session.elapsed() >= WINDOW_MIN * 60:
            self._last_summary = self._session.finalize()
            print(f"[{time.strftime('%H:%M:%S')}] {_fmt_summary(self._last_summary)}", flush=True)
            self._session.start()

        self._render(merged)
        self.root.after(POLL_MS, self._tick)

    def _render(self, snap: StatSnapshot) -> None:
        self._labels["level"]["text"] = f"LV {snap.level if snap.level is not None else '?'}"
        self._labels["hp"]["text"] = f"HP  {snap.hp_cur}/{snap.hp_max}" if snap.hp_cur is not None else "HP  --"
        self._labels["mp"]["text"] = f"MP  {snap.mp_cur}/{snap.mp_max}" if snap.mp_cur is not None else "MP  --"
        pct = f" ({snap.exp_pct:.2f}%)" if snap.exp_pct is not None else ""
        self._labels["exp"]["text"] = f"EXP {snap.exp_cur}{pct}" if snap.exp_cur is not None else "EXP --"

        start_exp = self._session.start_exp
        self._labels["startexp"]["text"] = f"Start EXP  {start_exp:,}" if start_exp is not None else "Start EXP  --"

        remaining = max(0.0, WINDOW_MIN * 60 - self._session.elapsed())
        self._labels["session"]["text"] = f"Session: {int(remaining // 60)}:{int(remaining % 60):02d} left"

        exp_diff = self._session.exp_diff
        if exp_diff is not None:
            pct_s = ""
            # Total EXP required for the current level isn't shown directly by
            # the game, but can be derived from any single tick that has both
            # the absolute value and percentage: total = cur / (pct/100).
            # Anchoring off the current tick (rather than diffing OCR'd
            # percentages directly) is more robust since per-level EXP totals
            # are constant, while independently-read percentages carry their
            # own OCR noise on top of the cur value's.
            if snap.exp_cur and snap.exp_pct:
                total_exp = snap.exp_cur / (snap.exp_pct / 100)
                pct_s = f" (+{exp_diff / total_exp * 100:.2f}%)"
            self._labels["expdiff"]["text"] = f"EXP diff  +{exp_diff:,}{pct_s}"
        else:
            self._labels["expdiff"]["text"] = "EXP diff  --"

        self._labels["hploss"]["text"] = f"HP loss  -{self._session.hp_loss}"
        self._labels["mploss"]["text"] = f"MP loss  -{self._session.mp_loss}"

        # Idle only if NONE of HP/MP/EXP have changed recently within this
        # session -- any one of them moving counts as activity, not idle.
        idle = self._session.hp_loss == 0 and self._session.mp_loss == 0 and (exp_diff or 0) == 0
        self._labels["status"]["text"] = "idle" if idle else "tracking"

        if self._last_summary is not None:
            self._summary_label["text"] = _fmt_summary(self._last_summary)

    def run(self) -> None:
        self.root.mainloop()
