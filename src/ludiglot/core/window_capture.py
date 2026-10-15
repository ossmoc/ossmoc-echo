"""HWND-only Windows Graphics Capture. Never falls back to desktop pixels."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import threading
import time

from PIL import Image


class CaptureUnavailable(RuntimeError):
    """Temporary loss of the selected window, not a request to pause OCR."""


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    pid: int
    title: str
    executable: str
    class_name: str

    def identity(self):
        return {"title": self.title, "executable": self.executable, "class_name": self.class_name}


def window_target(hwnd):
    import win32gui
    import win32process
    import win32api
    if not win32gui.IsWindow(hwnd):
        raise CaptureUnavailable("Cửa sổ đã đóng. Chọn lại ứng dụng game.")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = win32api.OpenProcess(0x1000, False, pid)
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.c_ulong(len(buffer))
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(int(handle)), 0, buffer, ctypes.byref(length)):
            raise OSError("Không đọc được tên ứng dụng")
        executable = buffer.value
    finally:
        handle.Close()
    return WindowTarget(int(hwnd), pid, win32gui.GetWindowText(hwnd), executable, win32gui.GetClassName(hwnd))


def list_windows():
    import win32gui
    result = []
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            try:
                target = window_target(hwnd)
                if target.pid != os.getpid() and os.path.basename(target.executable).casefold() not in {"ludiglot.exe","ossmocecho.exe"}:
                    result.append(target)
            except Exception:
                pass
    win32gui.EnumWindows(visit, None)
    return sorted(result, key=lambda w: ("wuthering" not in w.title.casefold(), w.title.casefold()))


def find_saved_window(identity):
    # Exact executable + class + caption; ambiguity requires explicit selection.
    candidates = [w for w in list_windows() if w.identity() == identity]
    return candidates[0] if len(candidates) == 1 else None


def physical_geometry(target):
    import win32gui
    import win32process
    if not win32gui.IsWindow(target.hwnd) or win32process.GetWindowThreadProcessId(target.hwnd)[1] != target.pid:
        raise CaptureUnavailable("Cửa sổ game đã đóng. Chọn lại ứng dụng; không chụp màn hình ngoài.")
    if win32gui.IsIconic(target.hwnd):
        raise CaptureUnavailable("Game đang thu nhỏ. Đang chờ game trả ảnh; OCR vẫn được bật.")
    if not win32gui.IsWindowVisible(target.hwnd):
        raise CaptureUnavailable("Cửa sổ game đang ẩn/đóng; đang chờ ảnh game.")
    dpi_context = ctypes.windll.user32.SetThreadDpiAwarenessContext
    dpi_context.restype = ctypes.c_void_p
    previous = dpi_context(ctypes.c_void_p(-4))
    try:
        left, top = win32gui.ClientToScreen(target.hwnd, (0, 0))
        _, _, width, height = win32gui.GetClientRect(target.hwnd)
        outer = win32gui.GetWindowRect(target.hwnd)
        rect = (ctypes.c_long * 4)()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(target.hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
        extended = tuple(rect) if hr == 0 else outer
        return {"left": left, "top": top, "width": width, "height": height}, (extended, outer)
    finally:
        if previous:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def client_crop_box(size, client, frames):
    """Map actual WGC bounds, rejecting unknown geometry instead of guessing."""
    if size == (client["width"], client["height"]):
        return (0, 0, *size)
    for left, top, right, bottom in frames:
        if size == (right-left, bottom-top):
            x, y = client["left"]-left, client["top"]-top
            if x >= 0 and y >= 0 and x+client["width"] <= size[0] and y+client["height"] <= size[1]:
                return x, y, x+client["width"], y+client["height"]
    raise CaptureUnavailable("Kích thước ảnh game đang thay đổi; đang chờ khung hình phù hợp.")


def local_crop_box(region, anchor, size):
    x, y = region.left-anchor["left"], region.top-anchor["top"]
    if x < 0 or y < 0 or x+region.width > size[0] or y+region.height > size[1]:
        raise CaptureUnavailable("Vùng OCR nằm ngoài cửa sổ đã chọn. Khoanh lại vùng trên ảnh game.")
    return x, y, x+region.width, y+region.height


class WindowCapture:
    def __init__(self, factory=None, geometry=physical_geometry):
        self._factory = factory
        self._geometry = geometry
        self._condition = threading.Condition(threading.RLock())
        self.target = None
        self._capture = self._control = self._image = None
        self._serial = 0
        self._closed = True
        self._received_at = 0.0
        self._received_number = 0
        self._restore_number = -1

    def bind(self, target):
        self.close()
        with self._condition:
            self.target = target
            self._serial += 1
            serial = self._serial
            self._closed = False
            self._received_number = 0
            self._restore_number = -1
        try:
            if self._factory is None:
                from windows_capture import WindowsCapture
                factory = WindowsCapture
            else:
                factory = self._factory
            capture = factory(window_hwnd=target.hwnd, monitor_index=None, cursor_capture=False,
                              draw_border=None, minimum_update_interval=None)
            # None preserves OS defaults instead of invoking optional WGC session
            # properties (border/update interval are unsupported on older Windows).
            # OCR polling remains throttled by the application scheduler.
            def on_frame_arrived(frame, control):
                # The native buffer is valid only in this callback; own the copy.
                image = Image.frombytes("RGB", (frame.width, frame.height), frame.frame_buffer.tobytes(), "raw", "BGRX")
                with self._condition:
                    if serial != self._serial:
                        control.stop()
                        return
                    self._image = image
                    self._received_at = time.monotonic()
                    self._received_number += 1
                    self._condition.notify_all()
            def on_closed():
                with self._condition:
                    if serial == self._serial:
                        self._closed = True
                        self._image = None
                        self._condition.notify_all()
            capture.event(on_frame_arrived)
            capture.event(on_closed)
            control = capture.start_free_threaded()
            with self._condition:
                self._capture, self._control = capture, control
        except Exception:
            self.close()
            raise

    def bounds(self):
        if not self.target:
            raise CaptureUnavailable("Chưa chọn ứng dụng game.")
        return self._geometry(self.target)[0]

    def image(self, timeout=.6):
        if not self.target:
            raise CaptureUnavailable("Chọn ứng dụng WuWa trước khi bắt đầu.")
        try:
            client, frames = self._geometry(self.target)
        except CaptureUnavailable:
            with self._condition:
                self._restore_number = self._received_number
            raise
        with self._condition:
            serial = self._serial
            self._condition.wait_for(lambda: self._closed or self._serial != serial or
                                    (self._image is not None and self._received_number > self._restore_number), timeout)
            if self._closed or self._serial != serial or self._image is None or self._received_number <= self._restore_number:
                raise CaptureUnavailable("Đang chờ ảnh từ cửa sổ game; không chuyển sang chụp desktop.")
            image = self._image
            cropped = image.crop(client_crop_box(image.size, client, frames))
            cropped.info["capture_bounds"] = client
            return cropped

    def close(self):
        with self._condition:
            self._serial += 1
            control = self._control
            self._control = self._capture = self._image = None
            self.target = None
            self._closed = True
            self._condition.notify_all()
        if control:
            control.stop()
