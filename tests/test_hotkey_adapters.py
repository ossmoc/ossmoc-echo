from __future__ import annotations

import pytest

from ludiglot.ui.hotkey_registrar import HotkeyBinding
from ludiglot.ui.pynput_hotkey_adapter import PynputGlobalHotkeyAdapter, convert_hotkey_for_pynput
from ludiglot.ui.qt_hotkey_adapter import WindowsNativeHotkeyAdapter, parse_win_hotkey


@pytest.mark.parametrize("event_type", ["windows_generic_MSG", "windows_dispatcher_MSG", b"windows_generic_MSG", b"windows_dispatcher_MSG"])
def test_native_filter_handles_qt_bytes_and_thread_hotkeys(event_type):
    from types import SimpleNamespace
    from ludiglot.ui.qt_hotkey_adapter import _WinHotkeyFilter, WM_HOTKEY
    calls = []
    class Message:
        @staticmethod
        def from_address(_address):
            return SimpleNamespace(message=WM_HOTKEY, wParam=1)
    native = _WinHotkeyFilter({1: lambda: calls.append("pressed")}, Message)
    assert native.nativeEventFilter(event_type, 123) == (True, 1)
    assert calls == ["pressed"]


def test_parse_win_hotkey_supports_modifiers_and_key():
    spec = parse_win_hotkey("ctrl+shift+o")

    assert spec is not None
    assert spec.modifiers == 0x0002 | 0x0004
    assert spec.vk == ord("O")


def test_parse_win_hotkey_supports_aliases():
    spec = parse_win_hotkey("control+alt+a")

    assert spec is not None
    assert spec.modifiers == 0x0002 | 0x0001
    assert spec.vk == ord("A")


def test_parse_win_hotkey_supports_win_and_cmd():
    win_spec = parse_win_hotkey("win+x")
    cmd_spec = parse_win_hotkey("cmd+x")

    assert win_spec is not None
    assert cmd_spec is not None
    assert win_spec.modifiers == 0x0008
    assert cmd_spec.modifiers == 0x0008
    assert win_spec.vk == ord("X")
    assert cmd_spec.vk == ord("X")


def test_parse_win_hotkey_handles_invalid_inputs():
    assert parse_win_hotkey("") is None
    assert parse_win_hotkey("ctrl+shift") is None
    assert parse_win_hotkey("ctrl+") is None


def test_parse_win_hotkey_handles_spaces_and_case():
    spec = parse_win_hotkey(" Ctrl + Alt + z ")

    assert spec is not None
    assert spec.modifiers == 0x0002 | 0x0001
    assert spec.vk == ord("Z")


def test_parse_win_hotkey_function_keys_and_ambiguous_keys():
    assert parse_win_hotkey("alt+f12").vk == 0x7B
    assert parse_win_hotkey("ctrl+a+b") is None
    assert parse_win_hotkey("ctrl+f25") is None
    assert parse_win_hotkey("garbage+a") is None


def test_convert_hotkey_for_pynput():
    assert convert_hotkey_for_pynput("ctrl+shift+o") == "<ctrl>+<shift>+o"
    assert convert_hotkey_for_pynput("alt+win+x") == "<alt>+<cmd>+x"


class FakeUser32:
    def __init__(self, failed_ids: set[int] | None = None) -> None:
        self.failed_ids = failed_ids or set()
        self.registered = []
        self.unregistered = []

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):
        self.registered.append((hwnd, hotkey_id, modifiers, vk))
        return hotkey_id not in self.failed_ids

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.unregistered.append((hwnd, hotkey_id))
        return True


class FakeApp:
    def __init__(self) -> None:
        self.installed = []
        self.removed = []

    def installNativeEventFilter(self, native_filter):
        self.installed.append(native_filter)

    def removeNativeEventFilter(self, native_filter):
        self.removed.append(native_filter)


class FakeMsg:
    pass


def test_optional_hotkey_failure_rolls_back_entire_registration():
    user32 = FakeUser32(failed_ids={2})
    adapter = WindowsNativeHotkeyAdapter(application_provider=lambda: FakeApp(), user32_provider=lambda: user32, msg_type_provider=lambda: FakeMsg)
    assert adapter.register(make_bindings()) is None
    assert user32.unregistered == [(None, 1)]


def test_custom_region_hotkey_and_no_repeat():
    user32 = FakeUser32()
    adapter = WindowsNativeHotkeyAdapter(application_provider=lambda: FakeApp(), user32_provider=lambda: user32, msg_type_provider=lambda: FakeMsg, no_repeat=True)
    registration = adapter.register((HotkeyBinding("region_a", "ctrl+f8", lambda: None),))
    assert registration is not None
    assert user32.registered == [(None, 100, 0x4002, 0x77)]
    registration.stop()


def test_duplicate_hotkeys_unregister_partial_registration():
    user32 = FakeUser32()
    adapter = WindowsNativeHotkeyAdapter(application_provider=lambda: FakeApp(), user32_provider=lambda: user32, msg_type_provider=lambda: FakeMsg)
    assert adapter.register((HotkeyBinding("capture", "alt+w", lambda: None), HotkeyBinding("region_a", "alt+w", lambda: None))) is None
    assert user32.unregistered == [(None, 1)]


def make_bindings():
    return (
        HotkeyBinding("capture", "alt+w", lambda: None),
        HotkeyBinding("toggle", "alt+h", lambda: None),
    )


def test_windows_adapter_registers_hotkeys_and_installs_filter():
    user32 = FakeUser32()
    app = FakeApp()
    adapter = WindowsNativeHotkeyAdapter(
        application_provider=lambda: app,
        user32_provider=lambda: user32,
        msg_type_provider=lambda: FakeMsg,
    )

    registration = adapter.register(make_bindings())

    assert registration is not None
    assert user32.registered == [
        (None, 1, 0x0001, ord("W")),
        (None, 2, 0x0001, ord("H")),
    ]
    assert len(app.installed) == 1


def test_windows_registration_stop_unregisters_and_removes_filter():
    user32 = FakeUser32()
    app = FakeApp()
    adapter = WindowsNativeHotkeyAdapter(
        application_provider=lambda: app,
        user32_provider=lambda: user32,
        msg_type_provider=lambda: FakeMsg,
    )
    registration = adapter.register(make_bindings())

    assert registration is not None
    registration.stop()
    registration.stop()

    assert user32.unregistered == [(None, 1), (None, 2)]
    assert app.removed == app.installed


def test_windows_adapter_returns_none_when_capture_parse_fails():
    user32 = FakeUser32()
    app = FakeApp()
    adapter = WindowsNativeHotkeyAdapter(
        application_provider=lambda: app,
        user32_provider=lambda: user32,
        msg_type_provider=lambda: FakeMsg,
    )

    registration = adapter.register((HotkeyBinding("capture", "ctrl+shift", lambda: None),))

    assert registration is None
    assert user32.registered == []
    assert app.installed == []


def test_windows_adapter_falls_back_when_capture_registration_fails():
    user32 = FakeUser32(failed_ids={1})
    app = FakeApp()
    adapter = WindowsNativeHotkeyAdapter(
        application_provider=lambda: app,
        user32_provider=lambda: user32,
        msg_type_provider=lambda: FakeMsg,
    )

    registration = adapter.register(make_bindings())

    assert registration is None
    assert user32.registered == [(None, 1, 0x0001, ord("W"))]
    assert user32.unregistered == []
    assert app.installed == []


class FakeListener:
    def __init__(self, bindings) -> None:
        self.bindings = bindings
        self.run_calls = 0
        self.stop_calls = 0

    def run(self) -> None:
        self.run_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class FakeKeyboard:
    def __init__(self) -> None:
        self.listeners = []

    def GlobalHotKeys(self, bindings):
        listener = FakeListener(bindings)
        self.listeners.append(listener)
        return listener


def test_pynput_adapter_converts_bindings_and_starts_listener():
    keyboard = FakeKeyboard()
    started = []
    adapter = PynputGlobalHotkeyAdapter(
        keyboard_provider=lambda: keyboard,
        thread_starter=lambda target: started.append(target),
    )

    registration = adapter.register(make_bindings())

    assert registration is not None
    assert len(keyboard.listeners) == 1
    assert set(keyboard.listeners[0].bindings) == {"<alt>+w", "<alt>+h"}
    assert started == [keyboard.listeners[0].run]


def test_pynput_registration_stop_stops_listener():
    keyboard = FakeKeyboard()
    adapter = PynputGlobalHotkeyAdapter(
        keyboard_provider=lambda: keyboard,
        thread_starter=lambda target: None,
    )
    registration = adapter.register(make_bindings())

    assert registration is not None
    registration.stop()
    registration.stop()

    assert keyboard.listeners[0].stop_calls == 1
