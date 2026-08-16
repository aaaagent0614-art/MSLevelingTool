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
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


class StatPanelOcr:
    def __init__(self) -> None:
        self._engine = RapidOCR()

    def read_field(self, image: Image.Image) -> str:
        """Recognition-only OCR on a small pre-cropped single-line field crop."""
        result, _elapse = self._engine(np.array(image), use_det=False, use_cls=False)
        if not result:
            return ""
        return result[0][0]
