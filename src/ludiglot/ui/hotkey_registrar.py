from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class HotkeyRegistrarCallbacks:
    capture: Callable[[], None]
    toggle: Callable[[], None]
    log: Callable[[str], None]
    error: Callable[[str], None]
    exception: Callable[[], None] = lambda: None
    exception_select: Callable[[], None] = lambda: None


@dataclass(frozen=True)
class HotkeyBinding:
    name: str
    hotkey: str
    callback: Callable[[], None]


class HotkeyRegistration(Protocol):
    def stop(self) -> None:
        ...


class HotkeyAdapter(Protocol):
    def register(self, bindings: tuple[HotkeyBinding, ...]) -> HotkeyRegistration | None:
        ...


class HotkeyRegistrar:
    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        callbacks: HotkeyRegistrarCallbacks,
        primary_adapter: HotkeyAdapter,
        fallback_adapter: HotkeyAdapter | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._callbacks = callbacks
        self._primary_adapter = primary_adapter
        self._fallback_adapter = fallback_adapter
        self._registration: HotkeyRegistration | None = None

    def start(self) -> None:
        if self._registration is not None:
            return
        config = self._config_provider()
        capture_hotkey = getattr(config, "hotkey_capture", None)
        if not capture_hotkey:
            return

        bindings = [
            HotkeyBinding("capture", str(capture_hotkey), self._callbacks.capture),
        ]
        toggle_hotkey = getattr(config, "hotkey_toggle", None)
        if toggle_hotkey:
            bindings.append(HotkeyBinding("toggle", str(toggle_hotkey), self._callbacks.toggle))
        exception_hotkey = getattr(config, "hotkey_exception", None)
        if exception_hotkey:
            bindings.append(HotkeyBinding("exception", str(exception_hotkey), self._callbacks.exception))
        exception_select_hotkey = getattr(config, "hotkey_exception_select", None)
        if exception_select_hotkey:
            bindings.append(
                HotkeyBinding("exception_select", str(exception_select_hotkey), self._callbacks.exception_select)
            )
        binding_tuple = tuple(bindings)

        registration = self._try_register(self._primary_adapter, binding_tuple)
        if registration is not None:
            self._registration = registration
            self._callbacks.log(f"[HOTKEY] 已注册(WinAPI): {self._format_hotkeys(binding_tuple)}")
            return

        if self._fallback_adapter is None:
            return
        try:
            registration = self._fallback_adapter.register(binding_tuple)
        except Exception as exc:
            self._callbacks.error(f"全局热键不可用: {exc}")
            return
        if registration is None:
            return
        self._registration = registration
        self._callbacks.log(f"[HOTKEY] 已注册: {self._format_hotkeys(binding_tuple)}")

    def stop(self) -> None:
        registration = self._registration
        self._registration = None
        if registration is None:
            return
        try:
            registration.stop()
        except Exception as exc:
            self._callbacks.log(f"[HOTKEY] 注销异常: {exc}")

    def _try_register(
        self,
        adapter: HotkeyAdapter,
        bindings: tuple[HotkeyBinding, ...],
    ) -> HotkeyRegistration | None:
        try:
            return adapter.register(bindings)
        except Exception:
            return None

    def _format_hotkeys(self, bindings: tuple[HotkeyBinding, ...]) -> str:
        return " / ".join(binding.hotkey for binding in bindings)
