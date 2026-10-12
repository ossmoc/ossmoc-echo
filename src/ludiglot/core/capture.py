from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

import mss

try:
    from PIL import Image
    from PIL import ImageGrab
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageGrab = None

try:
    import win32gui
except Exception:  # pragma: no cover
    win32gui = None


@dataclass
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int


class CaptureError(RuntimeError):
    pass


def _save_grab(grab, out_path: Path) -> None:
    import mss.tools

    out_path.parent.mkdir(parents=True, exist_ok=True)
    mss.tools.to_png(grab.rgb, grab.size, output=str(out_path))


def capture_region(region: CaptureRegion, out_path: Path) -> None:
    print(f"[CAPTURE] 区域: L={region.left}, T={region.top}, W={region.width}, H={region.height}")
    with mss.mss() as sct:
        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        grab = sct.grab(monitor)
        _save_grab(grab, out_path)


def capture_region_to_image(region: CaptureRegion) -> Any:
    """Capture a region and return a PIL Image directly (in-memory)."""
    if not HAS_PIL:
        raise ImportError("Pillow is required for in-memory capture.")
    
    with mss.mss() as sct:
        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        sct_img = sct.grab(monitor)
        # MSS returns BGRA. Convert to RGB for general usage, or keep RGBA.
        # OCR engine usually handles RGB/Grayscale.
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        return img


def _imagegrab_grab_bbox(left: int, top: int, width: int, height: int) -> Any:
    if not HAS_PIL or ImageGrab is None:
        raise ImportError("Pillow ImageGrab is required for native capture.")
    bbox = (left, top, left + width, top + height)
    return ImageGrab.grab(bbox=bbox, all_screens=True)


def capture_region_to_image_native(region: CaptureRegion) -> Any:
    """Capture a region using native ImageGrab path."""
    return _imagegrab_grab_bbox(region.left, region.top, region.width, region.height)


def capture_region_to_raw(region: CaptureRegion) -> tuple[bytes, int, int]:
    """Capture a region and return raw BGRA bytes + width/height (in-memory)."""
    with mss.mss() as sct:
        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        sct_img = sct.grab(monitor)
        width, height = sct_img.size
        return (bytes(sct_img.bgra), int(width), int(height))


def capture_fullscreen(out_path: Path, monitor_index: int = 1) -> None:
    with mss.mss() as sct:
        monitor = sct.monitors[monitor_index]
        grab = sct.grab(monitor)
        _save_grab(grab, out_path)


def capture_fullscreen_to_image(monitor_index: int = 1) -> Any:
    """Capture fullscreen and return a PIL Image directly (in-memory)."""
    if not HAS_PIL:
        raise ImportError("Pillow is required for in-memory capture.")
        
    with mss.mss() as sct:
        if monitor_index < len(sct.monitors):
            monitor = sct.monitors[monitor_index]
        else:
            monitor = sct.monitors[1] # Fallback to primary
            
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        return img


def capture_fullscreen_to_image_native(monitor_index: int = 1) -> Any:
    """Capture fullscreen using native ImageGrab path."""
    with mss.mss() as sct:
        if monitor_index < len(sct.monitors):
            monitor = sct.monitors[monitor_index]
        else:
            monitor = sct.monitors[1]
        return _imagegrab_grab_bbox(monitor["left"], monitor["top"], monitor["width"], monitor["height"])


def capture_fullscreen_to_raw(monitor_index: int = 1) -> tuple[bytes, int, int]:
    """Capture fullscreen and return raw BGRA bytes + width/height (in-memory)."""
    with mss.mss() as sct:
        if monitor_index < len(sct.monitors):
            monitor = sct.monitors[monitor_index]
        else:
            monitor = sct.monitors[1]  # Fallback to primary
        sct_img = sct.grab(monitor)
        width, height = sct_img.size
        return (bytes(sct_img.bgra), int(width), int(height))


def _find_window_rect(title: str) -> Optional[tuple[int, int, int, int]]:
    if win32gui is None:
        raise CaptureError("缺少 pywin32，无法通过窗口标题截取")

    hwnd = win32gui.FindWindow(None, title)
    if hwnd == 0:
        return None
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right, bottom


def capture_window(title: str, out_path: Path, trim_borders: bool = True) -> None:
    rect = _find_window_rect(title)
    if rect is None:
        raise CaptureError(f"未找到窗口: {title}")

    left, top, right, bottom = rect
    if trim_borders:
        # 简单扣掉边框（经验值），可后续配置化
        left += 8
        top += 30
        right -= 8
        bottom -= 8

    region = CaptureRegion(left=left, top=top, width=right - left, height=bottom - top)
    capture_region(region, out_path)


def capture_window_to_image(title: str, trim_borders: bool = True) -> Any:
    """Capture a window and return a PIL Image directly (in-memory)."""
    rect = _find_window_rect(title)
    if rect is None:
        raise CaptureError(f"未找到窗口: {title}")

    left, top, right, bottom = rect
    if trim_borders:
        left += 8
        top += 30
        right -= 8
        bottom -= 8

    region = CaptureRegion(left=left, top=top, width=right - left, height=bottom - top)
    return capture_region_to_image(region)


def capture_window_to_image_native(title: str, trim_borders: bool = True) -> Any:
    """Capture a window using native ImageGrab path."""
    rect = _find_window_rect(title)
    if rect is None:
        raise CaptureError(f"未找到窗口: {title}")

    left, top, right, bottom = rect
    if trim_borders:
        left += 8
        top += 30
        right -= 8
        bottom -= 8

    region = CaptureRegion(left=left, top=top, width=right - left, height=bottom - top)
    return capture_region_to_image_native(region)


def capture_window_to_raw(title: str, trim_borders: bool = True) -> tuple[bytes, int, int]:
    """Capture a window and return raw BGRA bytes + width/height (in-memory)."""
    rect = _find_window_rect(title)
    if rect is None:
        raise CaptureError(f"未找到窗口: {title}")

    left, top, right, bottom = rect
    if trim_borders:
        left += 8
        top += 30
        right -= 8
        bottom -= 8

    region = CaptureRegion(left=left, top=top, width=right - left, height=bottom - top)
    return capture_region_to_raw(region)
