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

## Layout

- `samples/` — reference screenshots for OCR crop-box/regex development
- (capture/OCR/analysis modules TBD)
