"""Fullscreen drag-to-select overlay for marking screen regions.

Used by the settings tab's "標記狀態列位置" / "標記楓幣位置" buttons: the user
drags a red rectangle over the game to record the screen-pixel rectangle of the
status bar / meso counter. Kept in its own module to keep overlay.py (the main
HUD) from ballooning further.
"""
from __future__ import annotations

import tkinter as tk


class RegionSelector:
    """Fullscreen, semi-transparent, topmost overlay for marking a screen
    rectangle by dragging the mouse. On release it stores the rectangle in
    screen pixels on `.result` and destroys itself; Esc cancels (result stays
    None). The caller blocks on `root.wait_window(selector.top)` and then
    reads `.result`."""

    def __init__(self, root, title: str, hint: str):
        self.result: tuple[int, int, int, int] | None = None
        self.top = tk.Toplevel(root)
        self.top.title(title)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        # ~25% opaque: the game shows through dimmed, and the red selection
        # rectangle stays visible on top.
        self.top.attributes("-alpha", 0.25)
        self.top.configure(bg="black", cursor="crosshair")
        self._canvas = tk.Canvas(self.top, bg="black", highlightthickness=0, cursor="crosshair")
        self._canvas.pack(fill="both", expand=True)
        self._start: tuple[int, int] | None = None
        self._rect_id: int | None = None
        self.top.update_idletasks()
        self._canvas.create_text(
            self._canvas.winfo_width() // 2, 40, anchor="n", fill="#ffcc00",
            text=f"{title}\n{hint}", font=("Microsoft JhengHei", 18, "bold"),
        )
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", lambda _e: self._finish(None))
        try:
            self.top.grab_set()
        except Exception:
            pass
        self.top.focus_force()

    def _on_press(self, event) -> None:
        self._start = (event.x, event.y)
        if self._rect_id is not None:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff2222", width=3,
        )

    def _on_drag(self, event) -> None:
        if self._start is None or self._rect_id is None:
            return
        x0, y0 = self._start
        self._canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _on_release(self, event) -> None:
        if self._start is None:
            self._finish(None)
            return
        x0, y0 = self._start
        left, right = sorted((x0, event.x))
        top, bottom = sorted((y0, event.y))
        if right - left < 12 or bottom - top < 12:
            self._finish(None)  # too small: accidental click, cancel
            return
        ox = self._canvas.winfo_rootx()
        oy = self._canvas.winfo_rooty()
        self._finish((ox + left, oy + top, ox + right, oy + bottom))

    def _finish(self, region) -> None:
        self.result = region
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()
