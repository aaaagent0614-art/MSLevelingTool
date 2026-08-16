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

import tkinter as tk
from typing import Protocol

from .ocr import StatPanelOcr
from .parser import StatSnapshot, parse_stat_lines
from .rate import DiffTracker, ExpRateTracker

POLL_MS = 500


class PanelSource(Protocol):
    def grab_panel(self):
        ...


class OverlayApp:
    def __init__(self, source: PanelSource):
        self._source = source
        self._ocr = StatPanelOcr()
        self._exp_rate = ExpRateTracker()
        self._hp_diff = DiffTracker()
        self._mp_diff = DiffTracker()

        self._last: StatSnapshot = StatSnapshot(None, None, None, None, None, None)

        self.root = tk.Tk()
        self.root.title("MapleStoryAnalyer")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.geometry("300x150+40+40")

        self._labels: dict[str, tk.Label] = {}
        for key in ("level", "hp", "mp", "exp", "rate1", "rate10", "hpmp1", "status"):
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
        self._exp_rate.record(merged.exp_cur)
        self._hp_diff.record(merged.hp_cur)
        self._mp_diff.record(merged.mp_cur)
        self._render(merged)
        self.root.after(POLL_MS, self._tick)

    def _render(self, snap: StatSnapshot) -> None:
        self._labels["level"]["text"] = f"LV {snap.level if snap.level is not None else '?'}"
        self._labels["hp"]["text"] = f"HP  {snap.hp_cur}/{snap.hp_max}" if snap.hp_cur is not None else "HP  --"
        self._labels["mp"]["text"] = f"MP  {snap.mp_cur}/{snap.mp_max}" if snap.mp_cur is not None else "MP  --"
        self._labels["exp"]["text"] = f"EXP {snap.exp_cur}" if snap.exp_cur is not None else "EXP --"

        rates = self._exp_rate.rates()
        r1 = rates.get(1)
        r10 = rates.get(10)
        self._labels["rate1"]["text"] = f"EXP/hr (1m avg)  {r1:,.0f}" if r1 is not None else "EXP/hr (1m avg)  --"
        self._labels["rate10"]["text"] = f"EXP/hr (10m avg) {r10:,.0f}" if r10 is not None else "EXP/hr (10m avg) --"

        hp1 = self._hp_diff.window_diff(1)
        mp1 = self._mp_diff.window_diff(1)
        hp1_s = f"{hp1:+d}" if hp1 is not None else "--"
        mp1_s = f"{mp1:+d}" if mp1 is not None else "--"
        self._labels["hpmp1"]["text"] = f"HP/MP diff (1m)  {hp1_s} / {mp1_s}"

        self._labels["status"]["text"] = "idle" if self._exp_rate.is_idle() else "tracking"

    def run(self) -> None:
        self.root.mainloop()
