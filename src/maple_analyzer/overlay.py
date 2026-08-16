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

import sys
import time
import tkinter as tk
from typing import Protocol

from .ocr import StatPanelOcr
from .parser import StatSnapshot, parse_stat_lines
from .rate import Session, SessionSummary

# The console's codepage (e.g. cp950 Traditional Chinese) can't represent
# every character OCR might misread out of the game's UI -- printing one
# used to raise UnicodeEncodeError and silently kill the tick loop (see
# _tick's try/except below for the other half of this fix). errors="replace"
# swaps unencodable characters for '?' instead of crashing.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POLL_MS = 500
WINDOW_MIN = 1  # session length in minutes -- set short for testing


class PanelSource(Protocol):
    def grab_panel(self):
        ...


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _fmt_summary(s: SessionSummary, index: int) -> str:
    diff = s.exp_diff
    diff_s = f"+{diff:,}" if diff is not None else "?"
    pct_diff = s.exp_pct_diff
    pct_s = f" (+{pct_diff:.2f}%)" if pct_diff is not None else ""
    start_s = f"{s.start_exp:,}" if s.start_exp is not None else "?"
    end_s = f"{s.end_exp:,}" if s.end_exp is not None else "?"
    dur_min = s.duration_s / 60
    # Duration and the configured interval only differ when a session was
    # manually restarted before its timer fired -- show both then, so a cut-
    # short session doesn't silently look like a full one in the history.
    if s.interval_minutes is not None and abs(dur_min - s.interval_minutes) > 0.05:
        dur_s = f"{dur_min:.1f}m of {s.interval_minutes:.0f}m, restarted early"
    else:
        dur_s = f"{dur_min:.1f}m"
    return (
        f"#{index} ({dur_s}): "
        f"EXP {start_s} -> {end_s} ({diff_s}{pct_s})  "
        f"HP -{s.hp_loss}  MP -{s.mp_loss}"
    )


class OverlayApp:
    def __init__(self, source: PanelSource):
        self._source = source
        self._ocr = StatPanelOcr()
        self._session = Session()
        self._session_history: list[SessionSummary] = []
        # Instance attribute (not the module constant directly) so a future
        # Settings tab can rebind this per the UI plan without touching how
        # sessions are tracked -- session length is a UI-layer setting, not
        # something Session/rate.py needs to know about internally.
        self._window_min = WINDOW_MIN

        self._last: StatSnapshot = StatSnapshot(None, None, None, None, None, None, None)

        self.root = tk.Tk()
        self.root.title("MapleStoryAnalyer")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.geometry("420x600+40+40")

        self._labels: dict[str, tk.Label] = {}
        for key in ("level", "hp", "mp", "exp", "startexp", "session", "expdiff", "eta", "hploss", "mploss", "status"):
            lbl = tk.Label(
                self.root, text="...", fg="#c8ffb0", bg="black",
                font=("Consolas", 11), anchor="w", justify="left",
            )
            lbl.pack(fill="x", padx=8, pady=1)
            self._labels[key] = lbl

        tk.Button(
            self.root, text="Restart Session", command=self._on_restart_clicked,
            bg="#222222", fg="#c8ffb0", activebackground="#333333", activeforeground="#c8ffb0",
            relief="flat", font=("Consolas", 10),
        ).pack(fill="x", padx=8, pady=(6, 2))

        tk.Label(
            self.root, text="Session history", fg="#7fa8ff", bg="black",
            font=("Consolas", 10, "bold"), anchor="w", justify="left",
        ).pack(fill="x", padx=8, pady=(10, 0))

        history_frame = tk.Frame(self.root, bg="black")
        history_frame.pack(fill="both", expand=True, padx=8, pady=(2, 4))
        scrollbar = tk.Scrollbar(history_frame)
        scrollbar.pack(side="right", fill="y")
        self._history_text = tk.Text(
            history_frame, height=6, fg="#7fa8ff", bg="black", font=("Consolas", 9),
            wrap="word", yscrollcommand=scrollbar.set, state="disabled",
        )
        self._history_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self._history_text.yview)

        self._tick()

    def _tick(self) -> None:
        # Wrapping the whole tick: any unhandled exception here used to abort
        # this call *before* rescheduling self.root.after(...), permanently
        # freezing the HUD on stale data with no visible error (observed live
        # -- a UnicodeEncodeError from printing a misread OCR character killed
        # the loop, and the HUD sat there silently showing 'idle' while the
        # user kept playing). Every path through this method must reschedule.
        next_delay = POLL_MS
        try:
            next_delay = self._do_tick()
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] tick error: {e!r}", flush=True)
            self._labels["status"]["text"] = f"error: {e}"
        self.root.after(next_delay, self._tick)

    def _do_tick(self) -> int:
        try:
            frame = self._source.grab_panel()
        except RuntimeError as e:
            # Game window gone (closed/crashed) or minimized -- don't crash
            # the HUD, show it plainly and keep retrying at a slower pace in
            # case it reopens/is restored.
            self._labels["status"]["text"] = str(e)
            return 2000
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
        self._session.record(merged.exp_cur, merged.hp_cur, merged.mp_cur, merged.exp_pct)

        if self._session.elapsed() >= self._window_min * 60:
            self._finalize_and_restart_session()

        self._render(merged)
        return POLL_MS

    def _finalize_and_restart_session(self) -> None:
        # Shared by both the timer check above and the manual restart button
        # -- exactly one code path finalizes+logs+restarts, so a button click
        # landing on the same tick as the timer firing can't double-log.
        # Skip logging if the session never got a real EXP reading (restart
        # clicked immediately after launch, before OCR produced anything --
        # a '? -> ?' entry would just be noise), or if essentially no time
        # passed (rapid double-click on the restart button after real data
        # already exists -- start() carries the last known values forward,
        # so a second click 50ms later would otherwise log a valid-looking
        # but meaningless 0-duration, 0-diff entry).
        if self._session.start_exp is not None and self._session.elapsed() >= 1.0:
            summary = self._session.finalize(self._window_min)
            self._session_history.append(summary)
            line = _fmt_summary(summary, len(self._session_history))
            print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)
            self._append_history_line(line)
        self._session.start()

    def _on_restart_clicked(self) -> None:
        self._finalize_and_restart_session()
        self._render(self._last)  # immediate feedback, don't wait for next tick

    def _append_history_line(self, line: str) -> None:
        self._history_text.configure(state="normal")
        self._history_text.insert("end", line + "\n")
        self._history_text.configure(state="disabled")
        self._history_text.see("end")

    def _render(self, snap: StatSnapshot) -> None:
        self._labels["level"]["text"] = f"LV {snap.level if snap.level is not None else '?'}"
        self._labels["hp"]["text"] = f"HP  {snap.hp_cur}/{snap.hp_max}" if snap.hp_cur is not None else "HP  --"
        self._labels["mp"]["text"] = f"MP  {snap.mp_cur}/{snap.mp_max}" if snap.mp_cur is not None else "MP  --"
        pct = f" ({snap.exp_pct:.2f}%)" if snap.exp_pct is not None else ""
        self._labels["exp"]["text"] = f"EXP {snap.exp_cur}{pct}" if snap.exp_cur is not None else "EXP --"

        start_exp = self._session.start_exp
        self._labels["startexp"]["text"] = f"Start EXP  {start_exp:,}" if start_exp is not None else "Start EXP  --"

        remaining = max(0.0, self._window_min * 60 - self._session.elapsed())
        self._labels["session"]["text"] = f"Session: {int(remaining // 60)}:{int(remaining % 60):02d} left"

        exp_diff = self._session.exp_diff
        # Total EXP required for the current level isn't shown directly by the
        # game, but can be derived from any single tick that has both the
        # absolute value and percentage: total = cur / (pct/100). Anchoring
        # off the current tick (rather than diffing OCR'd percentages
        # directly) is more robust since per-level EXP totals are constant,
        # while independently-read percentages carry their own OCR noise on
        # top of the cur value's.
        total_exp = snap.exp_cur / (snap.exp_pct / 100) if snap.exp_cur and snap.exp_pct else None

        if exp_diff is not None:
            pct_s = f" (+{exp_diff / total_exp * 100:.2f}%)" if total_exp else ""
            self._labels["expdiff"]["text"] = f"EXP diff  +{exp_diff:,}{pct_s}"
        else:
            self._labels["expdiff"]["text"] = "EXP diff  --"

        # ETA to level up: current session's EXP/sec rate, projected against
        # the EXP still needed (total - cur). Needs a few seconds of session
        # data first -- extrapolating off a 1-2 second sample swings wildly.
        elapsed = self._session.elapsed()
        eta_s = None
        if exp_diff and exp_diff > 0 and elapsed > 3 and total_exp and snap.exp_cur:
            rate_per_sec = exp_diff / elapsed
            remaining_exp = total_exp - snap.exp_cur
            if rate_per_sec > 0:
                eta_s = remaining_exp / rate_per_sec
        self._labels["eta"]["text"] = f"Level up ETA  {_fmt_duration(eta_s)}" if eta_s is not None else "Level up ETA  --"

        self._labels["hploss"]["text"] = f"HP loss  -{self._session.hp_loss}"
        self._labels["mploss"]["text"] = f"MP loss  -{self._session.mp_loss}"

        # Idle only if NONE of HP/MP/EXP have changed recently within this
        # session -- any one of them moving counts as activity, not idle.
        idle = self._session.hp_loss == 0 and self._session.mp_loss == 0 and (exp_diff or 0) == 0
        self._labels["status"]["text"] = "idle" if idle else "tracking"

    def run(self) -> None:
        self.root.mainloop()
