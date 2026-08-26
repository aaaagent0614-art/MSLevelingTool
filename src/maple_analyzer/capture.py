"""Window capture abstraction.

`WindowCapture.grab_panel()` returns a PIL image of just the stat panel region.
Two implementations:

- `GameWindowCapture` (Windows only): finds the MapleStory window by title via
  pywin32 and reads the window's OWN rendered frame with the Win32 PrintWindow
  API (PW_RENDERFULLCONTENT). Reading the window's DWM backing store -- rather
  than grabbing a screen *region* with mss -- is what makes the HUD immune to
  other windows resting on top of the game, and to screen magnifiers like
  Magpie. Magpie captures the game and renders a scaled copy into its own
  window over the top; the game window underneath keeps its original client
  size and keeps rendering, so PrintWindow reads the correct, unscaled frame
  regardless of how the magnifier rescales what is on screen.
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
import threading
import time
from pathlib import Path
from typing import Protocol

from PIL import Image

from .regions import FIELD_BOXES, STAT_PANEL_BOX, scale_box


# PrintWindow flags. PW_CLIENTONLY limits the render to the client area;
# PW_RENDERFULLCONTENT asks DWM for the window's own composited frame, which
# is the whole point -- it ignores whatever window sits on top of the game.
PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002


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


class GameWindowCapture:
    """Real capture: locates the game window by title and reads its own
    rendered frame via PrintWindow(PW_RENDERFULLCONTENT)."""

    def __init__(
        self,
        title_substring: str = "新楓之谷",
        process_name: str = "Maplestory",
        continuous: bool = False,
    ):
        if sys.platform != "win32":
            raise RuntimeError("GameWindowCapture requires Windows (pywin32 + real desktop)")
        import win32api
        import win32con
        import win32gui
        import win32process

        self._win32gui = win32gui
        self._win32process = win32process
        self._win32api = win32api
        self._win32con = win32con
        self._title_substring = title_substring
        self._process_name = process_name.lower()
        self._hwnd: int | None = None
        # Last client size seen by a grab, for the overlay to log. Every
        # crop in regions.py is scaled from this, so it is the single most
        # useful number when diagnosing a bad read from a log after the fact
        # -- and the one thing missing from every capture taken so far.
        self.client_size: tuple[int, int] | None = None
        # WGC (Windows Graphics Capture) availability, probed lazily on the
        # first grab. WGC reads the window's own composited frame -- occlusion
        # proof AND able to capture DirectX games (PrintWindow returns black for
        # MapleStory's D3D surface). None = not yet probed.
        self._wgc_available: bool | None = None
        # Continuous-capture mode (used by the persistent instance the tick
        # loop drives): a daemon thread keeps ONE WGC session alive and caches
        # the latest client-area frame, so grab_full() never blocks the UI
        # thread on a per-tick session create/wait/teardown (the lag fix).
        self._continuous = continuous
        self._stream_lock = threading.Lock()
        self._stream_frame: Image.Image | None = None
        self._stream_thread: threading.Thread | None = None
        self._stream_stop = threading.Event()
        # Which capture path the last grab actually took: "wgc" (occlusion
        # proof, DirectX-capable), "printwindow", or "mss" (screen-region
        # fallback -- NOT occlusion proof). The overlay reads this to show a
        # "compatibility mode" hint when WGC isn't doing the work, since a
        # silent fallback leaves the user unaware their anti-occlusion is gone.
        self.capture_mode = "wgc"

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

    def _window_geometry(self) -> tuple[int, int, int]:
        hwnd = self._find_window()
        if self._win32gui.IsIconic(hwnd):
            # Minimized windows report a zero-size client rect -- fail clearly
            # here so callers (the overlay) can show 'game minimized' and retry,
            # same as the 'game not found' case.
            raise RuntimeError("game window is minimized")
        _, _, right, bottom = self._win32gui.GetClientRect(hwnd)
        if right <= 0 or bottom <= 0:
            raise RuntimeError("game window is minimized")
        return hwnd, right, bottom

    def _print_window(self, hwnd: int, cw: int, ch: int) -> Image.Image | None:
        """Render the window's own client area into a bitmap via PrintWindow.

        Uses ctypes against gdi32/user32 rather than pywin32's win32ui, because
        win32ui (pythonwin) links against MFC (mfc140u.dll) which PyInstaller
        can't reliably bundle -- ctypes has no such dependency. Returns None
        when the window won't composite (PrintWindow reports 0, or the result
        is entirely black -- typical for exclusive-fullscreen D3D), so callers
        can fall back to an mss screen grab.
        """
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Pin arg/restypes so 64-bit HWND/HDC/HBITMAP handles aren't truncated
        # (ctypes defaults to 32-bit c_int arguments otherwise).
        c_void_p = ctypes.c_void_p
        user32.GetWindowDC.argtypes = [c_void_p]
        user32.GetWindowDC.restype = c_void_p
        user32.ReleaseDC.argtypes = [c_void_p, c_void_p]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.PrintWindow.argtypes = [c_void_p, c_void_p, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        gdi32.CreateCompatibleDC.argtypes = [c_void_p]
        gdi32.CreateCompatibleDC.restype = c_void_p
        gdi32.CreateCompatibleBitmap.argtypes = [c_void_p, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = c_void_p
        gdi32.SelectObject.argtypes = [c_void_p, c_void_p]
        gdi32.SelectObject.restype = c_void_p
        gdi32.DeleteObject.argtypes = [c_void_p]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [c_void_p]
        gdi32.DeleteDC.restype = wintypes.BOOL

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        gdi32.GetDIBits.argtypes = [
            c_void_p, c_void_p, wintypes.UINT, wintypes.UINT,
            c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
        ]
        gdi32.GetDIBits.restype = ctypes.c_int

        hwnd_dc = user32.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, cw, ch)
        if not mem_dc or not bmp:
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if bmp:
                gdi32.DeleteObject(bmp)
            user32.ReleaseDC(hwnd, hwnd_dc)
            return None
        old_bmp = gdi32.SelectObject(mem_dc, bmp)
        ok = user32.PrintWindow(hwnd, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
        if not ok:
            gdi32.SelectObject(mem_dc, old_bmp)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(hwnd, hwnd_dc)
            return None

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = cw
        bmi.biHeight = -ch  # negative height = top-down row order
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB

        buf = ctypes.create_string_buffer(cw * ch * 4)
        # DIB_RGB_COLORS (0) -> 32bpp BGRA, top-down, tightly packed.
        got = gdi32.GetDIBits(mem_dc, bmp, 0, ch, buf, ctypes.byref(bmi), 0)
        gdi32.SelectObject(mem_dc, old_bmp)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
        if not got:
            return None
        img = Image.frombytes("RGB", (cw, ch), buf.raw, "raw", "BGRX", 0, 1)
        # getbbox() is None exactly when every pixel is black -- the signal
        # that DWM didn't hand us the window's content.
        return img if img.getbbox() is not None else None

    def _screen_fallback(self) -> Image.Image:
        """mss screen-region grab, used only when PrintWindow can't composite
        the window. NOT occlusion-proof -- it reads whatever is on screen at
        the window's position, same as the pre-PrintWindow behaviour."""
        import mss

        hwnd = self._find_window()
        if self._win32gui.IsIconic(hwnd):
            raise RuntimeError("game window is minimized")
        left, top = self._win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, right, bottom = self._win32gui.GetClientRect(hwnd)
        shot = mss.mss().grab({"left": left, "top": top, "width": right, "height": bottom})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def _probe_wgc(self) -> bool:
        """Lazily check whether the `windows-capture` library is importable.
        Only meaningful on Windows; always False elsewhere (dev/tests)."""
        if self._wgc_available is None:
            self._wgc_available = False
            if sys.platform == "win32":
                try:
                    import windows_capture  # noqa: F401

                    self._wgc_available = True
                except Exception:
                    pass
        return self._wgc_available

    def _client_relative_crop(
        self, hwnd: int, frame_w: int, frame_h: int
    ) -> tuple[int, int, int, int] | None:
        """Locate the client area inside a full-window WGC frame, which can
        include the title bar + borders. Returns (x, y, w, h) in frame pixels,
        or None if the client rect can't be resolved (minimized, etc.)."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        try:
            win_rect = wintypes.RECT()
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            res = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(win_rect),
                ctypes.sizeof(win_rect),
            )
            if res != 0:
                user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(win_rect))
            client_rect = wintypes.RECT()
            client_pt = wintypes.POINT(0, 0)
            user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client_rect))
            user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(client_pt))
            client_w = client_rect.right - client_rect.left
            client_h = client_rect.bottom - client_rect.top
            if client_w <= 10 or client_h <= 10:
                return None
            offset_x = max(0, client_pt.x - win_rect.left)
            offset_y = max(0, client_pt.y - win_rect.top)
            crop_x = min(offset_x, max(0, frame_w - 1))
            crop_y = min(offset_y, max(0, frame_h - 1))
            crop_w = min(client_w, frame_w - crop_x)
            crop_h = min(client_h, frame_h - crop_y)
            if crop_w < 10 or crop_h < 10:
                return None
            return (crop_x, crop_y, crop_w, crop_h)
        except Exception:
            return None

    def _try_wgc_frame(self, hwnd: int) -> Image.Image | None:
        """Grab a single frame via Windows Graphics Capture. Returns None on any
        failure (library missing, window minimized, WGC denied, black frame) so
        grab_full() falls back to PrintWindow / mss."""
        if not self._probe_wgc():
            return None
        import threading

        import numpy as np
        import windows_capture

        result: dict = {}
        event = threading.Event()
        try:
            capture = windows_capture.WindowsCapture(
                cursor_capture=True, draw_border=False, window_hwnd=hwnd,
            )
        except Exception:
            return None

        @capture.event
        def on_frame_arrived(frame, control):
            try:
                bgr_obj = frame.convert_to_bgr()
                bgr = getattr(bgr_obj, "frame_buffer", bgr_obj)
                if isinstance(bgr, np.ndarray) and bgr.size > 0:
                    # Copy immediately: the array is a view into the native
                    # mapped frame, which is freed once the capture stops.
                    bgr = np.ascontiguousarray(bgr)
                    fh, fw = bgr.shape[:2]
                    crop = self._client_relative_crop(hwnd, fw, fh)
                    if crop is not None:
                        x, y, cw, ch = crop
                        bgr = np.ascontiguousarray(bgr[y:y + ch, x:x + cw])
                    result["bgr"] = bgr
            except Exception:
                pass
            finally:
                try:
                    control.stop()
                except Exception:
                    pass
                event.set()

        @capture.event
        def on_closed():
            event.set()

        try:
            control = capture.start_free_threaded()
            event.wait(timeout=2.0)
            try:
                control.stop()
            except Exception:
                pass
        except Exception:
            return None

        bgr = result.get("bgr")
        if bgr is None:
            return None
        rgb = bgr[:, :, ::-1]  # BGR -> RGB
        img = Image.fromarray(np.ascontiguousarray(rgb))
        return img if img.getbbox() is not None else None

    # ---- continuous capture stream (lag fix) ----------------------------

    def _ensure_stream(self) -> None:
        """Start the background WGC stream if it isn't already running."""
        if not self._continuous or not self._probe_wgc():
            return
        if self._stream_thread is not None and self._stream_thread.is_alive():
            return
        self._stream_stop.clear()
        self._stream_thread = threading.Thread(
            target=self._stream_loop, daemon=True, name="wgc-stream",
        )
        self._stream_thread.start()

    def _read_stream_frame(self) -> Image.Image | None:
        with self._stream_lock:
            return self._stream_frame

    def _stream_loop(self) -> None:
        """Keep (re)starting the WGC session, re-finding the game window on each
        iteration so a minimize/restore or a window recreation recovers."""
        while not self._stream_stop.is_set():
            try:
                hwnd = self._find_window()
                if self._win32gui.IsIconic(hwnd):
                    time.sleep(0.3)
                    continue
                self._run_wgc_stream(hwnd)
            except Exception:
                pass
            time.sleep(0.3)

    def _run_wgc_stream(self, hwnd: int) -> None:
        """Run one WGC capture session until it closes or the stream stops,
        caching the latest client-area frame under _stream_lock."""
        import numpy as np
        import windows_capture

        try:
            capture = windows_capture.WindowsCapture(
                cursor_capture=True, draw_border=False, window_hwnd=hwnd,
                # Throttle the stream to ~2Hz (500ms), matching the tick rate.
                # Without this, WGC fires a frame every render (~30-60fps) and
                # each one triggers a full-frame numpy copy + PIL conversion,
                # burning CPU/GPU that competes with the game -- the reported
                # "game lags after opening" (2026-08-25).
                minimum_update_interval=500,
            )
        except Exception:
            return

        closed = threading.Event()

        @capture.event
        def on_frame_arrived(frame, control):
            try:
                bgr_obj = frame.convert_to_bgr()
                bgr = getattr(bgr_obj, "frame_buffer", bgr_obj)
                if isinstance(bgr, np.ndarray) and bgr.size > 0:
                    # Copy immediately: a view into the native mapped frame,
                    # which is freed once the next frame arrives / capture stops.
                    bgr = np.ascontiguousarray(bgr)
                    fh, fw = bgr.shape[:2]
                    crop = self._client_relative_crop(hwnd, fw, fh)
                    if crop is not None:
                        x, y, cw, ch = crop
                        bgr = np.ascontiguousarray(bgr[y:y + ch, x:x + cw])
                    rgb = bgr[:, :, ::-1]
                    img = Image.fromarray(np.ascontiguousarray(rgb))
                    with self._stream_lock:
                        self._stream_frame = img
            except Exception:
                pass

        @capture.event
        def on_closed():
            with self._stream_lock:
                self._stream_frame = None
            closed.set()

        try:
            control = capture.start_free_threaded()
        except Exception:
            return

        while not closed.is_set() and not self._stream_stop.is_set():
            time.sleep(0.1)
        try:
            control.stop()
        except Exception:
            pass

    def grab_full(self) -> Image.Image:
        hwnd, cw, ch = self._window_geometry()
        if self._continuous:
            self._ensure_stream()
            img = self._read_stream_frame()
            if img is None:
                img = self._print_window(hwnd, cw, ch)
                if img is None:
                    img = self._screen_fallback()
                    self.capture_mode = "mss"
                else:
                    self.capture_mode = "printwindow"
            else:
                self.capture_mode = "wgc"
        else:
            img = self._try_wgc_frame(hwnd)
            if img is None:
                img = self._print_window(hwnd, cw, ch)
                if img is None:
                    img = self._screen_fallback()
                    self.capture_mode = "mss"
                else:
                    self.capture_mode = "printwindow"
            else:
                self.capture_mode = "wgc"
        self.client_size = img.size
        return img

    def grab_panel(self) -> Image.Image:
        frame = self.grab_full()
        box = scale_box(STAT_PANEL_BOX, frame.size)
        return frame.crop(box.as_tuple())

    def grab_fields(self) -> dict[str, Image.Image]:
        # One full-frame grab, then slice each field out of that single
        # in-memory image rather than a grab per field. PrintWindow captures
        # the whole window anyway, so this is also one PrintWindow call
        # instead of four.
        frame = self.grab_full()
        panel_box = scale_box(STAT_PANEL_BOX, frame.size)
        panel = frame.crop(panel_box.as_tuple())

        fields = {}
        for name, box in FIELD_BOXES.items():
            field_box = scale_box(box, frame.size)
            local = (
                field_box.left - panel_box.left, field_box.top - panel_box.top,
                field_box.right - panel_box.left, field_box.bottom - panel_box.top,
            )
            fields[name] = panel.crop(local)
        return fields


class ManualScreenCapture:
    """Screen-region capture driven by user-marked rectangles (mss).

    Used when the game runs under a screen magnifier (Magpie), where the game
    window's own client rect no longer matches what is actually visible on
    screen (Magpie renders a scaled copy into its own window). The user draws
    a box around the status bar and one around the meso counter; we OCR
    exactly those screen pixels, so the magnification factor is irrelevant.

    grab_full() returns the status-bar region (what the locator + tick crop
    from); grab_meso() returns the meso region. There are no fixed FIELD_BOXES
    here -- the locator subdivides the stat region by detection, so grab_fields
    returns nothing until then.
    """

    def __init__(
        self,
        stat_region: tuple[int, int, int, int],
        meso_region: tuple[int, int, int, int] | None,
    ):
        if sys.platform != "win32":
            raise RuntimeError("ManualScreenCapture requires Windows (mss + real desktop)")
        import mss

        self._mss = mss.mss()
        self._stat_region = stat_region
        self._meso_region = meso_region
        left, top, right, bottom = stat_region
        self.client_size = (right - left, bottom - top)

    def _grab(self, region: tuple[int, int, int, int]) -> Image.Image:
        left, top, right, bottom = region
        shot = self._mss.grab(
            {"left": left, "top": top, "width": right - left, "height": bottom - top}
        )
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def grab_full(self) -> Image.Image:
        return self._grab(self._stat_region)

    def grab_meso(self) -> Image.Image:
        if self._meso_region is None:
            raise RuntimeError("no meso region set")
        return self._grab(self._meso_region)

    def grab_panel(self) -> Image.Image:
        return self.grab_full()

    def grab_fields(self) -> dict[str, Image.Image]:
        # No fixed FIELD_BOXES in manual mode: the locator detection pass is
        # what subdivides the stat region into LV/HP/MP/EXP. Return empty so
        # the tick shows '--' until that pass lands instead of OCRing the whole
        # bar as one string and parsing garbage.
        return {}


def get_capture(sample_path: str | Path | None = None) -> WindowCapture:
    if sample_path is not None:
        return StaticImageCapture(sample_path)
    if sys.platform == "win32":
        return GameWindowCapture()
    raise RuntimeError(
        "Real game capture requires Windows. Pass sample_path= to use "
        "StaticImageCapture for dev/testing on this platform."
    )
