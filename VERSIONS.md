# Environment / Reproducibility

## Python

**3.11.11**, pinned via `pyenv` (`.python-version` file in repo root — `pyenv local 3.11.11`).

Chosen over 3.13 (the sandbox's ambient interpreter) because `onnxruntime` /
`rapidocr-onnxruntime` / `opencv-python-headless` all publish mature prebuilt wheels for
3.11 across Linux and Windows; 3.13 wheel coverage for this stack is inconsistent as of
2026-08. 3.11 also matches current pywin32 wheel support for the Windows deployment target.

## Setup (Linux dev / WSL — used for OCR + parser prototyping)

```bash
pyenv install -s 3.11.11      # picks up .python-version automatically after this
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Setup (Windows — used for real window capture + overlay HUD)

Same steps, but `pywin32` (platform-gated in requirements.txt) also installs, and its
post-install script must be run once:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python .venv\Scripts\pywin32_postinstall.py -install
```

## Locking

`requirements.txt` lists top-level deps only. `requirements.lock.txt` is the full
`pip freeze` output from the Linux dev `.venv` — exact versions known to work together.
Regenerate after any dependency change:

```bash
.venv/bin/pip freeze > requirements.lock.txt
```

The Linux lock file omits `pywin32` (platform-gated, doesn't install here). When
setting up the Windows runtime venv, freeze a second lock file there
(`requirements.win.lock.txt`) once pywin32 version is known, rather than guessing a pin
from Linux.

## Key package versions (as of 2026-08-17, from requirements.lock.txt)

| package | version |
|---|---|
| numpy | 2.4.6 |
| opencv-python-headless | 5.0.0.93 |
| pillow | 12.3.0 |
| mss | 10.2.0 |
| onnxruntime | 1.28.0 |
| rapidocr-onnxruntime | 1.4.4 |

## Note on OCR engine

Spec originally called for a standalone PP-OCRv6-tiny ONNX model (matching
MapleStoryExpTool's approach). For the prototype we use `rapidocr-onnxruntime`, which
bundles PP-OCR detection + recognition models (ONNX, CPU) with a simple Python API —
same underlying OCR family, no separate model download/wiring needed. Revisit swapping
in a hand-picked PP-OCRv6-tiny model directly if we need to shave inference latency
later; not a blocker for the prototype.
