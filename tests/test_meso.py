"""Meso counter selection tests.

The meso counter lives in the draggable inventory window, so its position
isn't known in advance. The pipeline finds it by running full-frame
*detection* OCR and picking the largest pure-digit text blob that isn't the
bottom stat-panel strip (parser.find_meso_from_boxes). These tests cover
the selection logic directly with synthetic boxes, plus an end-to-end run
through the real OCR engine on a synthetic frame drawn like the actual
game screenshot (white digits on dark background -- the counter is NOT
gold in the real client).
"""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from maple_analyzer.parser import find_meso_candidate, find_meso_from_boxes, find_stat_fields

_DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_BG = (10, 12, 18)
_WHITE = (255, 255, 255)

pytestmark = pytest.mark.skipif(
    not _DEJAVU.exists(), reason="DejaVu font not available for synthetic frames"
)


# ---- pure selection logic ------------------------------------------------

def test_picks_largest_digit_blob():
    boxes = [
        (100, 100, 75, 21, "154,821"),   # the meso counter
        (50, 50, 43, 18, "320"),         # an item stack count
        (10, 10, 37, 23, "32"),          # the LV value
    ]
    assert find_meso_from_boxes(boxes, (1351, 800)) == 154_821


def test_excludes_stat_panel_strip():
    """LV/HP/MP/EXP live at the bottom of the client -- their LV value is
    also pure digits and must never be picked as meso."""
    boxes = [
        (488, 770, 37, 23, "32"),        # LV, inside the bottom strip
        (1615, 596, 75, 21, "154,821"),  # meso, well above it
    ]
    assert find_meso_from_boxes(boxes, (2045, 1151)) == 154_821


def test_tie_break_lower_blob_wins():
    """Item counts can tie the meso on digit count -- the counter sits at
    the bottom edge of the inventory window, below the item grid."""
    boxes = [
        (100, 100, 43, 18, "850"),   # item stack count (higher on screen)
        (100, 300, 43, 18, "750"),   # meso (lower on screen)
    ]
    assert find_meso_from_boxes(boxes, (1351, 800)) == 750


def test_non_digit_boxes_ignored():
    boxes = [
        (100, 100, 80, 21, "楓幣點數"),
        (100, 200, 80, 21, "獲得 5 楓幣"),
        (100, 300, 75, 21, "154,821"),
    ]
    assert find_meso_from_boxes(boxes, (1351, 800)) == 154_821


def test_no_digit_blobs_returns_none():
    assert find_meso_from_boxes([(10, 10, 50, 20, "ITEMINVENTORY")], (1351, 800)) is None
    assert find_meso_from_boxes([], (1351, 800)) is None


def test_only_stat_strip_digits_returns_none():
    # LV at its real screenshot position: y=1116 in a 1151-high client.
    boxes = [(488, 1116, 37, 23, "32")]
    assert find_meso_from_boxes(boxes, (2045, 1151)) is None


def test_candidate_returns_box_and_value():
    """find_meso_candidate returns the detected box too, so the overlay can
    cache the position and re-read it with cheap recognition-only OCR."""
    boxes = [
        (100, 100, 75, 21, "154,821"),
        (50, 50, 43, 18, "320"),
    ]
    found = find_meso_candidate(boxes, (1351, 800))
    assert found is not None
    x, y, w, h, value = found
    assert value == 154_821
    assert (x, y, w, h) == (100, 100, 75, 21)


def test_absurd_digit_blob_rejected():
    boxes = [(100, 100, 200, 21, "99999999999999999999")]
    assert find_meso_from_boxes(boxes, (1351, 800)) is None


# ---- stat panel location (find_stat_fields) ------------------------------

def test_find_stat_fields_matches_panel_text():
    """Detection boxes from a real-style frame: the four panel fields plus
    the meso counter. Only the panel fields must be located."""
    boxes = [
        (436, 1115, 89, 24, "LV. 32"),
        (750, 1101, 108, 24, "HP[602/602]"),
        (913, 1101, 129, 24, "MP[2100/2100]"),
        (1083, 1103, 148, 21, "EXP38829[3183%]"),
        (1615, 596, 75, 21, "154,821"),
    ]
    found = find_stat_fields(boxes)
    assert set(found) == {"LV", "HP", "MP", "EXP"}
    assert found["LV"] == (436, 1115, 89, 24)
    assert found["EXP"] == (1083, 1103, 148, 21)


def test_find_stat_fields_ignores_unrelated_text():
    boxes = [
        (1509, 144, 122, 18, "ITEMINVENTORY"),
        (1694, 626, 71, 22, "楓幣點數"),
        (1200, 262, 131, 27, "MapleStory"),
    ]
    assert find_stat_fields(boxes) == {}


def test_find_stat_fields_partial_panel():
    """If some fields are obscured, the visible ones are still located."""
    boxes = [(750, 1101, 108, 24, "HP[602/602]")]
    assert find_stat_fields(boxes) == {"HP": (750, 1101, 108, 24)}


def test_find_stat_fields_merges_split_lv():
    """Detection splits 'LV. 32' into 'LV.' + '32' boxes on some frames --
    the label must be merged with the digit box to its right."""
    boxes = [
        (79, 831, 41, 27, "LV."),
        (131, 833, 39, 23, "32"),
        (394, 821, 104, 20, "HP[602/602]"),
    ]
    found = find_stat_fields(boxes)
    assert "LV" in found
    x, y, w, h = found["LV"]
    # Merged box spans both the label and the digits.
    assert x <= 79 and x + w >= 170
    assert y <= 831 and y + h >= 858


# ---- end-to-end through the real OCR engine ------------------------------

def _game_like_frame():
    """White digits on dark background, laid out like the user's real
    screenshot: meso '154,821' top-right inside the inventory, item count
    '320' above it, LV '32' at the bottom strip."""
    img = Image.new("RGB", (2045, 1151), _BG)
    draw = ImageDraw.Draw(img)
    font_big = ImageFont.truetype(str(_DEJAVU), 22)
    font_small = ImageFont.truetype(str(_DEJAVU), 16)
    draw.text((1615, 596), "154,821", font=font_big, fill=_WHITE)
    draw.text((1707, 261), "320", font=font_small, fill=_WHITE)
    draw.text((488, 1116), "32", font=font_small, fill=_WHITE)
    return img


def test_ocr_roundtrip_reads_the_meso_number():
    """Full chain against the real OCR engine (detection over the whole
    frame, exactly like overlay._try_read_meso): the meso value is found
    even though the item count and the LV are also pure digits."""
    from maple_analyzer.ocr import StatPanelOcr

    frame = _game_like_frame()
    boxes = StatPanelOcr().detect_text(frame)
    assert find_meso_from_boxes(boxes, frame.size) == 154_821
