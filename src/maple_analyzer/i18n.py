"""UI string translation. English + Traditional Chinese, default Traditional
Chinese per user request. One mapping table (`_STRINGS`) is the single source
of truth for every user-facing string in overlay.py -- no literal UI text
should live inline in overlay.py itself, so both languages stay complete and
adding a string can't accidentally skip a translation.

Live game-data labels (HP/MP/EXP/LV) are deliberately left as-is in both
languages -- they're the exact abbreviations the game's own panel displays
(see regions.py/parser.py), not app UI chrome, so translating them would make
the overlay's labels mismatch what's on-screen in the actual game.
"""
from __future__ import annotations

from typing import Literal

Lang = Literal["en", "zh"]

_STRINGS: dict[str, dict[Lang, str]] = {
    "tab_live": {"en": "Dashboard", "zh": "儀錶板"},
    "tab_history": {"en": "History", "zh": "紀錄"},
    "tab_settings": {"en": "Settings", "zh": "設定"},

    "status_tracking": {"en": "Tracking", "zh": "追蹤中"},
    "status_idle": {"en": "Idle", "zh": "閒置"},
    "status_calibrating": {"en": "Calibrating…", "zh": "校準中…"},
    "status_paused": {"en": "Paused", "zh": "已暫停"},
    "status_stopped": {"en": "Stopped", "zh": "已停止"},
    "timer_left": {"en": "{time} left", "zh": "剩餘 {time}"},

    # Recognized capture.py RuntimeError messages (see overlay.py's
    # _localize_error) -- the game closing or minimizing is a normal,
    # expected condition users hit constantly, so it gets a real translation
    # rather than showing capture.py's raw English exception text.
    "status_error_minimized": {"en": "Game window minimized", "zh": "遊戲視窗已最小化"},
    "status_error_not_found": {"en": "Game window not found", "zh": "找不到遊戲視窗"},
    # Fallback for anything NOT recognized above -- an actual bug, not a
    # known game-window state, so {detail} (the raw exception text) stays in
    # English rather than pretending to translate arbitrary Python errors.
    "status_error_unknown": {"en": "Error: {detail}", "zh": "發生錯誤：{detail}"},

    "kv_start_exp": {"en": "Start EXP", "zh": "起始經驗值"},
    "kv_exp_diff": {"en": "EXP diff", "zh": "經驗值變化"},
    "kv_eta": {"en": "Level-up ETA", "zh": "升級預估時間"},
    "kv_proj_exp": {"en": "Est. session EXP", "zh": "預估本次經驗值"},
    "kv_hp_loss": {"en": "HP loss", "zh": "HP 損失"},
    "kv_mp_loss": {"en": "MP loss", "zh": "MP 損失"},
    "kv_meso_start": {"en": "Start meso", "zh": "起始楓幣"},
    "kv_meso_current": {"en": "Current meso", "zh": "當前楓幣"},
    "kv_map": {"en": "Map", "zh": "地圖"},
    "kv_map_placeholder": {"en": "+ Click to enter map", "zh": "＋ 點擊輸入地圖"},

    "restart_button": {"en": "Restart Session", "zh": "重新開始"},
    "pause_button": {"en": "Pause", "zh": "暫停"},
    "resume_button": {"en": "Resume", "zh": "繼續"},
    "stop_button": {"en": "Stop", "zh": "停止"},
    "start_button": {"en": "Start Session", "zh": "開始"},

    "history_empty": {"en": "No sessions yet", "zh": "尚無紀錄"},
    "history_session": {"en": "SESSION #{n}", "zh": "紀錄 #{n}"},
    "history_hp_loss": {"en": "HP LOSS", "zh": "HP 損失"},
    "history_mp_loss": {"en": "MP LOSS", "zh": "MP 損失"},
    "history_meso": {"en": "MESO", "zh": "楓幣"},
    "history_exp": {"en": "EXP", "zh": "經驗值"},
    "history_exp_per_min": {"en": "EXP/MIN", "zh": "經驗/分"},
    "history_restarted_early": {"en": "restarted early", "zh": "提前重啟"},
    "history_clear_button": {"en": "Clear History", "zh": "清除紀錄"},
    "history_clear_confirm_title": {"en": "Clear history", "zh": "清除紀錄"},
    "history_clear_confirm_prompt": {
        "en": "Delete all {n} session(s)? This can't be undone.",
        "zh": "刪除全部 {n} 筆紀錄？此動作無法復原。",
    },
    "history_delete_confirm_title": {"en": "Delete session", "zh": "刪除紀錄"},
    "history_delete_confirm_prompt": {
        "en": "Delete \"{name}\"? This can't be undone.",
        "zh": "刪除「{name}」？此動作無法復原。",
    },

    "settings_window_scale": {"en": "WINDOW SCALE", "zh": "視窗縮放"},
    "settings_always_on_top": {"en": "Always on top", "zh": "永遠置頂"},
    "settings_session_interval": {"en": "SESSION INTERVAL", "zh": "紀錄區間"},
    "settings_display": {"en": "DISPLAY", "zh": "顯示項目"},
    "settings_show_hp": {"en": "Show HP", "zh": "顯示 HP"},
    "settings_show_mp": {"en": "Show MP", "zh": "顯示 MP"},
    "settings_show_exp": {"en": "Show EXP", "zh": "顯示經驗值"},
    "settings_show_exp_pct": {"en": "Show EXP percentage", "zh": "顯示經驗值百分比"},
    "settings_show_eta": {"en": "Show level-up ETA", "zh": "顯示升級預估時間"},
    "settings_show_proj_exp": {"en": "Show estimated session EXP", "zh": "顯示預估本次經驗值"},
    "settings_language": {"en": "LANGUAGE", "zh": "語言"},
    "settings_session": {"en": "SESSION", "zh": "紀錄行為"},
    "settings_auto_stop": {
        "en": "Stop automatically when the timer ends", "zh": "計時結束時自動停止",
    },
    "settings_save_on_restart": {
        "en": "Save to History when restarting", "zh": "重新開始時儲存至紀錄",
    },
    "settings_track_meso": {
        "en": "Track meso (inventory)", "zh": "楓幣追蹤（道具欄）",
    },
    "settings_track_meso_hint": {
        "en": "Open the inventory (I) once right after Start and once before the session ends; the meso counter is read both times.",
        "zh": "開始記錄後與結束前，各開一次道具欄（I），程式會讀取楓幣數字。",
    },
    "settings_manual": {"en": "MANUAL POSITION", "zh": "手動設定位置"},
    "settings_use_manual": {
        "en": "Use manually marked positions", "zh": "使用手動標記的位置",
    },
    "settings_set_stat_region": {
        "en": "Mark status bar position", "zh": "標記狀態列位置",
    },
    "settings_set_meso_region": {
        "en": "Mark meso position", "zh": "標記楓幣位置",
    },
    "settings_stat_region_set": {"en": "Status bar: set", "zh": "狀態列：已設定"},
    "settings_stat_region_unset": {"en": "Status bar: not set", "zh": "狀態列：未設定"},
    "settings_meso_region_set": {"en": "Meso: set", "zh": "楓幣：已設定"},
    "settings_meso_region_unset": {"en": "Meso: not set", "zh": "楓幣：未設定"},
    "settings_manual_detecting": {"en": "Detecting status bar…", "zh": "狀態列偵測中…"},
    "settings_manual_detected": {
        "en": "Status bar: {n}/4 fields found", "zh": "狀態列：偵測到 {n}/4 個欄位",
    },
    "settings_manual_detect_failed": {
        "en": "Status bar: detection failed, re-mark it", "zh": "狀態列：偵測失敗，請重新框選",
    },
    "settings_manual_missing_prompt": {
        "en": "Please mark the manual positions first, or switch to auto.",
        "zh": "請先標記手動設定位置，或改為自動。",
    },
    "settings_map": {"en": "MAP", "zh": "地圖"},
    "settings_map_auto": {
        "en": "Auto-fill map name", "zh": "自動帶入地圖名稱",
    },
    "settings_set_map_region": {
        "en": "Mark map name position", "zh": "標記地圖位置",
    },
    "settings_map_region_set": {"en": "Map: set", "zh": "地圖：已設定"},
    "settings_map_region_unset": {"en": "Map: not set", "zh": "地圖：未設定"},
    "region_selector_hint": {
        "en": "Drag to mark the area, release to confirm (Esc to cancel)",
        "zh": "拖曳框選範圍，放開滑鼠完成（Esc 取消）",
    },

    "unit_min": {"en": "min", "zh": "分鐘"},
    "unit_min_short": {"en": "m", "zh": "分"},
    "history_duration_early": {
        "en": "{dur}{unit} of {target}{unit}, {label}",
        "zh": "{dur}{unit}／{target}{unit}，{label}",
    },

    "rename_dialog_title": {"en": "Rename session", "zh": "重新命名紀錄"},
    "rename_dialog_prompt": {"en": "Session name:", "zh": "紀錄名稱："},
    "map_dialog_title": {"en": "Map name", "zh": "地圖名稱"},
    "map_dialog_prompt": {"en": "Map name:", "zh": "地圖名稱："},
}


def t(key: str, lang: Lang, **kwargs: object) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return key  # missing translation key -- fail loud-ish rather than KeyError mid-render
    text = entry.get(lang, entry["en"])
    return text.format(**kwargs) if kwargs else text
