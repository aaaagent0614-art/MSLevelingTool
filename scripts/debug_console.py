#!/usr/bin/env python
"""Terminal debug loop: waits for the game window, then logs every OCR tick
(raw detected lines + parsed snapshot) to stdout. Run from repo root on
Windows, with the game running or about to be started:

    .venv\\Scripts\\python scripts\\debug_console.py

Ctrl+C to stop.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from maple_analyzer.capture import GameWindowCapture
from maple_analyzer.ocr import StatPanelOcr
from maple_analyzer.parser import parse_stat_lines

WAIT_RETRY_S = 2


def wait_for_game(cap: GameWindowCapture) -> None:
    while True:
        try:
            cap._find_window()  # noqa: SLF001 -- deliberate: just probing, no capture yet
            return
        except RuntimeError:
            print(f"[{time.strftime('%H:%M:%S')}] waiting for game window "
                  f"(title contains {cap._title_substring!r}, process {cap._process_name!r})...")
            time.sleep(WAIT_RETRY_S)


def main() -> None:
    cap = GameWindowCapture()
    ocr = StatPanelOcr()

    print("Waiting for MapleStory window...")
    wait_for_game(cap)
    print("Found it. Starting OCR loop (Ctrl+C to stop).\n")

    while True:
        try:
            frame = cap.grab_panel()
        except RuntimeError as e:
            print(f"[{time.strftime('%H:%M:%S')}] lost game window ({e}); waiting again...")
            wait_for_game(cap)
            continue

        t0 = time.perf_counter()
        lines = ocr.read(frame)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        snap = parse_stat_lines(lines)

        ts = time.strftime("%H:%M:%S")
        raw = [l.text for l in lines]
        print(f"[{ts}] ocr={elapsed_ms:.0f}ms  raw={raw}")
        print(f"          -> {snap}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
