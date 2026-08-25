#!/usr/bin/env python
"""Real entrypoint: run against the actual MapleStory window. Windows only --
requires the game running and pywin32 installed (see VERSIONS.md). Run from
repo root:

    .venv\\Scripts\\python scripts\\run_overlay.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from maple_analyzer.capture import GameWindowCapture
from maple_analyzer.overlay import OverlayApp

if __name__ == "__main__":
    app = OverlayApp(GameWindowCapture(continuous=True))
    app.run()
