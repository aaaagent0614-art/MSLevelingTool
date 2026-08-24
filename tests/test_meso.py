"""Meso counter detection: find_meso_crop against synthetic frames.

The real game's inventory counter is bright gold digits on a dark
background; these tests draw that exact pattern (gold text on near-black)
and verify the detector finds the text blob and rejects non-gold frames.
No Windows/win32 needed -- the whole detection path is pure numpy/cv2.

The end-to-end test also runs the real OCR engine over the synthetic
counter, proving the full chain (gold scan -> crop -> OCR -> parse_meso)
produces the right number without a live game.
"""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from maple_analyzer.capture import find_meso_crop

_DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_GOLD = (255, 200, 50)
_BG = (10, 12, 18)

pytestmark = pytest.mark.skipif(
    not _DEJAVU.exists(), reason="DejaVu font not available for synthetic frames"
)


def _frame(text="1,234,567", size=(1351, 800), font_size=22, fill=_GOLD):
    img = Image.new("RGB", size, _BG)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(_DEJAVU), font_size)
    draw.text((600, 500), text, font=font, fill=fill)
    return img


def test_no_gold_text_returns_none():
    assert find_meso_crop(Image.new("RGB", (1351, 800), _BG)) is None


def test_gold_text_is_found_and_cropped_tightly():
    crop = find_meso_crop(_frame())
    assert crop is not None
    # Tight crop around one line of digits: wide-ish and short.
    assert crop.width < 400
    assert 8 <= crop.height <= 60
    # The crop must actually contain the gold text (not a blank corner).
    import numpy as np

    arr = np.asarray(crop.convert("RGB"))
    assert (
        (arr[..., 0] >= 140) & (arr[..., 1] >= 100) & (arr[..., 2] <= 130)
    ).any()


def test_non_gold_text_is_rejected():
    # Same layout, but white text -- the stat panel / chat text color.
    assert find_meso_crop(_frame(fill=(255, 255, 255))) is None


def test_coin_icon_blob_is_not_mistaken_for_text():
    """The meso coin icon is a filled gold blob (roughly square), not text.
    It must not be picked as the meso crop."""
    img = Image.new("RGB", (1351, 800), _BG)
    draw = ImageDraw.Draw(img)
    draw.ellipse((600, 500, 640, 540), fill=_GOLD)  # 40x40 filled circle
    assert find_meso_crop(img) is None


def test_ocr_roundtrip_reads_the_gold_number():
    """Full chain against the real OCR engine: gold digits -> crop -> OCR
    -> parse_meso yields the exact counter value."""
    from maple_analyzer.ocr import StatPanelOcr
    from maple_analyzer.parser import parse_meso

    crop = find_meso_crop(_frame("1,234,567"))
    assert crop is not None
    text = StatPanelOcr().read_field(crop)
    assert parse_meso(text) == 1_234_567
