# MapleStoryAnalyer

Python desktop app that reads HP/MP/EXP/Level from the MapleStory game window via
screen capture + OCR, to analyze grinding efficiency (EXP/hr, rate trends, session
comparison). Read-only observer — no input automation.

Full spec: `~/.claude/notes/maplestory-analyzer/spec-draft-2026-08-17.md`

## Status

Spec drafted. Confirmed client: MapleStory Worlds — 新楓之谷:經典版 (Artale/Classic),
Traditional Chinese UI. Sample screenshot in `samples/maple_story_ui.jpg`.

Confirmed stat bar format (bottom-left panel):
- `LV.` — plain integer
- HP: `[cur/max]`, e.g. `[377/824]`
- MP: `[cur/max]`, e.g. `[1663/2816]`
- EXP: `cur[percentage%]`, e.g. `162950[38.05%]` — absolute value AND percentage
  shown together, not either/or as originally assumed. Parser should extract both
  and use whichever is more precise (absolute cur value) as the primary signal.

## Status

Capture/OCR/parse/rate/overlay pipeline scaffolded and working end-to-end against
the sample screenshot (via a synthetic frame feed, since dev happens off-Windows —
see `demo_feed.py`). Not yet wired to a real Windows game window or tested there.

## Setup

See `VERSIONS.md` for the pinned Python 3.11.11 + locked dependency set and the
Linux-dev vs. Windows-runtime setup steps.

## Try it (dev/demo mode, no game needed)

```bash
.venv/bin/python scripts/run_overlay_demo.py
```

Opens an always-on-top HUD window and feeds it synthetic frames derived from
`samples/maple_story_ui.jpg` (real EXP text redrawn with an incrementing value
each tick) through the *real* OCR/parse/rate pipeline — proves the pipeline logic
without needing the actual game running. Close the window to stop.

## Run for real (Windows, game running)

```powershell
.venv\Scripts\python scripts\run_overlay.py
```

Requires `pywin32` installed (see VERSIONS.md) and the game window title
containing `新楓之谷` (adjust `GameWindowCapture(title_substring=...)` if your
client's window title differs).

## Layout

- `samples/` — reference screenshots for OCR crop-box/regex development
- `src/maple_analyzer/`
  - `capture.py` — window capture: real (`GameWindowCapture`, Windows/pywin32+mss)
    and dev stand-in (`StaticImageCapture`)
  - `regions.py` — stat-panel crop box (fixed pixel box measured off the sample
    screenshot; **placeholder** for the spec's real plan of template-matching a UI
    anchor icon so boxes scale properly across resolutions — not yet built)
  - `ocr.py` — RapidOCR wrapper (see VERSIONS.md for why this stands in for the
    spec's originally-named standalone PP-OCRv6-tiny ONNX model)
  - `parser.py` — regex + position-based extraction of LV/HP/MP/EXP from OCR
    output, handling both the merged (`HP[377/824]`) and split (`LV.` + `44`)
    box cases RapidOCR produces on this UI
  - `rate.py` — time-windowed EXP/hr tracker (1/10/60-min), idle detection,
    level-up (EXP reset) handling
  - `demo_feed.py` — synthetic live-frame generator for dev/demo (see above)
  - `overlay.py` — tkinter always-on-top HUD tying the above together on a
    500ms poll tick
- `scripts/run_overlay_demo.py`, `scripts/run_overlay.py` — entrypoints

## Not yet built (from spec, still open)

- Anchor template-matching for resolution/DPI-independent crop boxes (currently
  fixed pixel boxes calibrated to the one sample screenshot's resolution)
- Pixel-diff skip-check before re-running OCR (currently OCR runs every tick)
- SQLite session logging
- Session summaries / map-context tagging
- Verifying `GameWindowCapture` against the actual live game window (untested —
  no Windows machine in this dev environment)
