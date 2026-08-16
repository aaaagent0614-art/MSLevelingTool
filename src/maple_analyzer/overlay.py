"""Always-on-top HUD: capture -> OCR -> parse -> rate -> redraw, on a timer.

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
from .rate import DiffTracker, LossTracker

POLL_MS = 500
WINDOW_MIN = 5


class PanelSource(Protocol):
    def grab_panel(self):
        ...


class OverlayApp:
    def __init__(self, source: PanelSource):
        self._source = source
        self._ocr = StatPanelOcr()
        self._exp_diff = DiffTracker(reset_on_drop=True)  # level-up resets EXP to 0
        self._hp_loss = LossTracker()
        self._mp_loss = LossTracker()

        self._last: StatSnapshot = StatSnapshot(None, None, None, None, None, None, None)
        self._start_time = time.time()

        self.root = tk.Tk()
        self.root.title("MapleStoryAnalyer")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.geometry("300x290+40+40")

        self._labels: dict[str, tk.Label] = {}
        for key in ("level", "hp", "mp", "exp", "window", "expdiff", "hploss", "mploss", "status"):
            lbl = tk.Label(
                self.root, text="...", fg="#c8ffb0", bg="black",
                font=("Consolas", 11), anchor="w", justify="left",
            )
            lbl.pack(fill="x", padx=8, pady=1)
            self._labels[key] = lbl

        self._tick()

    def _tick(self) -> None:
        try:
            frame = self._source.grab_panel()
        except RuntimeError as e:
            # Game window gone (closed/crashed) -- don't crash the HUD, show it
            # plainly and keep retrying at a slower pace in case it reopens.
            self._labels["status"]["text"] = f"game not found: {e}"
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
        self._exp_diff.record(merged.exp_cur)
        self._hp_loss.record(merged.hp_cur)
        self._mp_loss.record(merged.mp_cur)
        self._render(merged)
        self.root.after(POLL_MS, self._tick)

    def _render(self, snap: StatSnapshot) -> None:
        self._labels["level"]["text"] = f"LV {snap.level if snap.level is not None else '?'}"
        self._labels["hp"]["text"] = f"HP  {snap.hp_cur}/{snap.hp_max}" if snap.hp_cur is not None else "HP  --"
        self._labels["mp"]["text"] = f"MP  {snap.mp_cur}/{snap.mp_max}" if snap.mp_cur is not None else "MP  --"
        pct = f" ({snap.exp_pct:.2f}%)" if snap.exp_pct is not None else ""
        self._labels["exp"]["text"] = f"EXP {snap.exp_cur}{pct}" if snap.exp_cur is not None else "EXP --"

        # The diff/loss figures below are only meaningful once a full
        # WINDOW_MIN worth of history has accumulated since launch (or since
        # a level-up reset the EXP tracker) -- before that they're based on
        # whatever partial window exists, which understates the real rate.
        # Surface that as a fill countdown rather than let '--' or a
        # too-small number silently look legitimate.
        window_elapsed = time.time() - self._start_time
        window_total = WINDOW_MIN * 60
        if window_elapsed >= window_total:
            self._labels["window"]["text"] = f"Window: full ({WINDOW_MIN}:00)"
        else:
            remaining = window_total - window_elapsed
            self._labels["window"]["text"] = (
                f"Window: filling, {int(remaining // 60)}:{int(remaining % 60):02d} left"
            )

        exp5 = self._exp_diff.window_diff(WINDOW_MIN)
        if exp5 is not None:
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
                pct_s = f" (+{exp5 / total_exp * 100:.2f}%)"
            self._labels["expdiff"]["text"] = f"EXP diff ({WINDOW_MIN}m)  +{exp5:,}{pct_s}"
        else:
            self._labels["expdiff"]["text"] = f"EXP diff ({WINDOW_MIN}m)  --"

        hp_loss = self._hp_loss.window_loss(WINDOW_MIN)
        mp_loss = self._mp_loss.window_loss(WINDOW_MIN)
        hp_loss_s = f"-{hp_loss}" if hp_loss is not None else "--"
        mp_loss_s = f"-{mp_loss}" if mp_loss is not None else "--"
        self._labels["hploss"]["text"] = f"HP loss ({WINDOW_MIN}m)  {hp_loss_s}"
        self._labels["mploss"]["text"] = f"MP loss ({WINDOW_MIN}m)  {mp_loss_s}"

        # Idle only if NONE of HP/MP/EXP have changed recently -- any one of
        # them moving (e.g. taking damage while EXP happens to be flat) counts
        # as activity, not idle.
        all_idle = self._exp_diff.is_idle() and self._hp_loss.is_idle() and self._mp_loss.is_idle()
        self._labels["status"]["text"] = "idle" if all_idle else "tracking"

    def run(self) -> None:
        self.root.mainloop()
