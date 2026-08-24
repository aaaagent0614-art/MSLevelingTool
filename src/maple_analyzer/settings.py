"""UI-layer settings, as a single struct.

Deliberately a plain dataclass with JSON-primitive fields only (str/int/bool/
tuple-of-int), same reasoning as rate.py's SessionSummary -- this is the shape
the persistence layer loads/saves wholesale. overlay.py reads/writes through a
single `self._settings` instance rather than scattering individual attributes
across OverlayApp, so "load one struct, save one struct" is all that happens.

Persistence: settings are written to a JSON file next to the exe (frozen) or in
the cwd (dev). The tuple region fields are stored as lists and converted back
on load.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .i18n import Lang


def app_data_dir() -> Path:
    """Directory for persisted files: next to the exe when frozen (the
    extracted MSLevelingTool folder), else the current working directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def settings_path() -> Path:
    return app_data_dir() / "MSLevelingTool.settings.json"


# The only non-JSON-native fields are the (l, t, r, b) screen-region tuples,
# which are stored as lists.
_REGION_FIELDS = ("manual_stat_region", "manual_meso_region", "map_region")


def save_settings(s: "Settings") -> None:
    try:
        data = asdict(s)
        for key in _REGION_FIELDS:
            value = data.get(key)
            data[key] = list(value) if value is not None else None
        settings_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        # Persistence is best-effort -- never let a failed save take down the app.
        pass


def load_settings() -> "Settings":
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        for key in _REGION_FIELDS:
            value = data.get(key)
            data[key] = tuple(value) if value is not None else None
        return Settings(**data)
    except Exception:
        # Missing/corrupt/old-format file -> fresh defaults.
        return Settings()


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
    # Default window scale is 120% per user request (2026-08-24): the
    # original 100% reads as too small on typical gaming displays.
    scale_pct: int = 120
    language: Lang = "zh"
    # Whether the timer rolling over finalizes+commits to History and then
    # STOPS, vs. finalizing and immediately starting the next session (the
    # only behaviour before this setting existed). Default on per user
    # request.
    auto_stop: bool = True
    # Whether the manual Restart button commits the in-progress session to
    # History before discarding it. Governs Restart only -- auto_stop's
    # timer-driven finalize always commits regardless of this. Default on.
    save_on_restart: bool = True
    # Meso tracking: watch the inventory counter and record the session's net
    # meso change. The user opens the inventory once after Start and once
    # before the session ends; the first/last readings become the endpoints.
    # Default on per user request (2026-08-24).
    track_meso: bool = True
    # Manual screen-region calibration (2026-08-24). When use_manual is on and
    # manual_stat_region is set, the capture layer OCRs the user-marked screen
    # rectangles directly via mss instead of locating the game window. This is
    # what keeps the HUD reading correct values under a screen magnifier
    # (Magpie): the game window's own client rect no longer matches what is
    # visible on screen, but the user can draw a box around the actual status
    # bar / meso counter and those screen pixels are exactly what we read.
    manual_stat_region: tuple[int, int, int, int] | None = None  # (l, t, r, b) screen px
    manual_meso_region: tuple[int, int, int, int] | None = None
    use_manual: bool = False
    # Map name (2026-08-24). The dashboard shows a "地圖" field: either typed
    # by hand, or auto-filled by OCR when map_auto is on and map_region is
    # marked (the map-name text on screen, e.g. the area banner). Auto is off
    # by default per user request.
    map_name: str = ""
    map_auto: bool = False
    map_region: tuple[int, int, int, int] | None = None
