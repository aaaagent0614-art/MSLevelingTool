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

# Whole stat panel, in absolute pixels at REFERENCE_CLIENT_SIZE. OCR runs once on
# this crop and all fields are parsed out of the result, rather than OCR-ing each
# field separately -- fewer inference calls, and labels ("HP"/"MP"/"EXP") make
# unambiguous anchors for the regex/position-based parser.
STAT_PANEL_BOX = (260, 758, 900, 800)  # (left, top, right, bottom)


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
