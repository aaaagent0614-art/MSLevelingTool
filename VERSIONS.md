# Environment / Reproducibility

## Python

**Windows (primary dev target, since 2026-08-17): 3.10**, via the `py` launcher
(`py -3.10`). This machine's `py -0` only lists 3.10 and 3.12 installed — no 3.11 — so
the Windows venv uses 3.10 rather than installing a new interpreter just for the pin.
All deps below have mature 3.10 wheels; verified working end-to-end against the live
game (see README "Status").

**Linux/WSL (secondary, used only for OCR/parser prototyping before real-game
testing was possible): 3.11.11**, pinned via `pyenv` (`.python-version` in repo root).

The app is fundamentally Windows-native — real capture requires `pywin32` + a live
Win32 desktop — so Windows is the environment of record going forward. The `.venv`
lock files differ per platform (see Locking below); don't assume the Linux lock
reflects what's actually running the app.

## Setup (Windows — primary; real window capture + overlay HUD)

```powershell
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python .venv\Scripts\pywin32_postinstall.py -install
```

## Setup (Linux dev / WSL — secondary, OCR/parser work only, no real capture)

```bash
pyenv install -s 3.11.11      # picks up .python-version automatically after this
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Locking

`requirements.txt` lists top-level deps only, platform-gating `pywin32` to Windows.
Two separate lock files exist since the two platforms installed different Python
minor versions and dependency resolutions:

- `requirements.win.lock.txt` — Windows venv (`py -3.10`), includes `pywin32`. This is
  the one that matters — it's what's actually running the app.
- `requirements.lock.txt` — Linux/WSL venv (3.11.11), no `pywin32`. Reference only.

Regenerate after any dependency change, on whichever platform you changed it on:

```powershell
.venv\Scripts\pip freeze | Out-File -FilePath requirements.win.lock.txt -Encoding utf8
```
```bash
.venv/bin/pip freeze > requirements.lock.txt
```

(`Out-File -Encoding utf8` avoids PowerShell's default UTF-16LE + BOM, which breaks
`pip install -r` on the resulting file.)

## Key package versions (Windows, from requirements.win.lock.txt, 2026-08-17)

| package | version |
|---|---|
| numpy | 2.2.6 |
| opencv-python-headless | 5.0.0.93 |
| pillow | 12.3.0 |
| mss | 10.2.0 |
| onnxruntime | 1.23.2 |
| rapidocr-onnxruntime | 1.4.4 |
| pywin32 | 312 |

## Note on OCR engine

Spec originally called for a standalone PP-OCRv6-tiny ONNX model (matching
MapleStoryExpTool's approach). For the prototype we use `rapidocr-onnxruntime`, which
bundles PP-OCR detection + recognition models (ONNX, CPU) with a simple Python API —
same underlying OCR family, no separate model download/wiring needed. Revisit swapping
in a hand-picked PP-OCRv6-tiny model directly if we need to shave inference latency
later; not a blocker for the prototype.
