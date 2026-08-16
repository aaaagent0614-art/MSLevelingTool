"""Turn raw OCR lines from the stat panel into structured HP/MP/EXP/LV values.

Two strategies, tried in order, because RapidOCR sometimes merges a label with its
value into one box ('HP[377/824]') and sometimes splits them into separate boxes
('HP' + '[506/824]', or even 'HP[' + '[506/824]'):

1. Regex directly against a single line's text (handles the merged case).
2. Position-based nearest-neighbor: find the label's box, then the closest
   value box to its right on roughly the same text baseline (handles the split
   case).

The split case turned out to be the *common* one for HP/MP in extended live
testing (2026-08-17) -- not a rare edge case, roughly half of ticks or more.
Only LV had a fallback for it originally, which meant HP/MP silently went
`None` (and, before the overlay's carry-forward fix, flickered to '--') on a
majority of ticks. Applies uniformly to LV/HP/MP now.

EXP is shown by the game as `cur[percentage%]` together, e.g. `162950[38.05%]`.
Only `cur` is parsed. The percentage was tried and dropped (2026-08-17): its '.'
and/or closing ']' are the most OCR-fragile part of the string and it came back
wrong often enough in live testing to not be worth displaying -- `cur` alone is
read reliably and is sufficient for rate calculations anyway.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .ocr import OcrLine

_PAIR_RE = {
    "HP": re.compile(r"HP\D{0,3}(\d+)\D+(\d+)", re.IGNORECASE),
    "MP": re.compile(r"MP\D{0,3}(\d+)\D+(\d+)", re.IGNORECASE),
}
_PAIR_LABEL_ONLY_RE = {
    "HP": re.compile(r"^HP\W*$", re.IGNORECASE),
    "MP": re.compile(r"^MP\W*$", re.IGNORECASE),
}
_PAIR_VALUE_ONLY_RE = re.compile(r"^\W*(\d+)\D+(\d+)\W*$")
_EXP_CUR_RE = re.compile(r"EXP\D{0,3}(\d+)", re.IGNORECASE)
_LV_RE = re.compile(r"LV\.?\D{0,3}(\d+)", re.IGNORECASE)
_LV_LABEL_ONLY_RE = re.compile(r"^LV\.?$", re.IGNORECASE)


def _nearest_right(label_line: OcrLine, lines: list[OcrLine], predicate) -> OcrLine | None:
    lx, ly = label_line.center
    candidates = [
        l for l in lines
        if l is not label_line and predicate(l) and abs(l.center[1] - ly) <= 15 and l.center[0] > lx
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda l: l.center[0] - lx)


@dataclass
class StatSnapshot:
    level: int | None
    hp_cur: int | None
    hp_max: int | None
    mp_cur: int | None
    mp_max: int | None
    exp_cur: int | None


def _find_pair(label: str, lines: list[OcrLine]) -> tuple[int | None, int | None]:
    pattern = _PAIR_RE[label]
    for line in lines:
        m = pattern.search(line.text)
        if m:
            return int(m.group(1)), int(m.group(2))
    # Split case: a label-only box ('HP', 'HP[', ...) + a separate nearby
    # value-only box ('[506/824]').
    label_box = next(
        (l for l in lines if _PAIR_LABEL_ONLY_RE[label].match(l.text.strip())), None
    )
    if label_box is None:
        return None, None
    value_box = _nearest_right(label_box, lines, lambda l: _PAIR_VALUE_ONLY_RE.match(l.text.strip()))
    if value_box is None:
        return None, None
    m = _PAIR_VALUE_ONLY_RE.match(value_box.text.strip())
    return int(m.group(1)), int(m.group(2))


def _find_exp(lines: list[OcrLine]) -> int | None:
    for line in lines:
        m = _EXP_CUR_RE.search(line.text)
        if m:
            return int(m.group(1))
    return None


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
    value_box = _nearest_right(lv_box, lines, lambda l: l.text.strip().isdigit())
    return int(value_box.text.strip()) if value_box is not None else None


def parse_stat_lines(lines: list[OcrLine]) -> StatSnapshot:
    hp_cur, hp_max = _find_pair("HP", lines)
    mp_cur, mp_max = _find_pair("MP", lines)
    exp_cur = _find_exp(lines)
    level = _find_level(lines)
    return StatSnapshot(
        level=level,
        hp_cur=hp_cur, hp_max=hp_max,
        mp_cur=mp_cur, mp_max=mp_max,
        exp_cur=exp_cur,
    )
