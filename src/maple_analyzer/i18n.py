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
    "status_calibrating": {"en": "Detecting…", "zh": "偵測中…"},
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
    "compact_restore": {"en": "Restore", "zh": "還原"},

    "history_empty": {"en": "No sessions yet", "zh": "尚無紀錄"},
    "history_session": {"en": "SESSION #{n}", "zh": "紀錄 #{n}"},
    "history_hp_loss": {"en": "HP LOSS", "zh": "HP 損失"},
    "history_mp_loss": {"en": "MP LOSS", "zh": "MP 損失"},
    "history_meso": {"en": "MESO", "zh": "楓幣"},
    "history_meso_wild": {"en": "Wild {n}", "zh": "野生 {n}"},
    "history_meso_equip": {"en": "Equip {n}", "zh": "裝備 {n}"},
    "history_exp": {"en": "EXP", "zh": "經驗值"},
    "history_exp_per_min": {"en": "EXP/MIN", "zh": "經驗/分"},
    "history_restarted_early": {"en": "restarted early", "zh": "提前重啟"},
    "history_clear_button": {"en": "Clear History", "zh": "清除紀錄"},
    "history_compare_button": {"en": "Compare", "zh": "比較"},
    "history_compare_unset_button": {"en": "Cancel", "zh": "取消比較"},
    "history_compare_badge": {"en": "BASELINE", "zh": "基準"},
    "compare_card_title": {"en": "COMPARE", "zh": "比較"},
    "compare_no_base": {
        "en": "Pick a baseline on the History tab to compare sessions",
        "zh": "在「紀錄」分頁選一筆紀錄即可比較",
    },
    "kv_compare_base": {"en": "vs baseline", "zh": "比較基準"},
    "kv_compare_exp": {"en": "EXP/min", "zh": "經驗/分"},
    "kv_compare_hp": {"en": "HP loss/min", "zh": "HP 損失/分"},
    "kv_compare_mp": {"en": "MP loss/min", "zh": "MP 損失/分"},
    "kv_compare_meso": {"en": "Meso/min", "zh": "楓幣/分"},
    "compact_compare": {"en": "vs {name} {pct}%", "zh": "比 {name} {pct}%"},
    # Manual stat edit (see _on_stat_edit) -- the player can correct an OCR
    # misread directly on the Dashboard instead of being stuck with it.
    "stat_edit_title": {"en": "Edit value", "zh": "手動調整"},
    "stat_edit_prompt_level": {"en": "LV:", "zh": "LV："},
    "stat_edit_prompt_hp": {"en": "HP (current):", "zh": "HP（目前）："},
    "stat_edit_prompt_mp": {"en": "MP (current):", "zh": "MP（目前）："},
    "stat_edit_prompt_exp": {"en": "EXP (cumulative):", "zh": "EXP（累計）："},
    "stat_edit_hint": {
        "en": "Empty input clears the manual value (revert to OCR).",
        "zh": "留空並確定可清除手動值（恢復自動讀取）。",
    },
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
    "settings_manual_detecting": {"en": "Detecting…", "zh": "偵測中…"},
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
    "screen_changed_prompt": {
        "en": "Screen resolution changed. Your marked positions may be off — please re-mark them.",
        "zh": "螢幕解析度已改變，標記位置可能已跑掉，請重新標記。",
    },
    "detect_button": {"en": "Detect", "zh": "辨識"},
    "detect_result_ok": {"en": "Detected {n}/4 fields", "zh": "偵測到 {n}/4 欄位"},
    "detect_result_fail": {"en": "Detection failed — re-mark it", "zh": "偵測失敗，請重新標記"},
    "detect_result_ok_partial": {
        "en": "Detected {n}/4 fields (missing {missing})",
        "zh": "偵測到 {n}/4 欄位（缺 {missing}）",
    },
    "detect_result_ok_meso": {"en": "Detected {n}/4 fields + meso", "zh": "偵測到 {n}/4 欄位＋楓幣"},
    "detect_result_meso_missing": {
        "en": "Detected {n}/4 fields — open inventory (I)",
        "zh": "偵測到 {n}/4 欄位，請開啟道具欄（I）",
    },
    "update_available": {"en": "New version v{ver} available", "zh": "有新版本 v{ver} 可下載"},
    "meso_hint": {"en": "Meso: press I to open inventory", "zh": "楓幣：按 I 開啟道具欄讀取"},
    "settings_notify_on_stop": {"en": "Alert when a session ends", "zh": "紀錄結束時提醒（音效）"},
    "history_summary_count": {"en": "{n} sessions", "zh": "共 {n} 筆紀錄"},
    "history_summary_today": {"en": "Today +{exp} EXP", "zh": "今日經驗值 +{exp}"},
    "history_summary_avg": {"en": "Avg {rate} EXP/min", "zh": "平均 {rate} EXP/分"},
    "history_cleanup_hint": {
        "en": "{n} sessions — consider cleaning up old ones",
        "zh": "已累積 {n} 筆紀錄，建議定期整理（刪除不用的）",
    },
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

    # Equipment-sale revenue (recorded after a session stops, attributed to
    # the session whose drops are being sold).
    "kv_meso_sale": {"en": "Sale revenue", "zh": "賣裝收益"},
    "kv_meso_total": {"en": "Total meso", "zh": "總收益"},
    "record_sale_button": {"en": "Record Sale", "zh": "記錄賣裝"},
    "record_sale_hint": {
        "en": "After selling, open the inventory (I) and press this.",
        "zh": "賣完裝後開啟道具欄（I），再按此記錄。",
    },
    "record_sale_done": {"en": "Sale recorded +{n}", "zh": "已記錄賣裝 +{n}"},
    "record_sale_need_inventory": {
        "en": "Open the inventory (I) first, then record.",
        "zh": "請先開啟道具欄（I）再記錄賣裝。",
    },
    "sale_pending_title": {"en": "Equipment revenue not recorded", "zh": "尚未計算裝備收益"},
    "sale_pending_prompt": {
        "en": "Equipment revenue hasn't been recorded for the last session. Start a new record anyway?",
        "zh": "尚未計算裝備收益，是否開始新的紀錄？",
    },

    # Compact overlay metric labels (derived values the game itself doesn't
    # show -- the game already renders live HP/MP bars, so the small window
    # shows the *changes* instead of duplicating current values).
    "compact_hp_loss": {"en": "HP USED", "zh": "HP 消耗"},
    "compact_mp_loss": {"en": "MP USED", "zh": "MP 消耗"},
    "compact_meso": {"en": "MESO", "zh": "楓幣"},
    "compact_exp": {"en": "EXP", "zh": "經驗值"},
    "compact_eta": {"en": "LEVEL", "zh": "升級"},

    # Shown when WGC isn't available and capture has silently degraded to
    # PrintWindow / mss (screen region) -- the user should know their
    # anti-occlusion capture is gone.
    "compat_mode_hint": {
        "en": "Compatibility capture (no anti-occlusion)",
        "zh": "相容模式（無抗遮擋）",
    },

    # Detection-result messages, split by cause so the instruction actually
    # matches what the user can do (see _set_detect_result).
    "detect_result_fail_auto": {
        "en": "Detection failed — check the game window",
        "zh": "偵測失敗，請確認遊戲視窗",
    },
    "detect_result_meso_need_mark": {
        "en": "Detected {n}/4 fields — mark the meso position first",
        "zh": "偵測到 {n}/4 欄位，請先標記楓幣位置",
    },
}


def t(key: str, lang: Lang, **kwargs: object) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return key  # missing translation key -- fail loud-ish rather than KeyError mid-render
    text = entry.get(lang, entry["en"])
    return text.format(**kwargs) if kwargs else text
