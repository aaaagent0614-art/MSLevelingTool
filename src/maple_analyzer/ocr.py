"""Thin wrapper around RapidOCR (PP-OCR recognition, ONNX/CPU).

See VERSIONS.md for why rapidocr-onnxruntime stands in for the spec's originally
named PP-OCRv6-tiny ONNX model -- same OCR family, bundled models, no manual
model-file wiring.

Recognition-only, not detection+recognition: benchmarked live against the real
game (2026-08-17), detection (finding text regions in an image) was ~600-680ms
per call -- the entire OCR bottleneck the rest of the pipeline was tuned around.
Recognition alone (reading a pre-cropped, known-to-contain-one-line-of-text
image) was ~15ms. Since regions.py's FIELD_BOXES already pins down exactly
where each field's text is, running detection to *re-discover* that on every
tick was pure waste -- see capture.py's grab_fields() for the cropping side of
this change.

Two engines:
- `StatPanelOcr` -- PP-OCRv4 Simplified-Chinese recognition, for the numeric
  stat panel (LV/HP/MP/EXP). v4 is the newest/most accurate on digits.
- `MapNameOcr` -- PP-OCRv3 Traditional-Chinese (`chinese_cht`) recognition, for
  the map-name banner. The Simplified model's 6623-char dictionary does NOT
  contain the Traditional forms 螞/蟻/…, so a Traditional map name could never
  be read correctly no matter how clean the capture; the `chinese_cht` dict
  (8421 chars) does contain them, which removes that hard ceiling.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


def _resource_path(relative: str) -> str:
    """Locate a data file bundled with this package.

    When frozen (PyInstaller), model files are unpacked under `sys._MEIPASS`
    (the `_internal` dir for one-folder builds); in dev they sit next to this
    source file. `relative` is the path under the `maple_analyzer` package.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", str(Path(sys.executable).resolve().parent))
        return str(Path(base) / "maple_analyzer" / relative)
    return str(Path(__file__).resolve().parent / relative)


class StatPanelOcr:
    def __init__(self) -> None:
        self._engine = RapidOCR()

    def read_field(self, image: Image.Image) -> str:
        """Recognition-only OCR on a small pre-cropped single-line field crop."""
        result, _elapse = self._engine(np.array(image), use_det=False, use_cls=False)
        if not result:
            return ""
        return result[0][0]

    def detect_text(self, image: Image.Image) -> list[tuple[int, int, int, int, str]]:
        """Full detection over an arbitrary frame: returns (x, y, w, h, text)
        tuples in image-pixel coordinates.

        Expensive (~600ms measured on the game's panel-sized crops, more on a
        full frame) -- the module docstring explains why the per-tick stat
        path avoids detection entirely. For meso the box genuinely isn't
        known in advance (the inventory is draggable), so a full scan is the
        only reliable way to find it; callers are expected to throttle it
        (see overlay's MESO_SCAN_INTERVAL_TICKS)."""
        result, _elapse = self._engine(np.array(image), use_det=True, use_cls=False)
        if not result:
            return []
        boxes: list[tuple[int, int, int, int, str]] = []
        for box, text, _score in result:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            boxes.append(
                (int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys)), text)
            )
        return boxes


class MapNameOcr:
    """Traditional-Chinese OCR for the map-name banner (see module docstring)."""

    def __init__(self) -> None:
        self._engine = RapidOCR(
            rec_model_path=_resource_path("models/chinese_cht_rec.onnx"),
            rec_keys_path=_resource_path("models/chinese_cht_dict.txt"),
        )

    def read_map_name(self, image: Image.Image) -> str:
        """OCR the marked map region: detection + a left-to-right join, so the
        name is isolated from a wider banner crop (channel/level text around
        it) and a split name ('螞蟻洞' + 'I') stays together."""
        result, _elapse = self._engine(np.array(image), use_det=True, use_cls=False)
        if not result:
            return ""
        items: list[tuple[int, str]] = []
        for box, text, _score in result:
            xs = [p[0] for p in box]
            items.append((min(xs), text))
        items.sort()
        return "".join(t for _, t in items).strip()
