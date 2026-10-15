from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QAbstractNativeEventFilter
from PyQt6.QtWidgets import QApplication

from ludiglot.ui.hotkey_registrar import HotkeyBinding

WM_HOTKEY = 0x0312
HOTKEY_IDS = {"capture": 1, "toggle": 2, "exception": 3, "exception_select": 4, "screenshot": 5}


@dataclass(frozen=True)
class WinHotkeySpec:
    modifiers: int
    vk: int


def parse_win_hotkey(hotkey: str) -> WinHotkeySpec | None:
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        return None
    modifiers = 0
    vk = None
    for part in parts:
        if part in {"ctrl", "control"}:
            modifiers |= 0x0002
        elif part == "alt":
            modifiers |= 0x0001
        elif part == "shift":
            modifiers |= 0x0004
        elif part in {"win", "cmd"}:
            modifiers |= 0x0008
        elif len(part) == 1:
            if vk is not None or not part.isascii() or not part.isalnum():
                return None
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            if vk is not None:
                return None
            vk = 0x70 + int(part[1:]) - 1
        else:
            return None
    if vk is None:
        return None
    return WinHotkeySpec(modifiers=modifiers, vk=vk)


class _WinHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback_map: dict[int, Callable[[], None]], msg_type: Any) -> None:
        super().__init__()
        self._callback_map = callback_map
        self._msg_type = msg_type

    def nativeEventFilter(self, eventType, message):
        if eventType not in ("windows_generic_MSG", "windows_dispatcher_MSG", b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        msg = self._msg_type.from_address(int(message))
        if msg.message == WM_HOTKEY:
            hotkey_id = int(msg.wParam)
            callback = self._callback_map.get(hotkey_id)
            if callback:
                callback()
                return True, 1
        return False, 0


def _unregister_hotkeys(user32: Any, hotkey_ids: list[int]) -> None:
    for hotkey_id in hotkey_ids:
        try:
            user32.UnregisterHotKey(None, hotkey_id)
        except Exception:
            pass


class WindowsNativeHotkeyRegistration:
    def __init__(
        self,
        *,
        user32: Any,
        registered_ids: list[int],
        native_filter: QAbstractNativeEventFilter | None,
        app: QApplication | None,
    ) -> None:
        self._user32 = user32
        self._registered_ids = registered_ids
        self._native_filter = native_filter
        self._app = app
        self._stopped = False

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        _unregister_hotkeys(self._user32, self._registered_ids)
        if self._app is not None and self._native_filter is not None:
            try:
                self._app.removeNativeEventFilter(self._native_filter)
            except Exception:
                pass
        self._registered_ids = []
        self._native_filter = None


class WindowsNativeHotkeyAdapter:
    def __init__(
        self,
        *,
        application_provider: Callable[[], QApplication | None] = QApplication.instance,
        user32_provider: Callable[[], Any] | None = None,
        msg_type_provider: Callable[[], Any] | None = None,
        no_repeat: bool = False,
    ) -> None:
        self._application_provider = application_provider
        self._user32_provider = user32_provider or self._default_user32_provider
        self._msg_type_provider = msg_type_provider or self._default_msg_type_provider
        self._no_repeat = no_repeat
        self.last_error = ""

    def register(self, bindings: tuple[HotkeyBinding, ...]) -> WindowsNativeHotkeyRegistration | None:
        self.last_error = ""
        try:
            user32 = self._user32_provider()
            msg_type = self._msg_type_provider()
        except Exception:
            return None

        registered: list[int] = []
        callback_map: dict[int, Callable[[], None]] = {}
        specs: set[WinHotkeySpec] = set()
        for index, binding in enumerate(bindings):
            hotkey_id = HOTKEY_IDS.get(binding.name, 100 + index)
            spec = parse_win_hotkey(binding.hotkey)
            if spec is None or spec in specs:
                _unregister_hotkeys(user32, registered)
                return None
            specs.add(spec)
            if user32.RegisterHotKey(None, hotkey_id, spec.modifiers | (0x4000 if self._no_repeat else 0), spec.vk):
                registered.append(hotkey_id)
                callback_map[hotkey_id] = binding.callback
            else:
                import ctypes
                code = ctypes.get_last_error()
                reason = "không có quyền đăng ký" if code == 5 else "phím đang được app khác dùng" if code == 1409 else "Windows từ chối đăng ký"
                self.last_error = f"Không đăng ký được {binding.hotkey}: {reason} (Windows {code})."
                _unregister_hotkeys(user32, registered)
                return None

        if not registered:
            _unregister_hotkeys(user32, registered)
            return None

        native_filter = _WinHotkeyFilter(callback_map, msg_type)
        app = self._application_provider()
        if app is not None:
            app.installNativeEventFilter(native_filter)
        return WindowsNativeHotkeyRegistration(
            user32=user32,
            registered_ids=registered,
            native_filter=native_filter,
            app=app,
        )

    def _default_user32_provider(self) -> Any:
        import ctypes

        return ctypes.WinDLL("user32", use_last_error=True)

    def _default_msg_type_provider(self) -> Any:
        import ctypes.wintypes

        return ctypes.wintypes.MSG
