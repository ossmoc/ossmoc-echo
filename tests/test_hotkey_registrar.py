from __future__ import annotations

from types import SimpleNamespace

from ludiglot.ui.hotkey_registrar import HotkeyRegistrar, HotkeyRegistrarCallbacks


class FakeRegistration:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class FakeAdapter:
    def __init__(self, registration=None, error: Exception | None = None) -> None:
        self.registration = registration
        self.error = error
        self.calls = []

    def register(self, bindings):
        self.calls.append(bindings)
        if self.error is not None:
            raise self.error
        return self.registration


class CallbackProbe:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.logs: list[str] = []
        self.errors: list[str] = []

    def callbacks(self) -> HotkeyRegistrarCallbacks:
        return HotkeyRegistrarCallbacks(
            capture=lambda: self.events.append("capture"),
            toggle=lambda: self.events.append("toggle"),
            log=self.logs.append,
            error=self.errors.append,
        )


def make_config(capture="ctrl+shift+o", toggle=None):
    return SimpleNamespace(hotkey_capture=capture, hotkey_toggle=toggle)


def make_registrar(probe: CallbackProbe, *, config=None, primary=None, fallback=None) -> HotkeyRegistrar:
    return HotkeyRegistrar(
        config_provider=lambda: config or make_config(),
        callbacks=probe.callbacks(),
        primary_adapter=primary or FakeAdapter(FakeRegistration()),
        fallback_adapter=fallback,
    )


def test_start_without_capture_hotkey_does_nothing():
    probe = CallbackProbe()
    primary = FakeAdapter(FakeRegistration())
    fallback = FakeAdapter(FakeRegistration())
    registrar = make_registrar(
        probe,
        config=make_config(capture="", toggle="alt+h"),
        primary=primary,
        fallback=fallback,
    )

    registrar.start()

    assert primary.calls == []
    assert fallback.calls == []
    assert probe.logs == []
    assert probe.errors == []


def test_start_registers_native_hotkeys_first():
    probe = CallbackProbe()
    native_registration = FakeRegistration()
    primary = FakeAdapter(native_registration)
    fallback = FakeAdapter(FakeRegistration())
    registrar = make_registrar(probe, primary=primary, fallback=fallback)

    registrar.start()

    assert len(primary.calls) == 1
    assert fallback.calls == []
    assert probe.logs == ["[HOTKEY] 已注册(WinAPI): ctrl+shift+o"]
    assert probe.errors == []


def test_start_includes_toggle_when_configured():
    probe = CallbackProbe()
    primary = FakeAdapter(FakeRegistration())
    registrar = make_registrar(probe, config=make_config(capture="alt+w", toggle="alt+h"), primary=primary)

    registrar.start()

    bindings = primary.calls[0]
    assert [(binding.name, binding.hotkey) for binding in bindings] == [("capture", "alt+w"), ("toggle", "alt+h")]
    assert probe.logs == ["[HOTKEY] 已注册(WinAPI): alt+w / alt+h"]


def test_native_failure_falls_back_to_pynput():
    probe = CallbackProbe()
    primary = FakeAdapter(None)
    fallback = FakeAdapter(FakeRegistration())
    registrar = make_registrar(
        probe,
        config=make_config(capture="alt+w", toggle="alt+h"),
        primary=primary,
        fallback=fallback,
    )

    registrar.start()

    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    assert probe.logs == ["[HOTKEY] 已注册: alt+w / alt+h"]
    assert probe.errors == []


def test_fallback_import_error_reports_global_hotkey_unavailable():
    probe = CallbackProbe()
    primary = FakeAdapter(None)
    fallback = FakeAdapter(error=ImportError("missing pynput"))
    registrar = make_registrar(probe, primary=primary, fallback=fallback)

    registrar.start()

    assert probe.errors == ["全局热键不可用: missing pynput"]
    assert probe.logs == []


def test_stop_delegates_to_active_registration_and_is_idempotent():
    probe = CallbackProbe()
    registration = FakeRegistration()
    registrar = make_registrar(probe, primary=FakeAdapter(registration))

    registrar.start()
    registrar.stop()
    registrar.stop()

    assert registration.stop_calls == 1


def test_callbacks_are_preserved_in_bindings():
    probe = CallbackProbe()
    primary = FakeAdapter(FakeRegistration())
    registrar = make_registrar(probe, config=make_config(capture="alt+w", toggle="alt+h"), primary=primary)

    registrar.start()
    bindings = primary.calls[0]
    {binding.name: binding.callback for binding in bindings}["capture"]()
    {binding.name: binding.callback for binding in bindings}["toggle"]()

    assert probe.events == ["capture", "toggle"]


def test_start_includes_exception_capture_hotkeys():
    probe = CallbackProbe()
    primary = FakeAdapter(FakeRegistration())
    config = SimpleNamespace(
        hotkey_capture="alt+w",
        hotkey_toggle="alt+h",
        hotkey_exception="alt+e",
        hotkey_exception_select="alt+shift+e",
    )
    registrar = make_registrar(probe, config=config, primary=primary)

    registrar.start()

    assert [(binding.name, binding.hotkey) for binding in primary.calls[0]] == [
        ("capture", "alt+w"),
        ("toggle", "alt+h"),
        ("exception", "alt+e"),
        ("exception_select", "alt+shift+e"),
    ]
