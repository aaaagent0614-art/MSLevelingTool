"""Pure regex/normalization tests for parser.py -- no OCR, no images. Covers
the field text -> StatSnapshot logic and the OCR-noise cases documented in
parser.py's module docstring (dropped '.', dropped ']', space-for-dot)."""
from maple_analyzer.parser import parse_fields


def test_clean_fields():
    snap = parse_fields({
        "LV": "LV.44", "HP": "HP[377/824]", "MP": "MP[1663/2816]", "EXP": "EXP162950[38.05%]",
    })
    assert snap.level == 44
    assert (snap.hp_cur, snap.hp_max) == (377, 824)
    assert (snap.mp_cur, snap.mp_max) == (1663, 2816)
    assert snap.exp_cur == 162950
    assert snap.exp_pct == 38.05


def test_missing_dot_in_pct_normalized():
    # Bare 3-4 digit run before '%' implies 2 decimal places.
    snap = parse_fields({"EXP": "EXP162950[3805%]"})
    assert snap.exp_pct == 38.05


def test_missing_dot_and_bracket():
    snap = parse_fields({"EXP": "EXP162950 3805%"})
    assert snap.exp_cur == 162950
    assert snap.exp_pct == 38.05


def test_space_for_dot_in_pct():
    snap = parse_fields({"EXP": "EXP162950[63 14%]"})
    assert snap.exp_pct == 63.14


def test_short_digit_run_before_percent_not_matched():
    # A bare 1-2 digit run is deliberately ambiguous, per parser.py -- should
    # not be picked up as a percentage.
    snap = parse_fields({"EXP": "EXP162950[5%]"})
    assert snap.exp_pct is None


def test_missing_field_is_none():
    snap = parse_fields({})
    assert snap.level is None
    assert snap.hp_cur is None and snap.hp_max is None
    assert snap.mp_cur is None and snap.mp_max is None
    assert snap.exp_cur is None and snap.exp_pct is None


def test_garbage_text_does_not_raise():
    snap = parse_fields({"LV": "??", "HP": "", "MP": "garbage###", "EXP": "not an exp string"})
    assert snap.level is None
    assert snap.hp_cur is None
    assert snap.mp_cur is None
    assert snap.exp_cur is None


def test_hp_mp_case_insensitive_label():
    snap = parse_fields({"HP": "hp[10/20]", "MP": "mp[5/30]"})
    assert (snap.hp_cur, snap.hp_max) == (10, 20)
    assert (snap.mp_cur, snap.mp_max) == (5, 30)
