import sys
from pathlib import Path

# Same src-on-path convention as scripts/run_overlay*.py -- this repo has no
# packaging config (no pyproject/setup.py), so tests need the same manual
# sys.path insert to import maple_analyzer.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# tests/ is not a package (no __init__.py), so a sibling fixture module like
# captured_frames.py isn't importable by default -- add tests/ itself too.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SAMPLE_IMAGE = Path(__file__).resolve().parent.parent / "samples" / "maple_story_ui.jpg"

# A second real client screenshot at a different size and aspect ratio, for
# checking that the proportional crops in regions.py survive the trip. The
# game letterboxes (black bars, panel bottom-centre), so panel position tracks
# aspect ratio and not just scale -- one sample per aspect is the point.
SAMPLE_IMAGE_1920 = Path(__file__).resolve().parent.parent / "samples" / "maple_story_ui_1920.jpg"
