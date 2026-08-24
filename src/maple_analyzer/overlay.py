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

Settings + i18n (2026-08-17, later same day): all UI-layer settings live in
one `Settings` struct (settings.py) instead of scattered instance attributes,
so a future persistence layer can load/save it wholesale. All user-facing
strings route through `self._t(key)` into i18n.py's translation table (English
+ Traditional Chinese, zh default) instead of literals inline here -- static
widgets built once register themselves in `self._i18n_labels` so a language
switch can walk the list and reconfigure every one of them, tabs get renamed
via CTkTabview.rename(), and History cards (built dynamically per session) are
simply torn down and rebuilt from `self._session_history` on switch.
"""
from __future__ import annotations

import contextlib
import dataclasses
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Protocol

import customtkinter as ctk
from PIL import Image

from .i18n import Lang, t
from .ocr import StatPanelOcr
from .parser import StatSnapshot, find_meso_candidate, find_meso_in_region, find_stat_fields, parse_fields, parse_meso
from .rate import Session, SessionSummary
from .settings import Settings


def _open_log_file():
    """Return a writable stream for the windowed (console=False) build's debug
    logging: a real file next to the exe instead of devnull, so the per-tick
    OCR readout can be inspected to diagnose bad recognition."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    path = os.path.join(base, "MSLevelingTool.log")
    try:
        return open(path, "a", encoding="utf-8", errors="replace")
    except OSError:
        return open(os.devnull, "w", encoding="utf-8", errors="replace")

# The console's codepage (e.g. cp950 Traditional Chinese) can't represent
# every character OCR might misread out of the game's UI -- printing one
# used to raise UnicodeEncodeError and silently kill the tick loop (see
# _tick's try/except below for the other half of this fix). errors="replace"
# swaps unencodable characters for '?' instead of crashing.
if sys.stdout is None:
    # PyInstaller's windowed build (console=False) has no stdout/stderr at
    # all (both are None), which crashes not just .reconfigure() below but
    # every bare print() elsewhere in this module (tick-error/debug
    # logging) the moment they run. Swap in a no-op sink so those stay
    # harmless instead of taking down the app.
    #
    # encoding/errors are NOT optional here: open() defaults to the locale
    # codepage with errors='strict', i.e. cp950 on this zh-TW machine. The
    # PP-OCR recognition dictionary is largely *Simplified* Chinese, so a
    # garbage read (game window obscured, floating damage numbers over the
    # panel) routinely produces characters Big5/cp950 cannot encode -- and
    # printing one raised UnicodeEncodeError straight through the sink,
    # killing the tick loop. Same errors="replace" the console path below
    # has always had; the windowed build was the only place missing it.
    sys.stdout = sys.stderr = _open_log_file()
else:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        # Tk writes uncaught-callback tracebacks here, and a traceback can
        # carry the same unencodable OCR text in its repr.
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
SCALE_STEP_PCT = 10
SCALE_MIN_PCT = 50
SCALE_MAX_PCT = 150
# Background locator cadence. Each pass runs full-frame *detection* OCR
# (~600ms+) in a daemon thread to re-find the stat panel fields and the
# meso counter, so the tick thread only ever does cheap recognition reads
# on cached boxes. Every 10 ticks = ~5s. Also what makes the HUD survive
# screen magnifiers (Megapipe): the detected positions track the rescaled
# layout every pass.
LOCATE_INTERVAL_TICKS = 10
# Consecutive locator passes that fail to find the stat panel before
# falling back to the fixed regions.FIELD_BOXES (transient OCR misses
# shouldn't flap the tick between detected and fixed boxes).
LOCATE_EMPTY_LIMIT = 3
# How often (in ticks) manual mode re-reads the meso counter via cheap
# recognition-only OCR on the marked meso region (~15ms) -- no detection, so
# no CPU spike like the locator's periodic detection pass used to cause.
MESO_SCAN_INTERVAL_TICKS = 10

# Live tab's Pause/Resume/Start + Restart button row -- see _apply_run_state.
BUTTON_HEIGHT = 28
STOPPED_BUTTON_WIDTH = 96  # Start alone, centered -- smaller than the two-button width

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

# Chrome text (tabs, headers, buttons, switches, kv labels -- anything that
# can carry translated content) picks its font family from the active
# language via OverlayApp._font(); Segoe UI has no real Traditional Chinese
# glyphs of its own (falls back to a system CJK font Windows picks for you,
# inconsistent with the rest of the UI), so zh uses Microsoft JhengHei
# (Windows' standard Traditional Chinese UI font) instead.
_FONT_FAMILY: dict[Lang, str] = {"en": "Segoe UI", "zh": "Microsoft JhengHei"}

# Fixed English-only chrome that never carries translated text (the game's
# own on-screen abbreviations LV/HP/MP/EXP, and the +/- scale stepper) stays
# on a plain Segoe UI tuple -- no language switching needed for pure ASCII.
_FONT_LABEL = ("Segoe UI", 10, "bold")
_FONT_UI_BOLD = ("Segoe UI", 13, "bold")

# Pure-numeric value labels (HP/MP/EXP readouts, session EXP diffs, history
# card numbers) stay on Consolas regardless of language -- they never render
# CJK text, and Consolas' monospacing is what keeps tabular digits aligned.
_FONT_MONO = ("Consolas", 12)
_FONT_MONO_SM = ("Consolas", 10)
_FONT_MONO_BOLD = ("Consolas", 12, "bold")


class PanelSource(Protocol):
    def grab_fields(self) -> dict:
        ...

    def grab_full(self) -> Image.Image:
        ...


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _fmt_loss(loss: int) -> str:
    return f"-{loss}" if loss > 0 else "0"


def _fmt_summary(s: SessionSummary, index: int) -> str:
    # Console/debug log only, not shown in the UI -- deliberately left in
    # plain English regardless of self._settings.language.
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
    meso_s = f"{s.meso_gained:+,}" if s.meso_gained is not None else "?"
    return (
        f"#{index} ({dur_s}): "
        f"EXP {start_s} -> {end_s} ({diff_s}{pct_s})  "
        f"HP {_fmt_loss(s.hp_loss)}  MP {_fmt_loss(s.mp_loss)}  "
        f"Meso {meso_s}"
    )


class _RegionSelector:
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


class OverlayApp:
    def __init__(self, source: PanelSource):
        self._source = source
        self._ocr = StatPanelOcr()
        self._session = Session()
        self._session_history: list[SessionSummary] = []
        self._settings = Settings()

        self._last: StatSnapshot = StatSnapshot(None, None, None, None, None, None, None)
        # Newest-first: History cards are inserted at index 0 rather than
        # appended, so this tracks the card widgets in display order (index 0
        # = topmost/newest) to pack each new one with before=.
        self._history_cards: list[ctk.CTkFrame] = []
        # Static widgets whose text is a plain translated string (no
        # per-tick data baked in) register themselves here as they're built,
        # so _apply_language() can walk this list and reconfigure every one
        # instead of _build_*_tab needing to be re-run from scratch.
        self._i18n_labels: list[tuple[ctk.CTkBaseClass, str, int, bool]] = []
        # Guards _do_tick's finalize-on-timeout check against the rename
        # dialog's nested event loop -- see _do_tick and _on_rename_clicked.
        self._modal_open = False
        # "running" / "paused" / "stopped" -- see _on_pause_button_clicked and
        # _finalize_and_maybe_stop. "stopped" reached via the timer is
        # implemented by pausing the already-running Session (its clock
        # freezes and record() no-ops, exactly what "stopped" needs) rather
        # than adding a third Session state; starting "stopped" here needs no
        # such call since nothing has fed this fresh Session a tick yet --
        # _do_tick simply doesn't call session.record() until Start is
        # clicked, so it can't begin calibrating or accumulating unasked.
        #
        # Starts stopped rather than tracking immediately on launch -- opening
        # the app (or the .exe) shouldn't silently start a session before the
        # user has actually arrived at the game and decided to track.
        self._run_state = "stopped"
        # Last capture failure message, so _do_tick can log state changes
        # instead of repeating the same line every 2s retry.
        self._last_capture_error: str | None = None
        self._last_client_size: tuple[int, int] | None = None
        # Background locator state (see _try_locate / _apply_locate). A
        # daemon thread periodically runs full-frame detection to find the
        # stat panel fields AND the meso counter, caching their positions
        # so the tick thread only does cheap recognition reads. This is what
        # keeps the HUD correct under screen magnifiers (Megapipe): the
        # detected positions track the magnified layout every pass.
        self._locate_ticks = 0
        self._locate_thread: threading.Thread | None = None
        self._locate_ocr: StatPanelOcr | None = None
        # Detected stat-field boxes as fractions of the client frame:
        # {'LV': (fx, fy, fw, fh), ...}. None until the locator has found
        # the panel (tick falls back to regions.FIELD_BOXES meanwhile).
        self._stat_boxes: dict[str, tuple[float, float, float, float]] | None = None
        self._locate_empty_count = 0
        # Detected meso counter box (fractions), refreshed every locate pass
        # so a dragged inventory window or a zoom change self-corrects.
        self._meso_box: tuple[float, float, float, float] | None = None
        # Manual-region capture source (see settings.manual_*): built lazily
        # when the user marks regions and toggles manual mode on, and used by
        # _active_source() in place of the auto GameWindowCapture.
        self._manual_source = None
        self._manual_calibrated = False
        self._meso_scan_ticks = 0

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        # Applied before the window/widgets are built so the default window
        # size below is already at the configured scale, not built at 100%
        # then rescaled after the fact.
        ctk.set_widget_scaling(self._settings.scale_pct / 100)
        ctk.set_window_scaling(self._settings.scale_pct / 100)

        self.root = ctk.CTk()
        self.root.title("MapleStoryAnalyzer")
        self.root.attributes("-topmost", self._settings.topmost)
        self.root.configure(fg_color=BG)
        self.root.geometry("260x420+40+40")

        # segmented_button_font is deliberately set to the same chrome font
        # as the rest of the UI: without it, CTkTabview falls back to
        # customtkinter's default font (Roboto, which isn't installed on
        # Windows and renders the tab labels in a mismatched fallback).
        self._tabview = ctk.CTkTabview(
            self.root, fg_color=BG, segmented_button_fg_color=SURFACE,
            segmented_button_font=self._font(13, bold=True),
        )
        self._tabview.pack(fill="both", expand=True, padx=8, pady=8)
        # CTkTabview's tab name doubles as its segmented-button label and its
        # internal dict key -- there's no separate "id" to address a tab by,
        # so the translated string itself is the key. rename() (used by
        # _apply_language) swaps the key/label together and keeps the
        # frame/selection intact; this dict just tracks the current name per
        # logical tab so rename() always has both the old and new string.
        self._tab_names = {
            "live": t("tab_live", self._settings.language),
            "history": t("tab_history", self._settings.language),
            "settings": t("tab_settings", self._settings.language),
        }
        for name in self._tab_names.values():
            self._tabview.add(name)

        self._build_live_tab(self._tabview.tab(self._tab_names["live"]))
        self._build_history_tab(self._tabview.tab(self._tab_names["history"]))
        self._build_settings_tab(self._tabview.tab(self._tab_names["settings"]))
        self._tabview.set(self._tab_names["live"])  # CTkTabview defaults to the last-added tab otherwise
        self._apply_visibility()
        self._apply_run_state()

        self._tick()

    # ---- i18n ------------------------------------------------------------

    def _t(self, key: str, **kwargs: object) -> str:
        return t(key, self._settings.language, **kwargs)

    def _localize_error(self, message: str) -> str:
        """Translate the known capture.py RuntimeError messages (game
        minimized / not found) shown via _set_status_error --
        these are routine, expected states, not exceptional ones, so they
        deserve a real translation rather than leaking capture.py's raw
        English text into a zh-language UI. Anything unrecognized (a real
        bug, not a known game-window state) passes through unchanged."""
        if message == "game window is minimized":
            return self._t("status_error_minimized")
        if message.startswith("No window found with title containing"):
            return self._t("status_error_not_found")
        return message

    def _font(self, size: int, bold: bool = False) -> tuple:
        """Chrome-text font at the given size, in the active language's font
        family (see _FONT_FAMILY). Use for any widget that renders translated
        text; pure-numeric value labels should use the module-level
        _FONT_MONO* constants instead (see their docstring)."""
        family = _FONT_FAMILY[self._settings.language]
        return (family, size, "bold") if bold else (family, size)

    def _scale_header_text(self) -> str:
        return self._t("settings_window_scale") + f" — {self._settings.scale_pct}%"

    def _interval_header_text(self) -> str:
        return self._t("settings_session_interval") + f" — {self._settings.window_min} {self._t('unit_min')}"

    def _i18n(self, widget: ctk.CTkBaseClass, key: str, size: int, bold: bool = True) -> ctk.CTkBaseClass:
        """Set a widget's text + font from a translation key and register it
        for re-translation on language switch. Use for any widget whose text
        is *only* the translated string (no per-tick value baked in) --
        widgets that mix in live data (timer, status pill, kv values) instead
        call self._t(...)/self._font(...) directly wherever they're
        re-rendered every tick."""
        widget.configure(text=self._t(key), font=self._font(size, bold))
        self._i18n_labels.append((widget, key, size, bold))
        return widget

    def _apply_language(self, lang: Lang) -> None:
        if lang == self._settings.language:
            return
        self._settings.language = lang

        for logical, key in (("live", "tab_live"), ("history", "tab_history"), ("settings", "tab_settings")):
            old_name = self._tab_names[logical]
            new_name = self._t(key)
            if new_name != old_name:
                self._tabview.rename(old_name, new_name)
                self._tab_names[logical] = new_name

        # Tab labels are translated text, so they follow the language font
        # like every other chrome label (see the construction-site comment
        # on _tabview for why this matters).
        self._tabview.configure(segmented_button_font=self._font(13, bold=True))

        for widget, key, size, bold in self._i18n_labels:
            widget.configure(text=self._t(key), font=self._font(size, bold))

        self._status_pill.configure(font=self._font(9, bold=True))
        self._timer_label.configure(font=self._font(10, bold=True))
        self._scale_header_label.configure(text=self._scale_header_text(), font=self._font(11, bold=True))
        self._interval_header_label.configure(text=self._interval_header_text(), font=self._font(11, bold=True))
        # _pause_button's text depends on _run_state, not just language, so it
        # isn't in _i18n_labels -- _apply_run_state() re-derives it from
        # scratch, which also happens to pick up the new language/font.
        self._apply_run_state()

        # History cards mix translated chrome (SESSION #N, HP/MP LOSS) with
        # per-session data and aren't worth tracking widget-by-widget --
        # tearing down and rebuilding from the data we already keep is
        # simpler and this only happens on an explicit language switch, and
        # _append_history_card already picks up the new language/font.
        self._rebuild_history_cards()
        self._refresh_manual_status()

        self._render(self._last)  # refreshes status pill / timer text immediately

    # ---- tab construction ------------------------------------------------

    def _build_live_tab(self, parent) -> None:
        # Scrollable so the window can stay compact at any scale while every
        # block stays reachable -- a scrollbar appears on the right when the
        # content is taller than the tab (per user request 2026-08-24: at
        # 120% scale the Start button used to sit below the fold with no
        # way to reach it).
        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG, label_text="")
        scroll.pack(fill="both", expand=True)
        parent = scroll
        parent.grid_columnconfigure(0, weight=1)

        # Status + session timer share one row, both shrunk down (smaller
        # font/padding than the rest of the chrome) so a longer localized
        # status string (the capture-error states from _localize_error run
        # much longer than "Tracking"/"追蹤中") still leaves room for the
        # timer instead of pushing the Restart button out of the window.
        # wraplength caps the status pill's own width so it wraps to a
        # second line rather than growing sideways into the timer's column.
        strip = ctk.CTkFrame(parent, fg_color="transparent")
        strip.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 3))
        strip.grid_columnconfigure(0, weight=1)
        strip.grid_columnconfigure(1, weight=0)

        self._status_pill = ctk.CTkLabel(
            strip, text=self._t("status_tracking"), corner_radius=999, fg_color=TRACK_BG,
            text_color=OK_COLOR, font=self._font(9, bold=True), padx=8, pady=2,
            anchor="w", justify="left", wraplength=110,
        )
        self._status_pill.grid(row=0, column=0, sticky="w")

        # Mixes translated chrome ("left"/"剩餘") with the countdown digits,
        # so it needs the language-aware font (self._font), not the fixed
        # digits-only _FONT_MONO_BOLD -- unlike the pure-numeric value labels.
        self._timer_label = ctk.CTkLabel(
            strip, text="--:--", corner_radius=999, fg_color=SURFACE_2,
            text_color=INK, font=self._font(10, bold=True), padx=8, pady=2,
        )
        self._timer_label.grid(row=0, column=1, sticky="e")

        # Stat grid: label | mini bar | tabular value, aligned via one grid
        # rather than independently left-justified label:value text. Labels
        # (LV/HP/MP/EXP) are the game's own on-screen abbreviations -- see
        # i18n.py's docstring for why these are not translated.
        stats = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        stats.grid(row=1, column=0, sticky="ew", padx=2, pady=(0, 3))
        stats.grid_columnconfigure(0, weight=0)
        stats.grid_columnconfigure(1, weight=1)
        stats.grid_columnconfigure(2, weight=0)

        self._stat_rows: dict[str, tuple] = {}
        self._value_labels: dict[str, ctk.CTkLabel] = {}
        self._bars: dict[str, ctk.CTkProgressBar] = {}

        def add_stat_row(row: int, key: str, label_text: str, color: str, with_bar: bool) -> None:
            lbl = ctk.CTkLabel(stats, text=label_text, font=_FONT_LABEL, text_color=color, anchor="w")
            lbl.grid(row=row, column=0, sticky="w", padx=(12, 6), pady=0)
            value = ctk.CTkLabel(stats, text="--", font=_FONT_MONO, text_color=INK, anchor="e")
            value.grid(row=row, column=2, sticky="e", padx=(6, 12), pady=0)
            bar = None
            if with_bar:
                bar = ctk.CTkProgressBar(stats, height=5, progress_color=color, fg_color=SURFACE_2)
                bar.set(0)
                bar.grid(row=row, column=1, sticky="ew", padx=6, pady=0)
                self._bars[key] = bar
            self._stat_rows[key] = (lbl, bar, value)
            self._value_labels[key] = value

        add_stat_row(0, "level", "LV", EXP_COLOR, with_bar=False)
        add_stat_row(1, "hp", "HP", HP_COLOR, with_bar=True)
        add_stat_row(2, "mp", "MP", MP_COLOR, with_bar=True)
        add_stat_row(3, "exp", "EXP", EXP_COLOR, with_bar=True)

        # Session info: label | tabular value, same alignment discipline.
        # Split into two cards with clear spacing between blocks (per user
        # request 2026-08-24): EXP block (start/diff/ETA/projection) and a
        # losses+meso block (HP/MP loss, start/current meso).
        self._kv_rows: dict[str, tuple] = {}

        def add_kv_row(card, row: int, key: str, i18n_key: str) -> None:
            lbl = ctk.CTkLabel(card, text_color=INK_DIM, anchor="w")
            self._i18n(lbl, i18n_key, size=11, bold=False)
            lbl.grid(row=row, column=0, sticky="w", padx=(12, 6), pady=0)
            value = ctk.CTkLabel(card, text="--", font=_FONT_MONO, text_color=INK, anchor="e")
            value.grid(row=row, column=1, sticky="e", padx=(6, 12), pady=0)
            self._kv_rows[key] = (lbl, value)
            self._value_labels[key] = value

        exp_card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        exp_card.grid(row=2, column=0, sticky="ew", padx=2, pady=(0, 6))
        exp_card.grid_columnconfigure(0, weight=1)
        exp_card.grid_columnconfigure(1, weight=0)
        add_kv_row(exp_card, 0, "startexp", "kv_start_exp")
        add_kv_row(exp_card, 1, "expdiff", "kv_exp_diff")
        add_kv_row(exp_card, 2, "eta", "kv_eta")
        add_kv_row(exp_card, 3, "projexp", "kv_proj_exp")

        loss_card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        loss_card.grid(row=3, column=0, sticky="ew", padx=2, pady=(0, 6))
        loss_card.grid_columnconfigure(0, weight=1)
        loss_card.grid_columnconfigure(1, weight=0)
        add_kv_row(loss_card, 0, "hploss", "kv_hp_loss")
        add_kv_row(loss_card, 1, "mploss", "kv_mp_loss")
        # Meso rows always exist; they show '--' until track_meso is on AND
        # the corresponding inventory readings have landed (see
        # Session.record_meso).
        add_kv_row(loss_card, 2, "mesostart", "kv_meso_start")
        add_kv_row(loss_card, 3, "mesocurrent", "kv_meso_current")

        # Two buttons share one row: the left one cycles Pause/Resume/Start
        # depending on _run_state (see _on_pause_button_clicked), the right
        # one is the unconditional manual Restart -- hidden only in the
        # "stopped" state, where Start already covers beginning a new
        # session and a separate Restart would have nothing to restart from.
        button_row = ctk.CTkFrame(parent, fg_color="transparent")
        button_row.grid(row=4, column=0, sticky="ew", padx=2, pady=(0, 2))
        button_row.grid_columnconfigure(0, weight=1)
        button_row.grid_columnconfigure(1, weight=1)

        self._pause_button = ctk.CTkButton(
            button_row, command=self._on_pause_button_clicked,
            fg_color=SURFACE_2, hover_color=TRACK_BG, text_color=INK,
            corner_radius=9, height=BUTTON_HEIGHT,
        )
        self._pause_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self._restart_button = ctk.CTkButton(
            button_row, command=self._on_restart_clicked,
            fg_color=ACCENT, text_color=ACCENT_INK, hover_color="#7ff2e0",
            corner_radius=9, height=BUTTON_HEIGHT,
        )
        self._i18n(self._restart_button, "restart_button", size=13, bold=True)
        self._restart_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))

    def _build_history_tab(self, parent) -> None:
        self._clear_history_button = ctk.CTkButton(
            parent, command=self._on_clear_history_clicked,
            fg_color=SURFACE_2, hover_color=TRACK_BG, text_color=HP_COLOR,
            corner_radius=9, height=28,
        )
        self._i18n(self._clear_history_button, "history_clear_button", size=11, bold=True)
        self._clear_history_button.pack(fill="x", padx=2, pady=(2, 4))

        self._history_frame = ctk.CTkScrollableFrame(parent, fg_color=BG, label_text="")
        self._history_frame.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        self._history_empty_label = ctk.CTkLabel(
            self._history_frame, text_color=INK_FAINT,
        )
        self._i18n(self._history_empty_label, "history_empty", size=13, bold=False)
        self._history_empty_label.pack(pady=24)

    def _build_settings_tab(self, parent) -> None:
        # Scrollable: at some WINDOW SCALE values the settings content is
        # taller than the window, and a plain .pack() into the tab would
        # just clip the overflow with no way to reach it -- a scrollbar
        # keeps every option reachable regardless of scale/window size.
        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG, label_text="")
        scroll.pack(fill="both", expand=True)

        window_card = ctk.CTkFrame(scroll, fg_color=SURFACE, corner_radius=12)
        window_card.pack(fill="x", padx=2, pady=(2, 3))

        # Value lives in the section header, not squeezed into the control
        # row -- at narrow window widths (esp. with the scrollbar eating
        # horizontal space) a fixed-width label at the end of a packed row
        # was getting clipped to invisible. The header always has room.
        self._scale_header_label = ctk.CTkLabel(
            window_card, text=self._scale_header_text(),
            anchor="w", text_color=INK_DIM, font=self._font(10, bold=True),
        )
        self._scale_header_label.pack(fill="x", padx=12, pady=(5, 0))
        scale_row = ctk.CTkFrame(window_card, fg_color="transparent")
        scale_row.pack(fill="x", padx=12, pady=(0, 3))
        # A +/- stepper instead of a slider -- a small draggable handle at
        # this widget size was fiddly to land on an exact value; discrete
        # SCALE_STEP_PCT taps are precise and don't need fine motor control.
        ctk.CTkButton(
            scale_row, text="-", width=36, command=lambda: self._on_scale_step(-SCALE_STEP_PCT),
            fg_color=SURFACE_2, hover_color=TRACK_BG, text_color=INK, font=_FONT_UI_BOLD,
        ).pack(side="left")
        ctk.CTkButton(
            scale_row, text="+", width=36, command=lambda: self._on_scale_step(SCALE_STEP_PCT),
            fg_color=SURFACE_2, hover_color=TRACK_BG, text_color=INK, font=_FONT_UI_BOLD,
        ).pack(side="left", padx=(6, 0))

        self._topmost_var = tk.BooleanVar(value=self._settings.topmost)
        self._i18n(ctk.CTkSwitch(
            window_card, variable=self._topmost_var, text_color=INK,
            progress_color=ACCENT, button_color=INK_DIM, button_hover_color=ACCENT,
            command=self._on_topmost_changed,
        ), "settings_always_on_top", size=11, bold=False).pack(fill="x", padx=12, pady=(0, 3))

        lang_row = ctk.CTkFrame(window_card, fg_color="transparent")
        lang_row.pack(fill="x", padx=12, pady=(0, 4))
        self._i18n(
            ctk.CTkLabel(lang_row, anchor="w", text_color=INK_DIM), "settings_language", size=10, bold=True
        ).pack(side="left")
        self._lang_button = ctk.CTkSegmentedButton(
            lang_row, values=["中文", "EN"], command=self._on_language_button_changed,
            selected_color=ACCENT, selected_hover_color="#7ff2e0", text_color=INK,
        )
        self._lang_button.set("中文" if self._settings.language == "zh" else "EN")
        self._lang_button.pack(side="right")

        card = ctk.CTkFrame(scroll, fg_color=SURFACE, corner_radius=12)
        card.pack(fill="x", padx=2, pady=(0, 0))

        self._interval_header_label = ctk.CTkLabel(
            card, text=self._interval_header_text(),
            anchor="w", text_color=INK_DIM, font=self._font(10, bold=True),
        )
        self._interval_header_label.pack(fill="x", padx=12, pady=(5, 0))
        slider_row = ctk.CTkFrame(card, fg_color="transparent")
        slider_row.pack(fill="x", padx=12, pady=(0, 2))
        slider = ctk.CTkSlider(
            slider_row, from_=1, to=60, number_of_steps=59, command=self._on_interval_changed,
            progress_color=ACCENT, button_color=ACCENT, button_hover_color="#7ff2e0",
        )
        slider.set(self._settings.window_min)
        slider.pack(fill="x", expand=True)

        self._i18n(
            ctk.CTkLabel(card, anchor="w", text_color=INK_DIM), "settings_display", size=10, bold=True
        ).pack(fill="x", padx=12, pady=(2, 0))

        self._switch_vars: dict[str, tk.BooleanVar] = {}
        for key, i18n_key, attr in (
            ("hp", "settings_show_hp", "show_hp"),
            ("mp", "settings_show_mp", "show_mp"),
            ("exp", "settings_show_exp", "show_exp"),
            ("exp_pct", "settings_show_exp_pct", "show_exp_pct"),
            ("eta", "settings_show_eta", "show_eta"),
            ("proj_exp", "settings_show_proj_exp", "show_proj_exp"),
        ):
            var = tk.BooleanVar(value=getattr(self._settings, attr))
            self._switch_vars[key] = var
            self._i18n(ctk.CTkSwitch(
                card, variable=var, text_color=INK,
                progress_color=ACCENT, button_color=INK_DIM, button_hover_color=ACCENT,
                command=lambda k=key, a=attr, v=var: self._on_switch_changed(k, a, v),
            ), i18n_key, size=11, bold=False).pack(fill="x", padx=12, pady=0)

        # SESSION: behaviour switches, not display toggles -- neither one
        # hides/shows a widget, so they bypass _on_switch_changed/_apply_visibility
        # entirely (see _on_auto_stop_changed/_on_save_on_restart_changed).
        self._i18n(
            ctk.CTkLabel(card, anchor="w", text_color=INK_DIM), "settings_session", size=10, bold=True
        ).pack(fill="x", padx=12, pady=(3, 0))

        self._auto_stop_var = tk.BooleanVar(value=self._settings.auto_stop)
        self._i18n(ctk.CTkSwitch(
            card, variable=self._auto_stop_var, text_color=INK,
            progress_color=ACCENT, button_color=INK_DIM, button_hover_color=ACCENT,
            command=self._on_auto_stop_changed,
        ), "settings_auto_stop", size=11, bold=False).pack(fill="x", padx=12, pady=0)

        self._save_on_restart_var = tk.BooleanVar(value=self._settings.save_on_restart)
        self._i18n(ctk.CTkSwitch(
            card, variable=self._save_on_restart_var, text_color=INK,
            progress_color=ACCENT, button_color=INK_DIM, button_hover_color=ACCENT,
            command=self._on_save_on_restart_changed,
        ), "settings_save_on_restart", size=11, bold=False).pack(fill="x", padx=12, pady=(0, 4))

        # MESO: found by the background locator's full-frame detection pass
        # (the same one that locates the stat panel), so it costs nothing
        # extra. Needs the user to open the inventory at both session ends
        # to land the endpoint readings. Default on (2026-08-24).
        self._i18n(
            ctk.CTkLabel(card, anchor="w", text_color=INK_DIM), "settings_track_meso", size=10, bold=True
        ).pack(fill="x", padx=12, pady=(3, 0))

        self._track_meso_var = tk.BooleanVar(value=self._settings.track_meso)
        self._i18n(ctk.CTkSwitch(
            card, variable=self._track_meso_var, text_color=INK,
            progress_color=ACCENT, button_color=INK_DIM, button_hover_color=ACCENT,
            command=self._on_track_meso_changed,
        ), "settings_track_meso", size=11, bold=False).pack(fill="x", padx=12, pady=0)
        self._i18n(
            ctk.CTkLabel(card, anchor="w", wraplength=210, justify="left", text_color=INK_FAINT),
            "settings_track_meso_hint", size=9, bold=False,
        ).pack(fill="x", padx=12, pady=(0, 3))

        # MANUAL POSITION: mark the status bar and the meso counter with the
        # mouse so the HUD OCRs those exact screen regions -- what makes it
        # work under a screen magnifier (Magpie), where the game window's own
        # rect no longer matches what is visible on screen.
        manual_card = ctk.CTkFrame(scroll, fg_color=SURFACE, corner_radius=12)
        manual_card.pack(fill="x", padx=2, pady=(4, 2))

        self._i18n(
            ctk.CTkLabel(manual_card, anchor="w", text_color=INK_DIM), "settings_manual", size=10, bold=True
        ).pack(fill="x", padx=12, pady=(5, 0))

        self._use_manual_var = tk.BooleanVar(value=self._settings.use_manual)
        self._i18n(ctk.CTkSwitch(
            manual_card, variable=self._use_manual_var, text_color=INK,
            progress_color=ACCENT, button_color=INK_DIM, button_hover_color=ACCENT,
            command=self._on_use_manual_changed,
        ), "settings_use_manual", size=11, bold=False).pack(fill="x", padx=12, pady=(0, 3))

        self._stat_region_button = ctk.CTkButton(
            manual_card, command=self._on_set_stat_region,
            fg_color=SURFACE_2, hover_color=TRACK_BG, text_color=INK,
            corner_radius=9, height=28,
        )
        self._i18n(self._stat_region_button, "settings_set_stat_region", size=11, bold=True)
        self._stat_region_button.pack(fill="x", padx=12, pady=(0, 3))

        self._meso_region_button = ctk.CTkButton(
            manual_card, command=self._on_set_meso_region,
            fg_color=SURFACE_2, hover_color=TRACK_BG, text_color=INK,
            corner_radius=9, height=28,
        )
        self._i18n(self._meso_region_button, "settings_set_meso_region", size=11, bold=True)
        self._meso_region_button.pack(fill="x", padx=12, pady=(0, 3))

        self._manual_status_label = ctk.CTkLabel(
            manual_card, anchor="w", wraplength=210, justify="left", text_color=INK_FAINT,
        )
        self._manual_status_label.pack(fill="x", padx=12, pady=(0, 5))
        self._refresh_manual_status()

    # ---- settings callbacks ------------------------------------------------

    def _on_scale_step(self, delta: int) -> None:
        pct = max(SCALE_MIN_PCT, min(SCALE_MAX_PCT, self._settings.scale_pct + delta))
        if pct == self._settings.scale_pct:
            return
        self._settings.scale_pct = pct
        self._scale_header_label.configure(text=self._scale_header_text())
        # CTk's own scaling knobs: widget_scaling resizes fonts/padding/etc,
        # window_scaling resizes the geometry set via .geometry() -- both are
        # needed together, otherwise widgets end up mismatched against the
        # window size. Both apply live to the already-open root window.
        factor = pct / 100
        ctk.set_widget_scaling(factor)
        ctk.set_window_scaling(factor)

    def _on_topmost_changed(self) -> None:
        self._settings.topmost = self._topmost_var.get()
        self.root.attributes("-topmost", self._settings.topmost)

    def _on_language_button_changed(self, value: str) -> None:
        self._apply_language("zh" if value == "中文" else "en")

    def _on_interval_changed(self, value: float) -> None:
        self._settings.window_min = round(value)
        self._interval_header_label.configure(text=self._interval_header_text())
        # Doesn't retroactively affect the currently-running session's
        # already-baked-in target -- takes effect for the *next* session,
        # same as the interval_minutes recorded on SessionSummary.finalize().

    def _on_switch_changed(self, key: str, attr: str, var: tk.BooleanVar) -> None:
        setattr(self._settings, attr, var.get())
        if key != "exp_pct":  # visibility-affecting; exp_pct only changes rendered text
            self._apply_visibility()
        self._render(self._last)  # immediate feedback

    def _on_auto_stop_changed(self) -> None:
        self._settings.auto_stop = self._auto_stop_var.get()

    def _on_save_on_restart_changed(self) -> None:
        self._settings.save_on_restart = self._save_on_restart_var.get()

    def _on_track_meso_changed(self) -> None:
        self._settings.track_meso = self._track_meso_var.get()
        # The live tab's Meso row is gated on this setting (see
        # _apply_visibility) -- show/hide it immediately.
        self._apply_visibility()

    def _rebuild_manual_source(self) -> None:
        """Build/clear the manual-region capture source from the current
        settings. Called whenever use_manual or a region changes."""
        s = self._settings
        if s.use_manual and s.manual_stat_region is not None:
            from .capture import ManualScreenCapture

            self._manual_source = ManualScreenCapture(s.manual_stat_region, s.manual_meso_region)
        else:
            self._manual_source = None
        # Regions/toggle changed: invalidate the one-shot calibration and force
        # the next tick to re-run detection immediately.
        self._manual_calibrated = False
        self._stat_boxes = None
        self._locate_ticks = LOCATE_INTERVAL_TICKS

    def _active_source(self):
        """The capture source for this tick/locate pass: the manual screen
        region source when manual mode is on, otherwise the auto source."""
        return self._manual_source if self._manual_source is not None else self._source

    def _refresh_manual_status(self) -> None:
        """Update the settings status line to show whether each manual region
        has been marked (with its screen coordinates when set)."""
        s = self._settings

        def line(region, set_key, unset_key) -> str:
            if region is None:
                return self._t(unset_key)
            l, t, r, b = region
            return f"{self._t(set_key)} ({l},{t})-({r},{b})"

        self._manual_status_label.configure(text="\n".join([
            line(s.manual_stat_region, "settings_stat_region_set", "settings_stat_region_unset"),
            line(s.manual_meso_region, "settings_meso_region_set", "settings_meso_region_unset"),
            *self._manual_feedback_lines(),
        ]))

    def _manual_feedback_lines(self) -> list[str]:
        """One extra status line describing the manual-mode detection result,
        so the user knows right away whether their marked box is good."""
        if not (self._settings.use_manual and self._settings.manual_stat_region is not None):
            return []
        if not self._manual_calibrated:
            return [self._t("settings_manual_detecting")]
        if self._stat_boxes:
            return [self._t("settings_manual_detected", n=len(self._stat_boxes))]
        return [self._t("settings_manual_detect_failed")]

    def _on_use_manual_changed(self) -> None:
        self._settings.use_manual = self._use_manual_var.get()
        self._rebuild_manual_source()

    def _on_set_stat_region(self) -> None:
        self._select_region(self._on_stat_region_selected, "settings_set_stat_region")

    def _on_set_meso_region(self) -> None:
        self._select_region(self._on_meso_region_selected, "settings_set_meso_region")

    def _on_stat_region_selected(self, region) -> None:
        self._settings.manual_stat_region = region
        self._refresh_manual_status()
        self._rebuild_manual_source()

    def _on_meso_region_selected(self, region) -> None:
        self._settings.manual_meso_region = region
        self._refresh_manual_status()
        self._rebuild_manual_source()

    def _select_region(self, callback, title_key: str) -> None:
        """Open the fullscreen region selector and hand the result to `callback`
        when the user finishes (None if they cancel). Blocks on wait_window.
        Wrapped in _modal() so a session can't finalize mid-selection."""
        with self._modal():
            selector = _RegionSelector(self.root, self._t(title_key), self._t("region_selector_hint"))
            self.root.wait_window(selector.top)
            if selector.result is not None:
                callback(selector.result)

    def _apply_visibility(self) -> None:
        s = self._settings
        visible_stats = {"level": True, "hp": s.show_hp, "mp": s.show_mp, "exp": s.show_exp}
        for key, (lbl, bar, value) in self._stat_rows.items():
            widgets = [lbl, value] + ([bar] if bar else [])
            for w in widgets:
                w.grid() if visible_stats[key] else w.grid_remove()

        visible_kv = {
            "startexp": True, "expdiff": True, "eta": s.show_eta,
            "projexp": s.show_proj_exp, "hploss": s.show_hp, "mploss": s.show_mp,
            "mesostart": s.track_meso, "mesocurrent": s.track_meso,
        }
        for key, (lbl, value) in self._kv_rows.items():
            for w in (lbl, value):
                w.grid() if visible_kv[key] else w.grid_remove()

    # ---- tick loop ---------------------------------------------------------

    def _tick(self) -> None:
        # Every path through this method must reschedule -- this loop is the
        # only thing driving the HUD, so an exception escaping before
        # self.root.after(...) freezes it permanently on stale data. The
        # window itself stays responsive, which makes the failure especially
        # confusing: buttons still click, Restart Session still "works", and
        # nothing ever updates again.
        #
        # Hence both the except *and* the finally. The original try/except
        # wasn't enough on its own: in the release .exe an unencodable OCR
        # character raised UnicodeEncodeError out of _do_tick's debug print,
        # and the handler's own `print(... {e!r})` re-raised on the same
        # unencodable text, so the reschedule below was never reached (see
        # the stdout-sink note at the top of this module for that trigger's
        # actual fix, and _log for why logging can no longer raise at all).
        # `finally` is what makes the loop survive the *next* such bug.
        next_delay = TARGET_MS
        try:
            next_delay = self._do_tick()
        except Exception as e:
            self._log(f"[{time.strftime('%H:%M:%S')}] tick error: {e!r}")
            with contextlib.suppress(Exception):
                self._set_status_error(self._t("status_error_unknown", detail=str(e)))
        finally:
            self.root.after(next_delay, self._tick)

    @staticmethod
    def _log(message: str) -> None:
        """Debug logging must never be able to kill the tick loop -- it is the
        least important thing this app does and has already taken the whole
        HUD down once (see _tick)."""
        with contextlib.suppress(Exception):
            print(message, flush=True)

    def _do_tick(self) -> int:
        t0 = time.perf_counter()

        # Kick the background locator (throttled inside). It re-finds the
        # stat panel fields + meso counter on the full frame so reads stay
        # correct under screen magnifiers (Megapipe) and a dragged
        # inventory window.
        self._try_locate()

        try:
            if self._stat_boxes:
                # Located path (Megapipe-safe): crop each detected field box
                # out of a single full-frame grab and OCR them recognition-
                # only, exactly like grab_fields does for the fixed boxes.
                # Detection boxes are tight, so pad each crop slightly.
                frame = self._active_source().grab_full()
                fw, fh = frame.size
                field_images = {}
                for name, (fx, fy, fw2, fh2) in self._stat_boxes.items():
                    x = max(0, int(fx * fw) - 2)
                    y = max(0, int(fy * fh) - 2)
                    w = max(1, int(fw2 * fw) + 4)
                    h = max(1, int(fh2 * fh) + 4)
                    field_images[name] = frame.crop((x, y, min(fw, x + w), min(fh, y + h)))
            else:
                field_images = self._active_source().grab_fields()
        except RuntimeError as e:
            # Game window gone (closed/crashed), minimized, or the stat panel
            # is covered by another window -- don't crash the HUD, show it
            # plainly and keep retrying at a slower pace in case it clears.
            #
            # Logged on *transition* only: this path produces no other output,
            # so a persistently obscured panel used to leave a completely
            # empty log with nothing to diagnose from -- but logging every
            # 2s retry would bury the real ticks.
            if str(e) != self._last_capture_error:
                self._log(f"[{time.strftime('%H:%M:%S')}] capture unavailable: {e}")
                self._last_capture_error = str(e)
            self._set_status_error(self._localize_error(str(e)))
            # The session clock is wall-clock time (Session.elapsed()), not
            # tick-driven, so it keeps running even while OCR can't read the
            # panel (game window covered, alt-tabbed away, minimized). Both
            # of these used to be skipped entirely on this path: the timer
            # chip froze at its last-rendered text even though the real
            # countdown kept going underneath, and a session whose window
            # stayed blocked past its interval would never auto-finalize at
            # all, silently overrunning forever.
            self._update_timer_label()
            self._maybe_finalize_on_timeout()
            return 2000
        if self._last_capture_error is not None:
            self._log(f"[{time.strftime('%H:%M:%S')}] capture recovered")
            self._last_capture_error = None

        # Every crop is scaled from the client size (regions.py), so a log
        # without it can't explain a bad read -- and a mid-session resize is
        # exactly the kind of thing that moves the panel out from under the
        # boxes. Logged once at startup and again on any change.
        client_size = getattr(self._active_source(), "client_size", None)
        if client_size is not None and client_size != self._last_client_size:
            self._log(f"[{time.strftime('%H:%M:%S')}] client size: {client_size[0]}x{client_size[1]}")
            self._last_client_size = client_size
        field_text = {name: self._ocr.read_field(img) for name, img in field_images.items()}
        snap = parse_fields(field_text)
        self._log(f"[{time.strftime('%H:%M:%S')}] fields={field_text}")
        self._log(f"          -> {snap}")
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
        # hp_max/mp_max are passed purely so Session can sanity-check them --
        # a tick whose max doesn't match the rest of the session was misparsed
        # (see rate.py's _LossTracker) and is dropped before it can inflate the
        # loss totals.
        #
        # There used to be a "does this frame even look like the stat panel?"
        # gate here (reject the tick unless LV parsed). It was removed after
        # ablating it against both live captures: it changed the totals by
        # exactly zero, because rate.py already rejects those same frames one
        # layer down -- and it carried a real risk of its own, since a broken
        # LV crop would have stopped a session recording anything at all.
        # tests/test_captured_regression.py replays the real failure through
        # this path with no gate in front of it.
        # Gated on run_state rather than relying on Session's own pause/no-op
        # behaviour: while "stopped" the Session may never have been started
        # at all (see _run_state's docstring in __init__), and feeding it
        # ticks here would silently begin calibrating/tracking a session the
        # user hasn't asked for yet.
        if self._run_state == "running":
            self._session.record(
                merged.exp_cur, merged.hp_cur, merged.mp_cur, merged.exp_pct,
                hp_max=merged.hp_max, mp_max=merged.mp_max, level=merged.level,
            )

        self._maybe_finalize_on_timeout()

        self._render(merged)
        self._maybe_refresh_manual_meso()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return max(0, int(TARGET_MS - elapsed_ms))

    def _try_locate(self) -> None:
        """Kick off a background locator pass (throttled to
        LOCATE_INTERVAL_TICKS).

        Full-frame *detection* OCR (~600ms) finds BOTH the stat panel
        fields (LV/HP/MP/EXP by their text patterns) and the meso counter,
        then caches their positions as frame fractions. The tick thread
        only ever does cheap recognition reads on those cached boxes, so
        the HUD stays correct even when a screen magnifier (Megapipe)
        rescales the layout -- the detected positions track the magnified
        frame every pass. Runs on a daemon thread so the UI never blocks;
        Tk is only touched via root.after."""
        self._locate_ticks = getattr(self, "_locate_ticks", 0) + 1
        if self._locate_ticks < LOCATE_INTERVAL_TICKS:
            return
        self._locate_ticks = 0
        # Manual mode: detection runs ONCE to subdivide the marked stat
        # region, then stops -- the region is fixed, and re-detecting every
        # ~5s was what spiked the CPU and dropped the game's frames.
        if self._settings.use_manual and getattr(self, "_manual_calibrated", False):
            return
        _thread = getattr(self, "_locate_thread", None)
        if _thread is not None and _thread.is_alive():
            return  # previous pass still running -- skip this round

        def _locate() -> None:
            try:
                # Own OCR engine: the main instance is used by the tick
                # thread concurrently, and RapidOCR is not documented
                # thread-safe. Lazy-init here so the first pass's model
                # load (seconds) happens off the UI thread too.
                locate_ocr = getattr(self, "_locate_ocr", None)
                if locate_ocr is None:
                    locate_ocr = StatPanelOcr()
                    self._locate_ocr = locate_ocr

                s = self._settings
                if s.use_manual and s.manual_stat_region is not None:
                    # Manual mode: OCR the user-marked screen rectangles
                    # directly (see ManualScreenCapture). The stat region is
                    # subdivided by detection; the meso region is read
                    # separately. Fresh capture on THIS thread.
                    from .capture import ManualScreenCapture

                    cap = ManualScreenCapture(s.manual_stat_region, s.manual_meso_region)
                    frame = cap.grab_full()
                    boxes = locate_ocr.detect_text(frame)
                    stat = find_stat_fields(boxes)
                    fw, fh = frame.size
                    stat_frac = {
                        name: (x / fw, y / fh, w / fw, h / fh)
                        for name, (x, y, w, h) in stat.items()
                    }
                    meso_frac = None
                    meso_value = None
                    if s.manual_meso_region is not None:
                        meso_frame = cap.grab_meso()
                        mboxes = locate_ocr.detect_text(meso_frame)
                        meso_found = find_meso_in_region(mboxes)
                        if meso_found is not None:
                            x, y, w, h, meso_value = meso_found
                            mw, mh = meso_frame.size
                            meso_frac = (x / mw, y / mh, w / mw, h / mh)
                else:
                    # Auto mode: fresh capture on THIS thread. The shared
                    # mss/win32 handles live on the tick thread, and
                    # re-entering them from a second thread risks races. A new
                    # GameWindowCapture is cheap (one EnumWindows + one grab).
                    # On non-Windows (dev/tests) the shared source is a
                    # pure-PIL stand-in, safe to reuse.
                    if sys.platform == "win32":
                        from .capture import GameWindowCapture

                        frame = GameWindowCapture().grab_full()
                    else:
                        frame = self._source.grab_full()
                    boxes = locate_ocr.detect_text(frame)
                    stat = find_stat_fields(boxes)
                    meso_found = find_meso_candidate(boxes, frame.size)
                    fw, fh = frame.size
                    stat_frac = {
                        name: (x / fw, y / fh, w / fw, h / fh)
                        for name, (x, y, w, h) in stat.items()
                    }
                    meso_frac = None
                    meso_value = None
                    if meso_found is not None:
                        x, y, w, h, meso_value = meso_found
                        meso_frac = (x / fw, y / fh, w / fw, h / fh)
            except Exception:
                stat_frac, meso_frac, meso_value = {}, None, None
            self.root.after(
                0,
                lambda s=stat_frac, m=meso_frac, v=meso_value: self._apply_locate(s, m, v),
            )

        self._locate_thread = threading.Thread(target=_locate, daemon=True)
        self._locate_thread.start()

    def _apply_locate(
        self,
        stat_frac: dict[str, tuple[float, float, float, float]],
        meso_frac: tuple[float, float, float, float] | None,
        meso_value: int | None,
    ) -> None:
        """Main-thread half of the locator pass (see _try_locate)."""
        if stat_frac:
            self._stat_boxes = stat_frac
            self._locate_empty_count = 0
        else:
            # Panel not found this pass (covered / not rendered). Keep the
            # last known boxes for a few passes -- transient OCR misses
            # shouldn't flap the tick between detected and fixed boxes --
            # then fall back to regions.FIELD_BOXES until the panel returns.
            self._locate_empty_count = getattr(self, "_locate_empty_count", 0) + 1
            if self._locate_empty_count >= LOCATE_EMPTY_LIMIT:
                self._stat_boxes = None

        if meso_frac is not None:
            self._meso_box = meso_frac
            if self._run_state == "running" and meso_value is not None:
                self._session.record_meso(meso_value)
                self._render(self._last)  # show the updated meso rows promptly

        if self._settings.use_manual:
            # One-shot manual calibration complete (success or not -- don't
            # retry every 5s and keep dropping the game's frames). The user
            # sees the result in the settings status line and can re-mark.
            self._manual_calibrated = True
            self._refresh_manual_status()

    def _maybe_refresh_manual_meso(self) -> None:
        """Manual-mode meso read: cheap recognition-only OCR on the marked
        meso region every MESO_SCAN_INTERVAL_TICKS (~15ms, no detection). The
        locator no longer runs periodically in manual mode, so this is what
        keeps the meso counter fresh when the user opens the inventory."""
        if not (self._settings.use_manual and self._manual_source is not None
                and self._settings.manual_meso_region is not None
                and self._settings.track_meso and self._run_state == "running"):
            return
        self._meso_scan_ticks += 1
        if self._meso_scan_ticks < MESO_SCAN_INTERVAL_TICKS:
            return
        self._meso_scan_ticks = 0
        try:
            img = self._manual_source.grab_meso()
            text = self._ocr.read_field(img)
            value = parse_meso(text)
            if value is not None:
                self._session.record_meso(value)
                self._render(self._last)
        except Exception:
            pass

    def _update_timer_label(self) -> None:
        """Split out of _render so the capture-error path in _do_tick can
        keep the countdown moving without running a full render against
        stale/absent OCR data."""
        if self._run_state == "stopped":
            # A stopped session (including the very first one, before Start
            # is ever clicked) has no countdown running -- showing a static
            # "10:00" the whole time would look like a stuck timer rather
            # than a genuinely inactive one.
            self._timer_label.configure(text="--:--")
            return
        remaining = max(0.0, self._settings.window_min * 60 - self._session.elapsed())
        remaining_s = f"{int(remaining // 60)}:{int(remaining % 60):02d}"
        self._timer_label.configure(text=self._t("timer_left", time=remaining_s))

    def _maybe_finalize_on_timeout(self) -> None:
        # Skipped while a rename dialog is open: simpledialog.askstring blocks
        # via a nested Tk event loop but doesn't stop self.root.after() timers
        # from firing, so without this guard a session could finalize and
        # insert a new history card underneath the open modal mid-edit. Also
        # skipped outright unless actually running: elapsed() is frozen while
        # paused/stopped anyway, so this wouldn't fire either way, but being
        # explicit here means it can't ever race a state change mid-tick.
        #
        # Called from both branches of _do_tick (capture success and capture
        # failure) -- Session.elapsed() is wall-clock time, not tick-driven,
        # so a session must still be able to hit its interval and finalize
        # even while the game window is covered/minimized for the whole
        # window, not just while OCR happens to be succeeding.
        if not self._modal_open and self._run_state == "running" \
                and self._session.elapsed() >= self._settings.window_min * 60:
            self._finalize_and_maybe_stop()

    def _commit_session_to_history(self) -> None:
        # Shared by the timer rollover and a manual restart with
        # save_on_restart on -- exactly one code path commits, so two
        # triggers landing on the same tick can't double-log.
        # Skip logging if the session never got a real EXP reading (restart
        # clicked immediately after launch, before OCR produced anything --
        # a '? -> ?' entry would just be noise), or if essentially no time
        # passed (rapid double-click on the restart button after real data
        # already exists -- start() carries the last known values forward,
        # so a second click 50ms later would otherwise log a valid-looking
        # but meaningless 0-duration, 0-diff entry).
        if self._session.start_exp is not None and self._session.elapsed() >= 1.0:
            summary = self._session.finalize(self._settings.window_min)
            self._session_history.append(summary)
            self._log(f"[{time.strftime('%H:%M:%S')}] {_fmt_summary(summary, len(self._session_history))}")
            self._append_history_card(summary, len(self._session_history))

    def _finalize_and_maybe_stop(self) -> None:
        """The timer rolling over. Always commits to History first; then
        either stops (default -- see settings.auto_stop) or immediately
        starts the next session, the only behaviour before that setting
        existed."""
        self._commit_session_to_history()
        if self._settings.auto_stop:
            # Reuses Session.pause() rather than adding a third Session
            # state: it freezes elapsed() at exactly this instant and makes
            # record() a no-op, which is exactly what "stopped" needs, and
            # nothing else in rate.py has to know "stopped" exists.
            self._session.pause()
            self._run_state = "stopped"
            self._apply_run_state()
        else:
            self._session.start()

    def _on_restart_clicked(self) -> None:
        if self._settings.save_on_restart:
            self._commit_session_to_history()
        self._session.start()  # resets pause state too, so a restart from "paused" lands in "running"
        self._run_state = "running"
        self._apply_run_state()
        self._render(self._last)  # immediate feedback, don't wait for next tick

    def _on_pause_button_clicked(self) -> None:
        """One button, three roles depending on _run_state -- see
        _apply_run_state for how its label/command follow that state."""
        if self._run_state == "running":
            self._session.pause()
            self._run_state = "paused"
        elif self._run_state == "paused":
            self._session.resume()
            self._run_state = "running"
        else:  # "stopped" -- already committed to History by _finalize_and_maybe_stop
            self._session.start()
            self._run_state = "running"
        self._apply_run_state()
        self._render(self._last)  # immediate feedback, don't wait for next tick

    def _apply_run_state(self) -> None:
        label_key = {"running": "pause_button", "paused": "resume_button", "stopped": "start_button"}[self._run_state]
        self._pause_button.configure(text=self._t(label_key), font=self._font(12, bold=True))
        # A Restart with nothing running/paused to restart from doesn't mean
        # anything -- Start (the pause button's role while stopped) already
        # covers beginning the next session. As the sole button in the row
        # it's centered and shrunk rather than stretched across both
        # columns the way the two-button running/paused layout is.
        if self._run_state == "stopped":
            self._restart_button.grid_remove()
            self._pause_button.configure(width=STOPPED_BUTTON_WIDTH, height=BUTTON_HEIGHT)
            self._pause_button.grid(row=0, column=0, columnspan=2, sticky="", padx=0)
        else:
            self._pause_button.configure(width=140, height=BUTTON_HEIGHT)  # CTkButton's own default width
            self._pause_button.grid(row=0, column=0, columnspan=1, sticky="ew", padx=(0, 3))
            self._restart_button.grid(row=0, column=1, columnspan=1, sticky="ew", padx=(3, 0))

    def _rebuild_history_cards(self) -> None:
        for card in self._history_cards:
            card.destroy()
        self._history_cards.clear()
        if not self._session_history:
            # _append_history_card only ever pack_forget()s this label (on
            # the first card added) -- nothing re-packs it once the list is
            # emptied again (e.g. via Clear History), so do it explicitly.
            self._history_empty_label.pack(pady=24)
            return
        # Cards are always inserted at the top (newest-first) -- rebuilding
        # oldest-first via _append_history_card reproduces the exact same
        # final order without needing separate "rebuild" layout logic.
        for index, summary in enumerate(self._session_history, start=1):
            self._append_history_card(summary, index)

    def _append_history_card(self, summary: SessionSummary, index: int) -> None:
        self._history_empty_label.pack_forget()

        card = ctk.CTkFrame(self._history_frame, fg_color=SURFACE, corner_radius=10)
        # Newest-first: pack before the current top card (if any) rather than
        # appending, so the most recently finalized session is always the
        # first thing visible in the scrollable frame.
        if self._history_cards:
            card.pack(fill="x", pady=(0, 8), before=self._history_cards[0])
        else:
            card.pack(fill="x", pady=(0, 8))
        self._history_cards.insert(0, card)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 0))
        title_label = ctk.CTkLabel(
            head, text=summary.name or self._t("history_session", n=index), font=self._font(10, bold=True),
            text_color=INK_FAINT, cursor="hand2",
        )
        title_label.pack(side="left")
        title_label.bind("<Button-1>", lambda _e, i=index, lbl=title_label: self._on_rename_clicked(i, lbl))

        # Packed before dur_text below so it lands rightmost -- pack(side="right")
        # stacks from the outer edge inward in packing order, so whichever
        # side="right" widget is packed first ends up furthest right.
        ctk.CTkButton(
            head, text="×", width=22, height=18, command=lambda i=index: self._on_delete_history_clicked(i),
            fg_color="transparent", hover_color=SURFACE_2, text_color=INK_FAINT, font=_FONT_UI_BOLD,
        ).pack(side="right")

        dur_min = summary.duration_s / 60
        # Mixes translated chrome ("restarted early"/提前重啟) with the
        # duration number when applicable, so this needs the language-aware
        # font -- the plain "10.0m" case doesn't strictly need it, but the
        # widget is rebuilt wholesale on language switch anyway either way.
        unit = self._t("unit_min_short")
        if summary.interval_minutes is not None and abs(dur_min - summary.interval_minutes) > 0.05:
            dur_text = self._t(
                "history_duration_early",
                dur=f"{dur_min:.1f}",
                target=summary.interval_minutes,
                unit=unit,
                label=self._t("history_restarted_early"),
            )
            dur_color = EXP_COLOR
            dur_font = self._font(11)
        else:
            dur_text, dur_color, dur_font = f"{dur_min:.1f}{unit}", INK_DIM, _FONT_MONO_SM
        ctk.CTkLabel(head, text=dur_text, font=dur_font, text_color=dur_color).pack(side="right")

        timestamp = ctk.CTkFrame(card, fg_color="transparent")
        timestamp.pack(fill="x", padx=12, pady=(0, 4))
        start_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(summary.start_time))
        end_ts = time.strftime("%H:%M:%S", time.localtime(summary.end_time))
        ctk.CTkLabel(
            timestamp, text=f"{start_ts} → {end_ts}", font=_FONT_MONO_SM, text_color=INK_FAINT,
        ).pack(side="left")

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
        # Efficiency: EXP per minute of session time, in the same muted
        # style as the percentage -- the headline number stays the diff.
        epm = summary.exp_per_min
        if epm is not None:
            ctk.CTkLabel(
                rng, text=f"  {epm:,.0f}/{self._t('unit_min_short')}",
                font=_FONT_MONO_SM, text_color=INK_DIM,
            ).pack(side="left")

        mini = ctk.CTkFrame(card, fg_color="transparent")
        mini.pack(fill="x", padx=12, pady=(0, 10))
        mini.grid_columnconfigure((0, 1, 2), weight=1, uniform="mini")

        def mini_stat(col: int, label: str, value: str, color: str) -> None:
            box = ctk.CTkFrame(mini, fg_color=SURFACE_2, corner_radius=7)
            box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0))
            ctk.CTkLabel(box, text=label, font=self._font(9, bold=True), text_color=INK_FAINT, anchor="w").pack(
                fill="x", padx=8, pady=(6, 0)
            )
            ctk.CTkLabel(box, text=value, font=_FONT_MONO_SM, text_color=color, anchor="w").pack(
                fill="x", padx=8, pady=(0, 6)
            )

        mini_stat(0, self._t("history_hp_loss"), _fmt_loss(summary.hp_loss), HP_COLOR if summary.hp_loss > 0 else INK_FAINT)
        mini_stat(1, self._t("history_mp_loss"), _fmt_loss(summary.mp_loss), MP_COLOR if summary.mp_loss > 0 else INK_FAINT)
        meso_s, meso_c = "--", INK_FAINT
        if summary.meso_gained is not None:
            meso_s = f"{summary.meso_gained:+,}"
            meso_c = EXP_COLOR if summary.meso_gained >= 0 else HP_COLOR
        mini_stat(2, self._t("history_meso"), meso_s, meso_c)

    @contextlib.contextmanager
    def _modal(self):
        """Run a blocking dialog. Two things have to happen around one:

        1. `_modal_open` tells _do_tick not to finalize a session while a
           dialog is up -- askstring/askyesno block on a *nested* Tk event
           loop, which does not stop self.root.after() timers from firing,
           so a session could otherwise roll over and insert a history card
           underneath the open modal mid-edit.
        2. -topmost has to come off for the duration. Tk dialogs are not
           topmost themselves, so with the HUD pinned above everything the
           dialog renders *behind* it -- while still holding a grab on all
           input. The app looks frozen (clicks on the HUD, including Restart
           Session, do nothing) with no visible cause, and stays that way
           until the invisible dialog is found and dismissed.
        """
        self._modal_open = True
        was_topmost = self._settings.topmost
        if was_topmost:
            self.root.attributes("-topmost", False)
        try:
            yield
        finally:
            self._modal_open = False
            if was_topmost:
                self.root.attributes("-topmost", True)

    def _on_rename_clicked(self, index: int, label: ctk.CTkLabel) -> None:
        # index is 1-based. session_history is no longer strictly append-only
        # (see _on_delete_history_clicked), but deleting any entry rebuilds
        # every card from scratch via _rebuild_history_cards(), so a *live*
        # card's index - 1 is always still correct: it can only go stale by
        # having its own card destroyed and recreated with the new one first.
        current = self._session_history[index - 1]
        with self._modal():
            new_name = simpledialog.askstring(
                self._t("rename_dialog_title"), self._t("rename_dialog_prompt"),
                initialvalue=current.name or self._t("history_session", n=index),
                parent=self.root,
            )
        if new_name is None:
            return  # cancelled
        new_name = new_name.strip()
        updated = dataclasses.replace(current, name=new_name or None)
        self._session_history[index - 1] = updated
        label.configure(text=updated.name or self._t("history_session", n=index))

    def _on_delete_history_clicked(self, index: int) -> None:
        summary = self._session_history[index - 1]
        with self._modal():  # see _do_tick's guard comment on _modal()
            confirmed = messagebox.askyesno(
                self._t("history_delete_confirm_title"),
                self._t(
                    "history_delete_confirm_prompt",
                    name=summary.name or self._t("history_session", n=index),
                ),
                parent=self.root,
            )
        if not confirmed:
            return
        del self._session_history[index - 1]
        # Every remaining card's 1-based index shifts once one entry is
        # removed -- rebuild from scratch rather than patching indices in
        # place, same as _on_clear_history_clicked already does.
        self._rebuild_history_cards()

    def _on_clear_history_clicked(self) -> None:
        if not self._session_history:
            return
        with self._modal():  # see _do_tick's guard comment on _modal()
            confirmed = messagebox.askyesno(
                self._t("history_clear_confirm_title"),
                self._t("history_clear_confirm_prompt", n=len(self._session_history)),
                parent=self.root,
            )
        if not confirmed:
            return
        self._session_history.clear()
        self._rebuild_history_cards()

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

        pct = f"  ({snap.exp_pct:.2f}%)" if snap.exp_pct is not None and self._settings.show_exp_pct else ""
        if snap.exp_cur is not None:
            self._value_labels["exp"].configure(text=f"{snap.exp_cur:,}{pct}")
            if snap.exp_pct is not None:
                self._bars["exp"].set(max(0.0, min(1.0, snap.exp_pct / 100)))
        else:
            self._value_labels["exp"].configure(text="--")

        start_exp = self._session.start_exp
        self._value_labels["startexp"].configure(text=f"{start_exp:,}" if start_exp is not None else "--")

        self._update_timer_label()

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
            pct_s = f"  (+{exp_diff / total_exp * 100:.2f}%)" if total_exp and self._settings.show_exp_pct else ""
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

        # Projected session total: current rate extrapolated across the full
        # window setting, not just what's elapsed so far -- see
        # Session.projected_exp (same 3s/positive-gain guard as ETA above,
        # for the same reason).
        proj = self._session.projected_exp(self._settings.window_min * 60)
        if proj is not None:
            proj_pct_s = f"  (+{proj / total_exp * 100:.2f}%)" if total_exp and self._settings.show_exp_pct else ""
            self._value_labels["projexp"].configure(text=f"+{proj:,}{proj_pct_s}")
        else:
            self._value_labels["projexp"].configure(text="--")

        hp_loss, mp_loss = self._session.hp_loss, self._session.mp_loss
        self._value_labels["hploss"].configure(
            text=_fmt_loss(hp_loss), text_color=HP_COLOR if hp_loss > 0 else INK_FAINT
        )
        self._value_labels["mploss"].configure(
            text=_fmt_loss(mp_loss), text_color=MP_COLOR if mp_loss > 0 else INK_FAINT
        )

        # Meso block: "起始楓幣" shows the session baseline; "當前楓幣" shows
        # the latest reading with the net delta, e.g. "155 (+55)" (spending
        # shows as a negative delta in red). Matches the EXP block's
        # start/diff split per user request 2026-08-24.
        meso_start = self._session.start_meso
        meso_end = self._session.end_meso
        meso = self._session.meso_gained
        self._value_labels["mesostart"].configure(
            text=f"{meso_start:,}" if meso_start is not None else "--", text_color=INK
        )
        if meso is not None and meso_end is not None:
            sign = "+" if meso >= 0 else "-"
            self._value_labels["mesocurrent"].configure(
                text=f"{meso_end:,} ({sign}{abs(meso):,})",
                text_color=EXP_COLOR if meso >= 0 else HP_COLOR,
            )
        else:
            self._value_labels["mesocurrent"].configure(text="--", text_color=INK)

        # Pause/stop/calibration are user- or engine-driven states that take
        # priority over the activity-based idle/tracking read below -- e.g. a
        # paused session with real HP/MP/EXP movement in its history isn't
        # "Idle", it's "Paused".
        if self._run_state == "paused":
            self._status_pill.configure(text=self._t("status_paused"), fg_color=SURFACE_2, text_color=EXP_COLOR)
        elif self._run_state == "stopped":
            self._status_pill.configure(text=self._t("status_stopped"), fg_color=SURFACE_2, text_color=INK_DIM)
        elif self._session.is_calibrating:
            self._status_pill.configure(text=self._t("status_calibrating"), fg_color=SURFACE_2, text_color=EXP_COLOR)
        else:
            # Idle only if NONE of HP/MP/EXP have changed recently within this
            # session -- any one of them moving counts as activity, not idle.
            idle = hp_loss == 0 and mp_loss == 0 and (exp_diff or 0) == 0
            if idle:
                self._status_pill.configure(text=self._t("status_idle"), fg_color=SURFACE_2, text_color=INK_DIM)
            else:
                self._status_pill.configure(text=self._t("status_tracking"), fg_color=TRACK_BG, text_color=OK_COLOR)

    def run(self) -> None:
        self.root.mainloop()
