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
    topmost: bool = True
    scale_pct: int = 100
    language: Lang = "zh"
