"""Crop-box definitions for the stat panel and its fields.

v1 approach: fixed pixel boxes, measured directly off samples/maple_story_ui.jpg
(1351x800 client area). This is a placeholder for the spec's real plan (template-
match a UI anchor icon, then derive boxes as anchor-relative offsets x scale factor
so it survives resolution/DPI changes) -- see VERSIONS.md / spec-draft. Swap
`locate_panel()` for a template-match implementation before relying on this outside
dev/demo use at this exact resolution.
"""
from __future__ import annotations

from dataclasses import dataclass

REFERENCE_CLIENT_SIZE = (1351, 800)  # size of samples/maple_story_ui.jpg

# Whole stat panel, in absolute pixels at REFERENCE_CLIENT_SIZE. Grabbed once per
# tick with a single mss.grab() call; FIELD_BOXES below are sliced out of it
# in-memory (no extra screen captures).
STAT_PANEL_BOX = (260, 758, 900, 800)  # (left, top, right, bottom)

# Per-field boxes, same reference frame as STAT_PANEL_BOX, generously padded
# around each field's label+value text (measured off samples/maple_story_ui.jpg,
# see commit history for the crop-and-inspect process). Recognition-only OCR
# (no detection) runs on each of these individually -- see ocr.py's read_field()
# docstring for why this replaced running detection over the whole panel
# (detection was ~600ms/call, the actual OCR bottleneck; recognition-only on a
# small pre-cropped box is ~15ms).
FIELD_BOXES = {
    "LV": (278, 774, 362, 799),
    "HP": (486, 767, 600, 787),
    "MP": (600, 767, 712, 787),
    "EXP": (712, 767, 858, 787),
}


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int
    bottom: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


def scale_box(box: tuple[int, int, int, int], client_size: tuple[int, int]) -> Box:
    """Scale a box defined at REFERENCE_CLIENT_SIZE to an arbitrary client size.

    Naive linear scaling -- fine as a stopgap since the game UI panel is
    bottom-anchored and roughly resolution-proportional, but not a substitute for
    real anchor template-matching (fonts/icons don't scale linearly in practice).
    """
    ref_w, ref_h = REFERENCE_CLIENT_SIZE
    cw, ch = client_size
    sx, sy = cw / ref_w, ch / ref_h
    l, t, r, b = box
    return Box(round(l * sx), round(t * sy), round(r * sx), round(b * sy))
