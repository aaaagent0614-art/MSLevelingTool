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
    extracted MsStatTractor folder), else the current working directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def settings_path() -> Path:
    return app_data_dir() / "MsStatTractor.settings.json"


# The only non-JSON-native fields are the (l, t, r, b) screen-region tuples,
# which are stored as lists.
_REGION_FIELDS = ("manual_stat_region", "manual_meso_region")


def save_settings(s: "Settings") -> None:
    try:
        data = asdict(s)
        for key in _REGION_FIELDS:
            value = data.get(key)
            data[key] = list(value) if value is not None else None
        frac = data.get("auto_stat_frac")
        data["auto_stat_frac"] = {k: list(v) for k, v in frac.items()} if frac else None
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
        frac = data.get("auto_stat_frac")
        data["auto_stat_frac"] = {k: tuple(v) for k, v in frac.items()} if frac else None
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
    # Play a sound (and briefly flash the window) when a session auto-stops or
    # is stopped manually. Default off.
    notify_on_stop: bool = False
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
    # Auto-mode last-known-good field positions, as fractions of the client
    # frame: {'LV': (fx, fy, fw, fh), ...}. Persisted so a restart (or a
    # transient OCR miss) reuses the real detected positions instead of the
    # stale fixed reference boxes in regions.py. Only meaningful in auto mode.
    auto_stat_frac: dict[str, tuple[float, float, float, float]] | None = None
    # Map name (2026-08-24). The dashboard shows a "地圖" field, typed by hand
    # (click the value to edit). Auto-OCR fill was removed (2026-08-25) at the
    # user's request -- the game's stylized banner font misread too often.
    map_name: str = ""
    # Compact 2x2 overlay's last position (screen px), so the user's dragged
    # placement survives a restart. None until the window has been moved once.
    compact_x: int | None = None
    compact_y: int | None = None
    # Comparison baseline (2026-08-26): start_time of the History session the
    # user picked on the History tab to compare new sessions against. None
    # while no baseline is selected. Start-time (not list index) is the key
    # so deleting/renaming sessions never shifts the reference.
    # DEPRECATED (2026-08-28): the compare feature moved to a dedicated
    # fourth tab with two dropdowns (see _build_compare_tab); this field is
    # kept only so old settings files still load.
    compare_start_time: float | None = None
