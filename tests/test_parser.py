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


def test_exp_pct_separator_read_as_colon():
    """Live capture (2026-08-17) showed the decimal point OCR'd as a colon in
    37 of 235 ticks -- each one silently lost exp_pct, and with it the EXP%
    readout and the level-up ETA."""
    snap = parse_fields({"EXP": "EXP 321675[75:11%] "})
    assert snap.exp_cur == 321675
    assert snap.exp_pct == 75.11


def test_overlay_rejects_frames_that_are_not_the_stat_panel():
    """Raw OCR captured live (2026-08-17) while a terminal window covered the
    game's stat bar -- the app was reading its own log back off the screen.
    Every one of these frames must be rejected before it reaches the loss
    math; the clean frame alongside them must still be accepted."""
    from maple_analyzer.overlay import _frame_is_coherent

    covered = [
        {"LV": "r=None, hp", "HP": "CHPr=12/n2emn", "MP": "MPx=1N/2e ex", "EXP": "EXP32447N0576eXD"},
        {"LV": "LV.44", "HP": "##############", "MP": "M (28163 281616 2", "EXP": "PA 324957 7598312"},
        {"LV": "LV. 44", "HP": "h1822824（iap", "MP": "MP (28163 281616 2)", "EXP": "DXF 324957759831216"},
    ]
    clean = {"LV": "LV. 44", "HP": "HP[338/824] ", "MP": "MP[2684/2816] ", "EXP": "EXP 321813[75:14%] "}

    assert _frame_is_coherent(parse_fields(clean))

    # Frame 0 is the dangerous one: it parses as MP 1/2, i.e. the whole bar
    # booked as loss. The level gate rejects it outright.
    assert not _frame_is_coherent(parse_fields(covered[0]))
    assert parse_fields(covered[0]).mp_cur == 1  # what it would have booked

    # The other two keep a readable LV, so the level gate passes them -- they
    # are stopped further down instead: one yields no HP/MP at all, the other
    # a max of 281616 that rate.py's max-stability guard rejects. Pinned so
    # the division of labour between the two layers stays visible.
    assert parse_fields(covered[1]).mp_cur is None
    assert parse_fields(covered[2]).mp_max == 281616
