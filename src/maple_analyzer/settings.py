"""UI-layer settings, as a single struct.

Deliberately a plain dataclass with JSON-primitive fields only (str/int/bool),
same reasoning as rate.py's SessionSummary -- this is the shape a future
persistence layer (see ~/.claude/notes/maplestory-analyzer/ui-plan-2026-08-17.md)
would load/save wholesale, e.g. `json.dumps(dataclasses.asdict(settings))`.
overlay.py should read/write through a single `self._settings` instance rather
than scattering individual attributes across OverlayApp, so that swapping in
disk persistence later is "load one struct, save one struct" instead of a
field-by-field migration.
"""
from __future__ import annotations

from dataclasses import dataclass

from .i18n import Lang


@dataclass
class Settings:
    window_min: int = 10
    show_hp: bool = True
    show_mp: bool = True
    show_exp: bool = True
    show_exp_pct: bool = True
    show_eta: bool = True
    show_proj_exp: bool = True
    topmost: bool = True
    scale_pct: int = 100
    language: Lang = "zh"
    # Whether the timer rolling over finalizes+commits to History and then
    # STOPS, vs. finalizing and immediately starting the next session (the
    # only behaviour before this setting existed). Default on per user
    # request -- see ~/.claude/notes/maplestory-analyzer/feature-plan-2026-08-23.md.
    auto_stop: bool = True
    # Whether the manual Restart button commits the in-progress session to
    # History before discarding it. Governs Restart only -- auto_stop's
    # timer-driven finalize always commits regardless of this. Default on
    # per user request (2026-08-23, revised from the original off default):
    # a restarted session's progress should be kept unless the user opts
    # out, and per-entry deletion (see OverlayApp._on_delete_history_clicked)
    # is the escape hatch for a throwaway entry now that one exists.
    save_on_restart: bool = True
