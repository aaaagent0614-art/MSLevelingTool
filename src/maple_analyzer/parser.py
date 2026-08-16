"""Turn raw OCR lines from the stat panel into structured HP/MP/EXP/LV values.

Two strategies, tried in order, because RapidOCR sometimes merges a label with its
value into one box ('HP[377/824]') and sometimes splits them into separate boxes
(observed for LV: 'LV.' and '44' come back as two adjacent boxes):

1. Regex directly against a single line's text (handles the merged case).
2. Position-based nearest-neighbor: find the label's box, then the closest
   digit-only box to its right on roughly the same text baseline (handles the
   split case).

EXP is a special case confirmed from the sample screenshot: the game shows
`cur[percentage%]` together, e.g. `162950[38.05%]`, not one or the other. Small
punctuation (the '.' and closing ']') is the most OCR-fragile part of that string
(observed dropped in testing), so the percentage is parsed best-effort and the
absolute `cur` value -- read cleanly and consistently in testing -- is treated as
the primary signal for rate calculations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .ocr import OcrLine

_PAIR_RE = {
    "HP": re.compile(r"HP\D{0,3}(\d+)\D+(\d+)", re.IGNORECASE),
    "MP": re.compile(r"MP\D{0,3}(\d+)\D+(\d+)", re.IGNORECASE),
}
_EXP_CUR_RE = re.compile(r"EXP\D{0,3}(\d+)", re.IGNORECASE)
# Percentage lost its '.' and/or trailing ']' in observed OCR output, so accept
# both '38.05%' and '3805%' (interpret a bare >=3-digit run before '%' as
# implying two decimal places, matching this game's percentage precision).
_EXP_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*%")
_LV_RE = re.compile(r"LV\.?\D{0,3}(\d+)", re.IGNORECASE)
_LV_LABEL_ONLY_RE = re.compile(r"^LV\.?$", re.IGNORECASE)


@dataclass
class StatSnapshot:
    level: int | None
    hp_cur: int | None
    hp_max: int | None
    mp_cur: int | None
    mp_max: int | None
    exp_cur: int | None
    exp_pct: float | None


def _normalize_pct(raw: str) -> float:
    if "." in raw:
        return float(raw)
    # No decimal point survived OCR -- this game shows 2 decimal places, so a
    # bare digit run like '3805' means 38.05.
    if len(raw) > 2:
        return float(f"{raw[:-2]}.{raw[-2:]}")
    return float(raw)


def _find_pair(label: str, lines: list[OcrLine]) -> tuple[int | None, int | None]:
    pattern = _PAIR_RE[label]
    for line in lines:
        m = pattern.search(line.text)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def _find_exp(lines: list[OcrLine]) -> tuple[int | None, float | None]:
    cur = pct = None
    for line in lines:
        m = _EXP_CUR_RE.search(line.text)
        if m:
            cur = int(m.group(1))
            pm = _EXP_PCT_RE.search(line.text[m.end():])
            if pm:
                pct = _normalize_pct(pm.group(1))
            break
    return cur, pct


def _find_level(lines: list[OcrLine]) -> int | None:
    # Merged case first.
    for line in lines:
        m = _LV_RE.search(line.text)
        if m:
            return int(m.group(1))
    # Split case: 'LV.' box + a separate nearby digit-only box.
    lv_box = next((l for l in lines if _LV_LABEL_ONLY_RE.match(l.text.strip())), None)
    if lv_box is None:
        return None
    lx, ly = lv_box.center
    candidates = [
        l for l in lines
        if l.text.strip().isdigit() and l is not lv_box and abs(l.center[1] - ly) <= 15
    ]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda l: abs(l.center[0] - lx))
    return int(nearest.text.strip())


def parse_stat_lines(lines: list[OcrLine]) -> StatSnapshot:
    hp_cur, hp_max = _find_pair("HP", lines)
    mp_cur, mp_max = _find_pair("MP", lines)
    exp_cur, exp_pct = _find_exp(lines)
    level = _find_level(lines)
    return StatSnapshot(
        level=level,
        hp_cur=hp_cur, hp_max=hp_max,
        mp_cur=mp_cur, mp_max=mp_max,
        exp_cur=exp_cur, exp_pct=exp_pct,
    )
