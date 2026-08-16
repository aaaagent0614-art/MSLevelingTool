"""Always-on-top HUD: capture -> OCR -> parse -> session -> redraw, on a timer.

Per-tick timing (2026-08-17 rework, measured against the live game): the
original whole-panel detection+recognition OCR pass was ~600-680ms/tick --
detection (finding text regions) was the entire cost, not recognition. Since
regions.py's FIELD_BOXES already pins down exactly where each field's text
is, detection was pure waste; switched to four small recognition-only calls
(no detection stage) on individually pre-cropped fields, ~15ms each, ~60ms
total. Capture itself is ~3.5ms. `_tick()` now also computes its own elapsed
work time and schedules the next call at `TARGET_MS - elapsed`, floored at
0, instead of the old fixed post-delay (which added TARGET_MS on top of
whatever the work took, so it could never reach the target rate no matter
how fast OCR got) -- this is what actually makes the real cycle approach
TARGET_MS rather than merely bound the *added* delay to it.

UI (2026-08-17 rework): CustomTkinter, three tabs (Live/History/Settings) --
see ~/.claude/notes/maplestory-analyzer/final-spec-2026-08-17.md Section 3
for the full spec. This module still only calls Session's public methods and
reads StatSnapshot/SessionSummary fields -- the capture/OCR/parser engine
(capture.py/ocr.py/parser.py/regions.py) is untouched by this rework, per
the hard UI/engine separation rule in that same doc.
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from typing import Protocol

import customtkinter as ctk

from .ocr import StatPanelOcr
from .parser import StatSnapshot, parse_fields
from .rate import Session, SessionSummary

# The console's codepage (e.g. cp950 Traditional Chinese) can't represent
# every character OCR might misread out of the game's UI -- printing one
# used to raise UnicodeEncodeError and silently kill the tick loop (see
# _tick's try/except below for the other half of this fix). errors="replace"
# swaps unencodable characters for '?' instead of crashing.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TARGET_MS = 500  # target full tick cycle -- 2Hz, per user request
WINDOW_MIN = 1  # default session length in minutes -- set short for testing

# Live-tab row order, fixed regardless of which rows are currently hidden --
# see OverlayApp._apply_visibility().
_LIVE_ROW_ORDER = (
    "level", "hp", "mp", "exp", "startexp", "session",
    "expdiff", "eta", "hploss", "mploss", "status",
)


class PanelSource(Protocol):
    def grab_fields(self) -> dict:
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
        # Instance attribute (not the module constant directly) so the
        # Settings tab can rebind this without Session/rate.py needing to
        # know settings exist -- session length is a UI-layer setting.
        self._window_min = WINDOW_MIN

        # Settings-tab state. Pure UI-layer: the engine keeps tracking every
        # field regardless of what's shown, these only affect _render().
        self._show_hp = True
        self._show_mp = True
        self._show_exp = True
        self._show_exp_pct = True
        self._show_eta = True

        self._last: StatSnapshot = StatSnapshot(None, None, None, None, None, None, None)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("MapleStoryAnalyer")
        self.root.attributes("-topmost", True)
        self.root.geometry("420x600+40+40")

        self._tabview = ctk.CTkTabview(self.root)
        self._tabview.pack(fill="both", expand=True, padx=8, pady=8)
        self._tabview.add("Live")
        self._tabview.add("History")
        self._tabview.add("Settings")

        self._build_live_tab(self._tabview.tab("Live"))
        self._build_history_tab(self._tabview.tab("History"))
        self._build_settings_tab(self._tabview.tab("Settings"))
        self._apply_visibility()

        self._tick()

    # ---- tab construction ------------------------------------------------

    def _build_live_tab(self, parent) -> None:
        self._labels: dict[str, ctk.CTkLabel] = {}
        for key in _LIVE_ROW_ORDER:
            lbl = ctk.CTkLabel(
                parent, text="...", anchor="w",
                font=ctk.CTkFont(family="Consolas", size=13),
            )
            self._labels[key] = lbl

        self._restart_button = ctk.CTkButton(
            parent, text="Restart Session", command=self._on_restart_clicked,
        )

    def _build_history_tab(self, parent) -> None:
        self._history_frame = ctk.CTkScrollableFrame(parent, label_text="Session history")
        self._history_frame.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_settings_tab(self, parent) -> None:
        ctk.CTkLabel(
            parent, text="Session interval (minutes)", anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
        ).pack(fill="x", padx=12, pady=(16, 0))
        self._interval_value_label = ctk.CTkLabel(
            parent, text=f"{self._window_min:.0f} min", anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._interval_value_label.pack(fill="x", padx=12)
        slider = ctk.CTkSlider(
            parent, from_=1, to=60, number_of_steps=59, command=self._on_interval_changed,
        )
        slider.set(self._window_min)
        slider.pack(fill="x", padx=12, pady=(4, 16))

        ctk.CTkLabel(
            parent, text="Display", anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
        ).pack(fill="x", padx=12, pady=(4, 4))

        self._switch_vars: dict[str, tk.BooleanVar] = {}
        for key, label_text, attr in (
            ("hp", "Show HP", "_show_hp"),
            ("mp", "Show MP", "_show_mp"),
            ("exp", "Show EXP", "_show_exp"),
            ("exp_pct", "Show EXP percentage", "_show_exp_pct"),
            ("eta", "Show level-up ETA", "_show_eta"),
        ):
            var = tk.BooleanVar(value=getattr(self, attr))
            self._switch_vars[key] = var
            ctk.CTkSwitch(
                parent, text=label_text, variable=var,
                font=ctk.CTkFont(family="Consolas", size=12),
                command=lambda k=key, a=attr, v=var: self._on_switch_changed(k, a, v),
            ).pack(fill="x", padx=12, pady=4)

    # ---- settings callbacks ------------------------------------------------

    def _on_interval_changed(self, value: float) -> None:
        self._window_min = round(value)
        self._interval_value_label.configure(text=f"{self._window_min} min")
        # Doesn't retroactively affect the currently-running session's
        # already-baked-in target -- takes effect for the *next* session,
        # same as the interval_minutes recorded on SessionSummary.finalize().

    def _on_switch_changed(self, key: str, attr: str, var: tk.BooleanVar) -> None:
        setattr(self, attr, var.get())
        if key != "exp_pct":  # visibility-affecting; exp_pct only changes rendered text
            self._apply_visibility()
        self._render(self._last)  # immediate feedback

    def _apply_visibility(self) -> None:
        visible = {
            "hp": self._show_hp, "mp": self._show_mp, "exp": self._show_exp,
            "eta": self._show_eta, "hploss": self._show_hp, "mploss": self._show_mp,
        }
        for key in _LIVE_ROW_ORDER:
            self._labels[key].pack_forget()
        for key in _LIVE_ROW_ORDER:
            if visible.get(key, True):
                self._labels[key].pack(fill="x", padx=8, pady=2)
        self._restart_button.pack_forget()
        self._restart_button.pack(fill="x", padx=8, pady=(10, 4))

    # ---- tick loop ---------------------------------------------------------

    def _tick(self) -> None:
        # Wrapping the whole tick: any unhandled exception here used to abort
        # this call *before* rescheduling self.root.after(...), permanently
        # freezing the HUD on stale data with no visible error (observed live
        # -- a UnicodeEncodeError from printing a misread OCR character killed
        # the loop, and the HUD sat there silently showing 'idle' while the
        # user kept playing). Every path through this method must reschedule.
        next_delay = TARGET_MS
        try:
            next_delay = self._do_tick()
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] tick error: {e!r}", flush=True)
            self._labels["status"].configure(text=f"error: {e}")
        self.root.after(next_delay, self._tick)

    def _do_tick(self) -> int:
        t0 = time.perf_counter()
        try:
            field_images = self._source.grab_fields()
        except RuntimeError as e:
            # Game window gone (closed/crashed) or minimized -- don't crash
            # the HUD, show it plainly and keep retrying at a slower pace in
            # case it reopens/is restored.
            self._labels["status"].configure(text=str(e))
            return 2000
        field_text = {name: self._ocr.read_field(img) for name, img in field_images.items()}
        snap = parse_fields(field_text)
        print(f"[{time.strftime('%H:%M:%S')}] fields={field_text}", flush=True)
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
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return max(0, int(TARGET_MS - elapsed_ms))

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
        ctk.CTkLabel(
            self._history_frame, text=line, anchor="w", justify="left", wraplength=380,
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(fill="x", padx=4, pady=3)

    # ---- render --------------------------------------------------------

    def _render(self, snap: StatSnapshot) -> None:
        self._labels["level"].configure(text=f"LV {snap.level if snap.level is not None else '?'}")
        self._labels["hp"].configure(
            text=f"HP  {snap.hp_cur}/{snap.hp_max}" if snap.hp_cur is not None else "HP  --"
        )
        self._labels["mp"].configure(
            text=f"MP  {snap.mp_cur}/{snap.mp_max}" if snap.mp_cur is not None else "MP  --"
        )
        pct = f" ({snap.exp_pct:.2f}%)" if snap.exp_pct is not None and self._show_exp_pct else ""
        self._labels["exp"].configure(
            text=f"EXP {snap.exp_cur}{pct}" if snap.exp_cur is not None else "EXP --"
        )

        start_exp = self._session.start_exp
        self._labels["startexp"].configure(
            text=f"Start EXP  {start_exp:,}" if start_exp is not None else "Start EXP  --"
        )

        remaining = max(0.0, self._window_min * 60 - self._session.elapsed())
        self._labels["session"].configure(
            text=f"Session: {int(remaining // 60)}:{int(remaining % 60):02d} left"
        )

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
            pct_s = f" (+{exp_diff / total_exp * 100:.2f}%)" if total_exp and self._show_exp_pct else ""
            self._labels["expdiff"].configure(text=f"EXP diff  +{exp_diff:,}{pct_s}")
        else:
            self._labels["expdiff"].configure(text="EXP diff  --")

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
        self._labels["eta"].configure(
            text=f"Level up ETA  {_fmt_duration(eta_s)}" if eta_s is not None else "Level up ETA  --"
        )

        self._labels["hploss"].configure(text=f"HP loss  -{self._session.hp_loss}")
        self._labels["mploss"].configure(text=f"MP loss  -{self._session.mp_loss}")

        # Idle only if NONE of HP/MP/EXP have changed recently within this
        # session -- any one of them moving counts as activity, not idle.
        idle = self._session.hp_loss == 0 and self._session.mp_loss == 0 and (exp_diff or 0) == 0
        self._labels["status"].configure(text="idle" if idle else "tracking")

    def run(self) -> None:
        self.root.mainloop()
