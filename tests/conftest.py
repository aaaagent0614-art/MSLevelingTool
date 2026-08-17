import sys
from pathlib import Path

# Same src-on-path convention as scripts/run_overlay*.py -- this repo has no
# packaging config (no pyproject/setup.py), so tests need the same manual
# sys.path insert to import maple_analyzer.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

SAMPLE_IMAGE = Path(__file__).resolve().parent.parent / "samples" / "maple_story_ui.jpg"
