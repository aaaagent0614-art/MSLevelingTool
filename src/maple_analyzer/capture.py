"""Window capture abstraction.

`WindowCapture.grab_panel()` returns a PIL image of just the stat panel region.
Two implementations:

- `GameWindowCapture` (Windows only): finds the MapleStory window by title via
  pywin32, reads its client rect, grabs that rect off screen with `mss`. This is
  the real production path.
- `StaticImageCapture` (any platform): replays a single image file every call.
  Used for dev/testing on machines without the game running (e.g. this repo's
  Linux dev environment) -- proves out the OCR/parse/rate/overlay code without
  needing Windows + a live client.

Call `get_capture()` to get whichever is appropriate for the current platform;
pass `sample_path=` to force StaticImageCapture regardless of platform (useful
for demos/tests on Windows too).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from .regions import FIELD_BOXES, STAT_PANEL_BOX, scale_box


# Raised (as a RuntimeError message) when another window sits over the stat
# panel. Routine and recoverable, so it travels the same path as the
# minimized/not-found states -- see overlay._do_tick and _localize_error.
PANEL_OBSCURED = "stat panel is obscured"

# ---- meso counter detection ---------------------------------------------
#
# The meso counter only exists while the inventory is open, and the classic
# inventory is a draggable window -- a fixed pixel box (the FIELD_BOXES
# approach) can't find it. What IS stable is its appearance: the counter is
# bright gold digits on the dark inventory background, the only persistent
# gold text of that size in the classic UI (item names are white/blue/purple,
# the coin icon is a filled blob, not text). So we scan the whole frame for
# gold text-shaped blobs and OCR the widest one. None when the inventory is
# closed (no gold text on screen at all).

# "Gold" here: strong red + green, weak blue. Tuned against the sample
# client screenshots (bottom panel chat text reads white, the LV/HP/MP/EXP
# strip is white-on-dark, so nothing there matches).
GOLD_MIN_R = 140
GOLD_MIN_G = 100
GOLD_MAX_B = 130
# A meso amount is a *row* of digit glyphs -- wide and short. The coin icon
# is a single roughly-square blob. Individual digit components are grouped
# into rows first, then the row's overall aspect ratio is checked (each
# digit alone is ~0.7-0.8 wide/tall, so per-component aspect would reject
# every one of them -- see the component stats in test_meso.py's synthetic
# frame debug). 1.5 catches 3-digit amounts while still excluding the
# square coin icon and gold monster-name tags (transient, small).
MESO_MIN_HEIGHT = 8
MESO_MIN_ASPECT = 1.5
MESO_MIN_FILL = 0.12  # strokes are thin -- a filled blob is not text
MESO_PAD = 4


def find_meso_crop(frame: Image.Image) -> Image.Image | None:
    """Locate the gold meso counter in a full client frame and return a tight
    crop around it (PIL image, ready for recognition-only OCR). None when no
    gold text-shaped blob is visible -- i.e. the inventory is closed.

    Digit glyphs come out of connected-components as separate blobs (each
    digit its own component, aspect ~0.7), so the text check happens per
    *row*: components whose vertical spans overlap are grouped into a line,
    and the line must be wide relative to its height."""
    arr = np.asarray(frame.convert("RGB"))
    mask = (
        (arr[..., 0] >= GOLD_MIN_R)
        & (arr[..., 1] >= GOLD_MIN_G)
        & (arr[..., 2] <= GOLD_MAX_B)
    ).astype(np.uint8)
    if not mask.any():
        return None

    import cv2  # opencv-python-headless is a hard dep; import lazily to keep import cost off the hot path

    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    comps: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if h >= MESO_MIN_HEIGHT and area >= w * h * MESO_MIN_FILL:
            comps.append((int(x), int(y), int(w), int(h)))
    if not comps:
        return None

    # Group into rows by vertical-span overlap (sorted top-to-bottom so a
    # component joins the first row it touches).
    comps.sort(key=lambda c: (c[1], c[0]))
    rows: list[list[tuple[int, int, int, int]]] = []
    for c in comps:
        for row in rows:
            ry0 = min(cc[1] for cc in row)
            ry1 = max(cc[1] + cc[3] for cc in row)
            if c[1] < ry1 and c[1] + c[3] > ry0:
                row.append(c)
                break
        else:
            rows.append([c])

    best: tuple[int, int, int, int] | None = None
    best_w = 0
    for row in rows:
        x0 = min(c[0] for c in row)
        x1 = max(c[0] + c[2] for c in row)
        y0 = min(c[1] for c in row)
        y1 = max(c[1] + c[3] for c in row)
        rw, rh = x1 - x0, y1 - y0
        if rh <= 0 or rw < rh * MESO_MIN_ASPECT:
            continue  # square-ish blob (coin icon) or a stray small tag
        if rw > best_w:
            best = (x0, y0, rw, rh)
            best_w = rw
    if best is None:
        return None
    x, y, w, h = best
    box = (
        max(0, x - MESO_PAD), max(0, y - MESO_PAD),
        min(frame.width, x + w + MESO_PAD), min(frame.height, y + h + MESO_PAD),
    )
    return frame.crop(box)


def field_sample_points(client_size: tuple[int, int]) -> list[tuple[int, int]]:
    """Client-relative points to probe for occlusion: the four corners of each
    FIELD_BOX, inset by a pixel so a corner lands inside its own box.

    Sampling per *field* rather than the panel as a whole is deliberate. A
    window clipping only the MP digits is the dangerous case -- the 'MP' label
    stays readable so the value still parses, just wrong -- and a panel-level
    check with a few points can miss it.
    """
    points: list[tuple[int, int]] = []
    for box in FIELD_BOXES.values():
        b = scale_box(box, client_size)
        points += [
            (b.left + 1, b.top + 1), (b.right - 2, b.top + 1),
            (b.left + 1, b.bottom - 2), (b.right - 2, b.bottom - 2),
        ]
    return points


def panel_is_obscured(sample_points, game_hwnd: int, window_at) -> bool:
    """True if any sample point belongs to a window other than the game.

    `window_at(x, y)` returns the *root* window at a screen point; injected so
    this stays testable without a Win32 desktop. Any single covered point
    counts -- there is no threshold, because partial coverage corrupts values
    rather than merely hiding them.
    """
    return any(window_at(x, y) != game_hwnd for x, y in sample_points)


class WindowCapture(Protocol):
    def grab_full(self) -> Image.Image:
        """Full client-area frame."""
        ...

    def grab_panel(self) -> Image.Image:
        """Just the stat panel crop, scaled to the current client size."""
        ...

    def grab_fields(self) -> dict[str, Image.Image]:
        """One crop per FIELD_BOXES entry ('LV'/'HP'/'MP'/'EXP'), for
        recognition-only OCR -- see ocr.py's read_field()."""
        ...

    def grab_meso(self) -> Image.Image | None:
        """Crop around the gold meso counter (inventory open), or None when
        no gold text is visible. Independent of the stat-panel occlusion
        probe -- opening the inventory obscures the panel AND is exactly
        when meso becomes readable."""
        ...


class StaticImageCapture:
    """Dev/demo stand-in: always returns (a copy of) one image from disk."""

    def __init__(self, path: str | Path):
        self._image = Image.open(path).convert("RGB")

    def grab_full(self) -> Image.Image:
        return self._image.copy()

    def grab_panel(self) -> Image.Image:
        box = scale_box(STAT_PANEL_BOX, self._image.size)
        return self._image.crop(box.as_tuple())

    def grab_fields(self) -> dict[str, Image.Image]:
        return {
            name: self._image.crop(scale_box(box, self._image.size).as_tuple())
            for name, box in FIELD_BOXES.items()
        }

    def grab_meso(self) -> Image.Image | None:
        return find_meso_crop(self._image)


class GameWindowCapture:
    """Real capture: locates the game window by title and grabs its client area."""

    def __init__(self, title_substring: str = "新楓之谷", process_name: str = "Maplestory"):
        if sys.platform != "win32":
            raise RuntimeError("GameWindowCapture requires Windows (pywin32 + real desktop)")
        import mss
        import win32api
        import win32con
        import win32gui
        import win32process

        self._win32gui = win32gui
        self._win32process = win32process
        self._win32api = win32api
        self._win32con = win32con
        self._mss = mss.mss()
        self._title_substring = title_substring
        self._process_name = process_name.lower()
        self._hwnd: int | None = None
        # Whether grab_fields refuses frames whose stat panel is covered by
        # another window (see panel_is_obscured). Off by default; the
        # overlay can flip it (Settings -> ignore occlusion) to keep reading
        # whatever is on screen -- useful with magnifier overlays, with the
        # caveat that covered pixels are the covering window's, so OCR may
        # read garbage. Meso capture (grab_meso) is never affected: it runs
        # its own scan and the whole point is that the inventory covers the
        # panel.
        self.check_occlusion: bool = True
        # Last client size seen by grab_fields, for the overlay to log. Every
        # crop in regions.py is scaled from this, so it is the single most
        # useful number when diagnosing a bad read from a log after the fact
        # -- and the one thing missing from every capture taken so far.
        self.client_size: tuple[int, int] | None = None

    def _owning_process_name(self, hwnd: int) -> str:
        # Title alone isn't a reliable match: e.g. a browser tab for a wiki page
        # about the game can also contain the title substring. Require the
        # window's actual owning process to match too.
        try:
            _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
            # PROCESS_VM_READ is denied for this game (anti-tamper protection) --
            # PROCESS_QUERY_LIMITED_INFORMATION alone is enough for
            # GetModuleFileNameEx and works even on protected processes.
            handle = self._win32api.OpenProcess(
                self._win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            try:
                path = self._win32process.GetModuleFileNameEx(handle, 0)
                return path.rsplit("\\", 1)[-1]
            finally:
                self._win32api.CloseHandle(handle)
        except Exception:
            return ""

    def _is_match(self, hwnd: int) -> bool:
        title = self._win32gui.GetWindowText(hwnd)
        if self._title_substring not in title:
            return False
        return self._process_name in self._owning_process_name(hwnd).lower()

    def _find_window(self) -> int:
        # IsWindow() alone isn't enough: if the game process exits, Windows can
        # recycle its hwnd number for an unrelated window, and IsWindow() stays
        # True for that new window -- silently capturing garbage instead of
        # erroring. Re-check title+process on every call to catch that.
        if self._hwnd and self._win32gui.IsWindow(self._hwnd) and self._is_match(self._hwnd):
            return self._hwnd
        self._hwnd = None

        found: list[int] = []

        def _cb(hwnd: int, _):
            if self._is_match(hwnd):
                found.append(hwnd)

        self._win32gui.EnumWindows(_cb, None)
        if not found:
            raise RuntimeError(f"No window found with title containing {self._title_substring!r}")
        self._hwnd = found[0]
        return self._hwnd

    def _client_rect_on_screen(self) -> tuple[int, int, int, int]:
        hwnd = self._find_window()
        if self._win32gui.IsIconic(hwnd):
            # Minimized windows report a client rect around (-32000, -32000)
            # with zero size -- mss.grab() throws a raw ScreenShotError on
            # that instead of anything actionable. Fail clearly here so
            # callers (the overlay) can show 'game minimized' and retry,
            # same as the 'game not found' case.
            raise RuntimeError("game window is minimized")
        left, top, right, bottom = self._win32gui.GetClientRect(hwnd)
        left, top = self._win32gui.ClientToScreen(hwnd, (left, top))
        right, bottom = self._win32gui.ClientToScreen(hwnd, (right, bottom))
        return left, top, right, bottom

    def grab_full(self) -> Image.Image:
        left, top, right, bottom = self._client_rect_on_screen()
        shot = self._mss.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def grab_panel(self) -> Image.Image:
        left, top, right, bottom = self._client_rect_on_screen()
        client_size = (right - left, bottom - top)
        box = scale_box(STAT_PANEL_BOX, client_size)
        shot = self._mss.grab({
            "left": left + box.left,
            "top": top + box.top,
            "width": box.right - box.left,
            "height": box.bottom - box.top,
        })
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def _root_window_at(self, x: int, y: int) -> int:
        # GA_ROOT (2) resolves a child window/control to its top-level owner,
        # so the game's own children don't read as something covering it.
        return self._win32gui.GetAncestor(self._win32gui.WindowFromPoint((x, y)), 2)

    def grab_fields(self) -> dict[str, Image.Image]:
        # One screen grab covering the whole panel (mss itself is cheap, ~3.5ms
        # measured -- see VERSIONS.md/overlay.py timing notes), then slice each
        # field out of that single in-memory image rather than four separate
        # mss.grab() calls.
        left, top, right, bottom = self._client_rect_on_screen()
        client_size = (right - left, bottom - top)
        self.client_size = client_size

        # mss grabs the screen *region* where the panel sits, not the game's
        # own pixels, so anything on top of it is what would reach OCR. Refuse
        # the frame instead of reading someone else's window (~0.03ms measured
        # for the whole check, against ~60ms of OCR) -- unless the user opted
        # out via Settings (ignore occlusion), which reads whatever is there.
        points = [(left + x, top + y) for x, y in field_sample_points(client_size)]
        if self.check_occlusion and panel_is_obscured(points, self._hwnd, self._root_window_at):
            raise RuntimeError(PANEL_OBSCURED)

        panel_box = scale_box(STAT_PANEL_BOX, client_size)
        shot = self._mss.grab({
            "left": left + panel_box.left,
            "top": top + panel_box.top,
            "width": panel_box.right - panel_box.left,
            "height": panel_box.bottom - panel_box.top,
        })
        panel = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

        fields = {}
        for name, box in FIELD_BOXES.items():
            field_box = scale_box(box, client_size)
            local = (
                field_box.left - panel_box.left, field_box.top - panel_box.top,
                field_box.right - panel_box.left, field_box.bottom - panel_box.top,
            )
            fields[name] = panel.crop(local)
        return fields

    def grab_meso(self) -> Image.Image | None:
        """Full client-area grab, then the gold-text scan (find_meso_crop).

        Deliberately skips the panel_is_obscured probe: the inventory
        window covers the stat panel whenever meso is readable, so gating
        meso on the panel being clear would make it permanently unreadable."""
        left, top, right, bottom = self._client_rect_on_screen()
        shot = self._mss.grab({
            "left": left, "top": top, "width": right - left, "height": bottom - top,
        })
        frame = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return find_meso_crop(frame)


def get_capture(sample_path: str | Path | None = None) -> WindowCapture:
    if sample_path is not None:
        return StaticImageCapture(sample_path)
    if sys.platform == "win32":
        return GameWindowCapture()
    raise RuntimeError(
        "Real game capture requires Windows. Pass sample_path= to use "
        "StaticImageCapture for dev/testing on this platform."
    )
