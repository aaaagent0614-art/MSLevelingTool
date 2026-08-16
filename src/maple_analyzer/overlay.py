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

UI (2026-08-17 rework, restyled same day per an HTML design preview the user
approved): CustomTkinter, three tabs (Live/History/Settings). Status/session
timer live in their own strip at the top of Live (a pill + a chip, not just
another text row); stats and session info sit in aligned grids with tabular
numerals; History renders each session as a card, not scrollback text. See
~/.claude/notes/maplestory-analyzer/final-spec-2026-08-17.md Section 3 for
the full spec. This module still only calls Session's public methods and
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

if sys.platform == "win32":
    # Without declaring DPI awareness, Windows scales the whole rendered
    # window as a bitmap after the fact -- Tk still thinks the window is
    # e.g. 420 logical px, but the OS-scaled result doesn't match, and
    # widget content ends up clipped past the visible window edge (observed
    # live: value labels cut off mid-digit). Declaring per-monitor-v2
    # awareness lets Windows and Tk agree on actual pixel dimensions instead.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        pass

TARGET_MS = 500  # target full tick cycle -- 2Hz, per user request
WINDOW_MIN = 1  # default session length in minutes -- set short for testing

# Color tokens, matching the approved HTML design preview.
BG = "#0d1117"
SURFACE = "#161b22"
SURFACE_2 = "#1c2230"
INK = "#e6edf3"
INK_DIM = "#8b96a5"
INK_FAINT = "#5b6577"
ACCENT = "#5eead4"
ACCENT_INK = "#06322c"
HP_COLOR = "#ff6b6b"
MP_COLOR = "#5b9dff"
EXP_COLOR = "#ffc247"
OK_COLOR = "#3ddc84"
TRACK_BG = "#12291f"

_FONT_UI = ("Segoe UI", 13)
_FONT_UI_BOLD = ("Segoe UI", 13, "bold")
_FONT_LABEL = ("Segoe UI", 11, "bold")
_FONT_LABEL_SM = ("Segoe UI", 10, "bold")
_FONT_LABEL_XS = ("Segoe UI", 9, "bold")
_FONT_MONO = ("Consolas", 13)
_FONT_MONO_SM = ("Consolas", 11)
_FONT_MONO_BOLD = ("Consolas", 13, "bold")


class PanelSource(Protocol):
    def grab_fields(self) -> dict:
        ...


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _fmt_loss(loss: int) -> str:
    return f"-{loss}" if loss > 0 else "0"


def _fmt_summary(s: SessionSummary, index: int) -> str:
    diff = s.exp_diff
    diff_s = f"+{diff:,}" if diff is not None else "?"
    pct_diff = s.exp_pct_diff
    pct_s = f" (+{pct_diff:.2f}%)" if pct_diff is not None else ""
    start_s = f"{s.start_exp:,}" if s.start_exp is not None else "?"
    end_s = f"{s.end_exp:,}" if s.end_exp is not None else "?"
    dur_min = s.duration_s / 60
    if s.interval_minutes is not None and abs(dur_min - s.interval_minutes) > 0.05:
        dur_s = f"{dur_min:.1f}m of {s.interval_minutes:.0f}m, restarted early"
    else:
        dur_s = f"{dur_min:.1f}m"
    return (
        f"#{index} ({dur_s}): "
        f"EXP {start_s} -> {end_s} ({diff_s}{pct_s})  "
        f"HP {_fmt_loss(s.hp_loss)}  MP {_fmt_loss(s.mp_loss)}"
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
        self.root.configure(fg_color=BG)
        self.root.geometry("480x640+40+40")

        self._tabview = ctk.CTkTabview(self.root, fg_color=BG, segmented_button_fg_color=SURFACE)
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
        parent.grid_columnconfigure(0, weight=1)

        # Status + session timer: their own strip, not just more stat rows --
        # these are "what's happening right now" facts, distinct in kind
        # (and styling) from the stat readouts below.
        strip = ctk.CTkFrame(parent, fg_color="transparent")
        strip.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 10))
        strip.grid_columnconfigure(0, weight=1)
        strip.grid_columnconfigure(1, weight=0)

        self._status_pill = ctk.CTkLabel(
            strip, text="Tracking", corner_radius=999, fg_color=TRACK_BG,
            text_color=OK_COLOR, font=_FONT_LABEL_SM, padx=14, pady=6,
        )
        self._status_pill.grid(row=0, column=0, sticky="w")

        self._timer_label = ctk.CTkLabel(
            strip, text="--:-- left", corner_radius=999, fg_color=SURFACE_2,
            text_color=INK, font=_FONT_MONO_BOLD, padx=14, pady=6,
        )
        self._timer_label.grid(row=0, column=1, sticky="e")

        # Stat grid: label | mini bar | tabular value, aligned via one grid
        # rather than independently left-justified label:value text.
        stats = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        stats.grid(row=1, column=0, sticky="ew", padx=2, pady=(0, 10))
        stats.grid_columnconfigure(0, weight=0)
        stats.grid_columnconfigure(1, weight=1)
        stats.grid_columnconfigure(2, weight=0)

        self._stat_rows: dict[str, tuple] = {}
        self._value_labels: dict[str, ctk.CTkLabel] = {}
        self._bars: dict[str, ctk.CTkProgressBar] = {}

        def add_stat_row(row: int, key: str, label_text: str, color: str, with_bar: bool) -> None:
            lbl = ctk.CTkLabel(stats, text=label_text, font=_FONT_LABEL, text_color=color, anchor="w")
            lbl.grid(row=row, column=0, sticky="w", padx=(14, 8), pady=9)
            value = ctk.CTkLabel(stats, text="--", font=_FONT_MONO, text_color=INK, anchor="e")
            value.grid(row=row, column=2, sticky="e", padx=(8, 14), pady=9)
            bar = None
            if with_bar:
                bar = ctk.CTkProgressBar(stats, height=6, progress_color=color, fg_color=SURFACE_2)
                bar.set(0)
                bar.grid(row=row, column=1, sticky="ew", padx=6, pady=9)
                self._bars[key] = bar
            self._stat_rows[key] = (lbl, bar, value)
            self._value_labels[key] = value

        add_stat_row(0, "level", "LV", EXP_COLOR, with_bar=False)
        add_stat_row(1, "hp", "HP", HP_COLOR, with_bar=True)
        add_stat_row(2, "mp", "MP", MP_COLOR, with_bar=True)
        add_stat_row(3, "exp", "EXP", EXP_COLOR, with_bar=True)

        # Session info: label | tabular value, same alignment discipline.
        session_card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        session_card.grid(row=2, column=0, sticky="ew", padx=2, pady=(0, 10))
        session_card.grid_columnconfigure(0, weight=1)
        session_card.grid_columnconfigure(1, weight=0)

        self._kv_rows: dict[str, tuple] = {}

        def add_kv_row(row: int, key: str, label_text: str) -> None:
            lbl = ctk.CTkLabel(session_card, text=label_text, font=_FONT_UI, text_color=INK_DIM, anchor="w")
            lbl.grid(row=row, column=0, sticky="w", padx=(14, 8), pady=7)
            value = ctk.CTkLabel(session_card, text="--", font=_FONT_MONO, text_color=INK, anchor="e")
            value.grid(row=row, column=1, sticky="e", padx=(8, 14), pady=7)
            self._kv_rows[key] = (lbl, value)
            self._value_labels[key] = value

        add_kv_row(0, "startexp", "Start EXP")
        add_kv_row(1, "expdiff", "EXP diff")
        add_kv_row(2, "eta", "Level-up ETA")
        add_kv_row(3, "hploss", "HP loss")
        add_kv_row(4, "mploss", "MP loss")

        self._restart_button = ctk.CTkButton(
            parent, text="Restart Session", command=self._on_restart_clicked,
            fg_color=ACCENT, text_color=ACCENT_INK, hover_color="#7ff2e0",
            font=_FONT_UI_BOLD, corner_radius=9, height=38,
        )
        self._restart_button.grid(row=3, column=0, sticky="ew", padx=2, pady=(0, 4))

    def _build_history_tab(self, parent) -> None:
        self._history_frame = ctk.CTkScrollableFrame(parent, fg_color=BG, label_text="")
        self._history_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self._history_empty_label = ctk.CTkLabel(
            self._history_frame, text="No sessions yet", font=_FONT_UI, text_color=INK_FAINT,
        )
        self._history_empty_label.pack(pady=24)

    def _build_settings_tab(self, parent) -> None:
        card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        card.pack(fill="x", padx=2, pady=(2, 0))

        ctk.CTkLabel(
            card, text="SESSION INTERVAL", anchor="w", text_color=INK_DIM, font=_FONT_LABEL,
        ).pack(fill="x", padx=14, pady=(14, 6))
        slider_row = ctk.CTkFrame(card, fg_color="transparent")
        slider_row.pack(fill="x", padx=14, pady=(0, 14))
        slider = ctk.CTkSlider(
            slider_row, from_=1, to=60, number_of_steps=59, command=self._on_interval_changed,
            progress_color=ACCENT, button_color=ACCENT, button_hover_color="#7ff2e0",
        )
        slider.set(self._window_min)
        slider.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self._interval_value_label = ctk.CTkLabel(
            slider_row, text=f"{self._window_min} min", font=_FONT_MONO, text_color=INK, width=52, anchor="e",
        )
        self._interval_value_label.pack(side="right")

        ctk.CTkLabel(
            card, text="DISPLAY", anchor="w", text_color=INK_DIM, font=_FONT_LABEL,
        ).pack(fill="x", padx=14, pady=(4, 6))

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
                card, text=label_text, variable=var, font=_FONT_UI, text_color=INK,
                progress_color=ACCENT, button_color=INK_DIM, button_hover_color=ACCENT,
                command=lambda k=key, a=attr, v=var: self._on_switch_changed(k, a, v),
            ).pack(fill="x", padx=14, pady=8)

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
        visible_stats = {"level": True, "hp": self._show_hp, "mp": self._show_mp, "exp": self._show_exp}
        for key, (lbl, bar, value) in self._stat_rows.items():
            widgets = [lbl, value] + ([bar] if bar else [])
            for w in widgets:
                w.grid() if visible_stats[key] else w.grid_remove()

        visible_kv = {
            "startexp": True, "expdiff": True, "eta": self._show_eta,
            "hploss": self._show_hp, "mploss": self._show_mp,
        }
        for key, (lbl, value) in self._kv_rows.items():
            for w in (lbl, value):
                w.grid() if visible_kv[key] else w.grid_remove()

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
            self._set_status_error(str(e))
        self.root.after(next_delay, self._tick)

    def _do_tick(self) -> int:
        t0 = time.perf_counter()
        try:
            field_images = self._source.grab_fields()
        except RuntimeError as e:
            # Game window gone (closed/crashed) or minimized -- don't crash
            # the HUD, show it plainly and keep retrying at a slower pace in
            # case it reopens/is restored.
            self._set_status_error(str(e))
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
            print(f"[{time.strftime('%H:%M:%S')}] {_fmt_summary(summary, len(self._session_history))}", flush=True)
            self._append_history_card(summary, len(self._session_history))
        self._session.start()

    def _on_restart_clicked(self) -> None:
        self._finalize_and_restart_session()
        self._render(self._last)  # immediate feedback, don't wait for next tick

    def _append_history_card(self, summary: SessionSummary, index: int) -> None:
        self._history_empty_label.pack_forget()

        card = ctk.CTkFrame(self._history_frame, fg_color=SURFACE, corner_radius=10)
        card.pack(fill="x", pady=(0, 8))

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            head, text=f"SESSION #{index}", font=_FONT_LABEL_SM, text_color=INK_FAINT,
        ).pack(side="left")
        dur_min = summary.duration_s / 60
        if summary.interval_minutes is not None and abs(dur_min - summary.interval_minutes) > 0.05:
            dur_text, dur_color = f"{dur_min:.1f}m of {summary.interval_minutes:.0f}m, restarted early", EXP_COLOR
        else:
            dur_text, dur_color = f"{dur_min:.1f}m", INK_DIM
        ctk.CTkLabel(head, text=dur_text, font=_FONT_MONO_SM, text_color=dur_color).pack(side="right")

        rng = ctk.CTkFrame(card, fg_color="transparent")
        rng.pack(fill="x", padx=12, pady=(0, 8))
        start_s = f"{summary.start_exp:,}" if summary.start_exp is not None else "?"
        end_s = f"{summary.end_exp:,}" if summary.end_exp is not None else "?"
        diff = summary.exp_diff
        diff_s = f"+{diff:,}" if diff is not None else "?"
        pct_diff = summary.exp_pct_diff
        ctk.CTkLabel(rng, text=start_s, font=_FONT_MONO, text_color=INK).pack(side="left")
        ctk.CTkLabel(rng, text=" → ", font=_FONT_MONO, text_color=INK_FAINT).pack(side="left")
        ctk.CTkLabel(rng, text=end_s, font=_FONT_MONO, text_color=INK).pack(side="left")
        ctk.CTkLabel(rng, text=f"  {diff_s}", font=_FONT_MONO, text_color=EXP_COLOR).pack(side="left")
        if pct_diff is not None:
            ctk.CTkLabel(rng, text=f" (+{pct_diff:.2f}%)", font=_FONT_MONO_SM, text_color=INK_DIM).pack(side="left")

        mini = ctk.CTkFrame(card, fg_color="transparent")
        mini.pack(fill="x", padx=12, pady=(0, 10))
        mini.grid_columnconfigure((0, 1, 2), weight=1, uniform="mini")

        def mini_stat(col: int, label: str, value: str, color: str) -> None:
            box = ctk.CTkFrame(mini, fg_color=SURFACE_2, corner_radius=7)
            box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0))
            ctk.CTkLabel(box, text=label, font=_FONT_LABEL_XS, text_color=INK_FAINT, anchor="w").pack(
                fill="x", padx=8, pady=(6, 0)
            )
            ctk.CTkLabel(box, text=value, font=_FONT_MONO_SM, text_color=color, anchor="w").pack(
                fill="x", padx=8, pady=(0, 6)
            )

        mini_stat(0, "HP LOSS", _fmt_loss(summary.hp_loss), HP_COLOR if summary.hp_loss > 0 else INK_FAINT)
        mini_stat(1, "MP LOSS", _fmt_loss(summary.mp_loss), MP_COLOR if summary.mp_loss > 0 else INK_FAINT)
        mini_stat(2, "ENDED", time.strftime("%H:%M:%S", time.localtime(summary.end_time)), INK_DIM)

    def _set_status_error(self, text: str) -> None:
        self._status_pill.configure(text=text, fg_color=SURFACE_2, text_color=HP_COLOR)

    # ---- render --------------------------------------------------------

    def _render(self, snap: StatSnapshot) -> None:
        self._value_labels["level"].configure(text=str(snap.level) if snap.level is not None else "--")

        if snap.hp_cur is not None:
            self._value_labels["hp"].configure(text=f"{snap.hp_cur}/{snap.hp_max}")
            if snap.hp_max:
                self._bars["hp"].set(max(0.0, min(1.0, snap.hp_cur / snap.hp_max)))
        else:
            self._value_labels["hp"].configure(text="--")

        if snap.mp_cur is not None:
            self._value_labels["mp"].configure(text=f"{snap.mp_cur}/{snap.mp_max}")
            if snap.mp_max:
                self._bars["mp"].set(max(0.0, min(1.0, snap.mp_cur / snap.mp_max)))
        else:
            self._value_labels["mp"].configure(text="--")

        pct = f"  ({snap.exp_pct:.2f}%)" if snap.exp_pct is not None and self._show_exp_pct else ""
        if snap.exp_cur is not None:
            self._value_labels["exp"].configure(text=f"{snap.exp_cur:,}{pct}")
            if snap.exp_pct is not None:
                self._bars["exp"].set(max(0.0, min(1.0, snap.exp_pct / 100)))
        else:
            self._value_labels["exp"].configure(text="--")

        start_exp = self._session.start_exp
        self._value_labels["startexp"].configure(text=f"{start_exp:,}" if start_exp is not None else "--")

        remaining = max(0.0, self._window_min * 60 - self._session.elapsed())
        self._timer_label.configure(text=f"{int(remaining // 60)}:{int(remaining % 60):02d} left")

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
            pct_s = f"  (+{exp_diff / total_exp * 100:.2f}%)" if total_exp and self._show_exp_pct else ""
            self._value_labels["expdiff"].configure(text=f"+{exp_diff:,}{pct_s}")
        else:
            self._value_labels["expdiff"].configure(text="--")

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
        self._value_labels["eta"].configure(text=_fmt_duration(eta_s) if eta_s is not None else "--")

        hp_loss, mp_loss = self._session.hp_loss, self._session.mp_loss
        self._value_labels["hploss"].configure(
            text=_fmt_loss(hp_loss), text_color=HP_COLOR if hp_loss > 0 else INK_FAINT
        )
        self._value_labels["mploss"].configure(
            text=_fmt_loss(mp_loss), text_color=MP_COLOR if mp_loss > 0 else INK_FAINT
        )

        # Idle only if NONE of HP/MP/EXP have changed recently within this
        # session -- any one of them moving counts as activity, not idle.
        idle = hp_loss == 0 and mp_loss == 0 and (exp_diff or 0) == 0
        if idle:
            self._status_pill.configure(text="Idle", fg_color=SURFACE_2, text_color=INK_DIM)
        else:
            self._status_pill.configure(text="Tracking", fg_color=TRACK_BG, text_color=OK_COLOR)

    def run(self) -> None:
        self.root.mainloop()
