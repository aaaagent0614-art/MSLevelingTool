"""Turn per-field OCR text (from regions.py's FIELD_BOXES + ocr.py's
read_field()) into structured HP/MP/EXP/LV values.

Each field is OCR'd from its own tightly-cropped, isolated box now (see the
2026-08-17 recognition-only rework in ocr.py/capture.py) -- there's exactly
one string per field, always. Earlier versions of this module had to handle
RapidOCR sometimes merging a label+value into one detected box and sometimes
splitting them into two ('HP' + '[506/824]'), with a position-based
nearest-neighbor fallback for the split case; that's gone now, because
per-field cropping means there's no "other box" to merge or split against --
regex against the one string is always enough.

EXP is shown by the game as `cur[percentage%]` together, e.g. `162950[38.05%]`.
The '.' and/or closing ']' are the most OCR-fragile part of the string (observed
dropped in testing, e.g. '4980%' instead of '49.80%') -- normalized by treating
a bare >=3-digit run before '%' as implying 2 decimal places, since that's this
game's percentage precision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PAIR_RE = {
    "HP": re.compile(r"HP\D{0,3}(\d+)\D+(\d+)", re.IGNORECASE),
    "MP": re.compile(r"MP\D{0,3}(\d+)\D+(\d+)", re.IGNORECASE),
}
# The game renders EXP as `cur[pct%]`, so the opening bracket is structure,
# not decoration: a read without one is broken and must not yield a number.
# Measured over 12,384 live reads, requiring it costs 0.4% -- and those are
# garbage like 'EXP101332182' (booked +101,322,049 of phantom gain before this)
# and 'EXP357041183.37%]', where the missing bracket merged 357041 and 183 into
# one number. OCR reads '[' as '(' or '{' often enough to accept those too.
_EXP_CUR_RE = re.compile(r"EXP\D{0,3}(\d+)\s*[\[({]", re.IGNORECASE)
# Percentage is 0-99.99. Separator between the two digit groups is normally
# '.', but OCR sometimes drops it entirely (bare 3-4 digit run) or -- seen
# with recognition-only OCR on this tiny font -- reads it as a space or a
# colon instead ('63 14%', '75:11%'). The colon form showed up in 37 of 235
# ticks in a live capture (2026-08-17), each one silently costing exp_pct and
# with it the EXP% display and the level-up ETA. All forms captured here;
# _normalize_pct interprets them. A bare 1-2 digit run is deliberately NOT
# matched, ambiguous with stray adjacent OCR noise.
_EXP_PCT_RE = re.compile(r"(\d{1,2}[.\s:]\d{1,2}|\d{3,4})\s*%")
_LV_RE = re.compile(r"LV\.?\D{0,3}(\d+)", re.IGNORECASE)

# Detection-text patterns for locating the stat panel fields in a full-frame
# detection pass (see find_stat_fields) -- the same regexes the per-field
# recognition path uses, applied to the boxes detection finds.
_STAT_FIELD_PATTERNS: dict[str, re.Pattern] = {
    "LV": _LV_RE,
    "HP": _PAIR_RE["HP"],
    "MP": _PAIR_RE["MP"],
    "EXP": _EXP_CUR_RE,
}

# Meso counter (inventory open) renders as digits with comma separators,
# e.g. "1,234,567". OCR drops or mangles commas regularly, so accept any
# digit/comma run and strip the commas -- the digits alone are the value.
_MESO_RE = re.compile(r"[\d,]+")
# Anything beyond a quadrillion is OCR garbage, not a real counter (the
# classic client caps mesos far lower; even a raised-cap server never
# approaches this). Guards against a stray digit run from some other
# text on screen.
_MESO_MAX = 10**15
# A detection box counts as the meso counter only if its text is PURE
# digits/commas (the counter renders as '154,821' with no label in the
# same box). Item stack counts qualify too -- they're just smaller (see
# find_meso_from_boxes' scoring).
_MESO_PURE_DIGIT_RE = re.compile(r"^[\d,]+$")
# The stat panel strip (LV/HP/MP/EXP) sits at the bottom of the client --
# its LV value is also pure digits and must never be picked as meso.
_STAT_STRIP_MARGIN = 50


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
    # Recognition-only OCR on the tiny EXP field font sometimes reads the
    # decimal point as a space ('63 14%') or a colon ('75:11%') rather than
    # dropping it outright -- treat both the same as a dot.
    raw = raw.replace(" ", ".").replace(":", ".")
    if "." in raw:
        return float(raw)
    if len(raw) > 2:
        return float(f"{raw[:-2]}.{raw[-2:]}")
    return float(raw)


def _find_pair(label: str, text: str) -> tuple[int | None, int | None]:
    m = _PAIR_RE[label].search(text)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _find_exp(text: str) -> tuple[int | None, float | None]:
    m = _EXP_CUR_RE.search(text)
    if not m:
        return None, None
    cur = int(m.group(1))
    pm = _EXP_PCT_RE.search(text[m.end():])
    pct = _normalize_pct(pm.group(1)) if pm else None
    return cur, pct


def _find_level(text: str) -> int | None:
    m = _LV_RE.search(text)
    return int(m.group(1)) if m else None


def parse_fields(field_text: dict[str, str]) -> StatSnapshot:
    """field_text: {'LV': ..., 'HP': ..., 'MP': ..., 'EXP': ...} -- the raw
    recognized text for each of regions.py's FIELD_BOXES."""
    hp_cur, hp_max = _find_pair("HP", field_text.get("HP", ""))
    mp_cur, mp_max = _find_pair("MP", field_text.get("MP", ""))
    exp_cur, exp_pct = _find_exp(field_text.get("EXP", ""))
    level = _find_level(field_text.get("LV", ""))
    return StatSnapshot(
        level=level,
        hp_cur=hp_cur, hp_max=hp_max,
        mp_cur=mp_cur, mp_max=mp_max,
        exp_cur=exp_cur, exp_pct=exp_pct,
    )


def parse_meso(text: str | None) -> int | None:
    """Extract the meso counter value from OCR text.

    Accepts '1,234,567', '1234567', and the mangled in-between forms OCR
    actually produces ('1,234567', 'l234,567' won't match the digit run and
    yields None). Returns None for empty/unrecognized input and for
    implausibly large values (OCR garbage from unrelated text).
    """
    m = _MESO_RE.search(text or "")
    if not m:
        return None
    digits = m.group(0).replace(",", "")
    if not digits:
        return None
    value = int(digits)
    if value > _MESO_MAX:
        return None
    return value


def find_meso_candidate(
    boxes: list[tuple[int, int, int, int, str]], frame_size: tuple[int, int]
) -> tuple[int, int, int, int, int] | None:
    """Pick the meso counter out of full-frame detection boxes and return
    (x, y, w, h, value) -- the box so callers can cache the position for
    cheap recognition-only re-reads, plus the parsed value.

    `boxes` are (x, y, w, h, text) from ocr.detect_text(). The meso counter
    is the largest pure-digit text blob on screen that is NOT in the bottom
    stat-panel strip (the LV value there is also pure digits). Item stack
    counts are pure digits too but smaller; when digit counts tie, the lower
    blob wins -- the counter sits at the bottom edge of the inventory
    window, below the item grid. None when nothing qualifies (inventory
    closed)."""
    fw, fh = frame_size
    candidates: list[tuple[int, int, str, int, int, int, int]] = []
    for x, y, w, h, text in boxes:
        stripped = text.strip()
        if not _MESO_PURE_DIGIT_RE.match(stripped):
            continue
        if y + h > fh - _STAT_STRIP_MARGIN:
            continue  # stat-panel strip: LV/HP/MP/EXP live here
        digits = stripped.replace(",", "")
        if not digits:
            continue
        if int(digits) > _MESO_MAX:
            continue
        candidates.append((len(digits), y, stripped, x, y, w, h))
    if not candidates:
        return None
    # Most digits wins; ties break toward the bottom of the screen.
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, text, x, y, w, h = candidates[-1]
    value = parse_meso(text)
    if value is None:
        return None
    return x, y, w, h, value


def find_meso_from_boxes(
    boxes: list[tuple[int, int, int, int, str]], frame_size: tuple[int, int]
) -> int | None:
    """Value-only convenience over find_meso_candidate (see its docstring)."""
    found = find_meso_candidate(boxes, frame_size)
    return found[4] if found is not None else None


def find_stat_fields(
    boxes: list[tuple[int, int, int, int, str]],
) -> dict[str, tuple[int, int, int, int]]:
    """Locate the stat panel fields in a full-frame detection pass.

    Matches each detection box's text against the known panel patterns
    ('LV. 32', 'HP[602/602]', 'MP[...]', 'EXP...[%]') and returns the box
    (x, y, w, h) per field found. Works on magnified frames too -- the text
    is bigger but the patterns are unchanged, which is what makes the HUD
    survive screen magnifiers (Megapipe). Empty dict when the panel is not
    visible (covered / not rendered / OCR failed)."""
    found: dict[str, tuple[int, int, int, int]] = {}
    digit_boxes: list[tuple[int, int, int, int, str]] = []
    for x, y, w, h, text in boxes:
        stripped = text.strip()
        matched = False
        for name, pat in _STAT_FIELD_PATTERNS.items():
            if name in found:
                continue
            if pat.search(stripped):
                found[name] = (int(x), int(y), int(w), int(h))
                matched = True
                break
        if not matched and re.fullmatch(r"[\d,]+", stripped):
            digit_boxes.append((int(x), int(y), int(w), int(h), stripped))

    # LV split fallback: detection often splits 'LV. 32' into an 'LV.' box
    # and a separate '32' box (seen on the 1280x868 client frame). Neither
    # matches _LV_RE alone; merge the 'LV.' label with the digit box
    # immediately to its right (vertically overlapping) into one box.
    if "LV" not in found:
        for x, y, w, h, text in boxes:
            stripped = text.strip()
            if not re.match(r"^LV\.?$", stripped, re.IGNORECASE):
                continue
            for dx, dy, dw, dh, _dtext in digit_boxes:
                if dx < x + w - 4 or dx > x + w + 80:
                    continue
                if dy >= y + h or dy + dh <= y:
                    continue
                x0, y0 = min(x, dx), min(y, dy)
                x1, y1 = max(x + w, dx + dw), max(y + h, dy + dh)
                found["LV"] = (x0, y0, x1 - x0, y1 - y0)
                break
            if "LV" in found:
                break
    return found
