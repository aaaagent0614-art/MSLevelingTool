"""Thin wrapper around RapidOCR (PP-OCR detection+recognition, ONNX/CPU).

See VERSIONS.md for why rapidocr-onnxruntime stands in for the spec's originally
named PP-OCRv6-tiny ONNX model -- same OCR family, bundled models, no manual
model-file wiring.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

Point = tuple[float, float]


@dataclass
class OcrLine:
    text: str
    box: list[Point]  # 4 corner points, clockwise from top-left
    score: float

    @property
    def center(self) -> tuple[float, float]:
        xs = [p[0] for p in self.box]
        ys = [p[1] for p in self.box]
        return (sum(xs) / len(xs), sum(ys) / len(ys))


class StatPanelOcr:
    """Runs OCR once per frame and returns structured lines."""

    def __init__(self) -> None:
        self._engine = RapidOCR()

    def read(self, image: Image.Image) -> list[OcrLine]:
        result, _elapse = self._engine(np.array(image))
        if not result:
            return []
        return [OcrLine(text=text, box=box, score=score) for box, text, score in result]
