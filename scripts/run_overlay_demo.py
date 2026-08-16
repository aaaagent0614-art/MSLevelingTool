#!/usr/bin/env python
"""Dev/demo entrypoint: real OCR+parse+rate+HUD pipeline against a synthetic
frame feed (no game window needed). Run from repo root:

    .venv/bin/python scripts/run_overlay_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from maple_analyzer.demo_feed import DemoExpFeed
from maple_analyzer.overlay import OverlayApp

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "maple_story_ui.jpg"

if __name__ == "__main__":
    app = OverlayApp(DemoExpFeed(SAMPLE))
    app.run()
