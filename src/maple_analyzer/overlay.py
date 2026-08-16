"""Always-on-top HUD: capture -> OCR -> parse -> rate -> redraw, on a timer.

Poll tick is 500ms (2Hz) per spec -- stat numbers don't visibly update faster
than that. OCR runs every tick here (the pixel-diff skip-check from the spec is
not yet implemented -- see README "Not yet built"); this is still cheap enough
for a live demo (~40-90ms/frame on CPU for a crop this small, per the timing
below).
"""
from __future__ import annotations

import tkinter as tk
from typing import Protocol

from .ocr import StatPanelOcr
from .parser import StatSnapshot, parse_stat_lines
from .rate import ExpRateTracker

POLL_MS = 500


class PanelSource(Protocol):
    def grab_panel(self):
        ...


class OverlayApp:
    def __init__(self, source: PanelSource):
        self._source = source
        self._ocr = StatPanelOcr()
        self._rate = ExpRateTracker()

        self.root = tk.Tk()
        self.root.title("MapleStoryAnalyer")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.geometry("300x150+40+40")

        self._labels: dict[str, tk.Label] = {}
        for key in ("level", "hp", "mp", "exp", "rate1", "rate10", "status"):
            lbl = tk.Label(
                self.root, text="...", fg="#c8ffb0", bg="black",
                font=("Consolas", 11), anchor="w", justify="left",
            )
            lbl.pack(fill="x", padx=8, pady=1)
            self._labels[key] = lbl

        self._tick()

    def _tick(self) -> None:
        frame = self._source.grab_panel()
        lines = self._ocr.read(frame)
        snap = parse_stat_lines(lines)
        self._rate.record(snap.exp_cur, snap.level)
        self._render(snap)
        self.root.after(POLL_MS, self._tick)

    def _render(self, snap: StatSnapshot) -> None:
        self._labels["level"]["text"] = f"LV {snap.level if snap.level is not None else '?'}"
        self._labels["hp"]["text"] = f"HP  {snap.hp_cur}/{snap.hp_max}" if snap.hp_cur is not None else "HP  --"
        self._labels["mp"]["text"] = f"MP  {snap.mp_cur}/{snap.mp_max}" if snap.mp_cur is not None else "MP  --"
        pct = f" ({snap.exp_pct:.2f}%)" if snap.exp_pct is not None else ""
        self._labels["exp"]["text"] = f"EXP {snap.exp_cur}{pct}" if snap.exp_cur is not None else "EXP --"

        rates = self._rate.rates()
        r1 = rates.get(1)
        r10 = rates.get(10)
        self._labels["rate1"]["text"] = f"EXP/hr (1m avg)  {r1:,.0f}" if r1 is not None else "EXP/hr (1m avg)  --"
        self._labels["rate10"]["text"] = f"EXP/hr (10m avg) {r10:,.0f}" if r10 is not None else "EXP/hr (10m avg) --"
        self._labels["status"]["text"] = "idle" if self._rate.is_idle() else "tracking"

    def run(self) -> None:
        self.root.mainloop()
