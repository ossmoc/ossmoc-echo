from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_WINDOW = PROJECT_ROOT / "src" / "ludiglot" / "ui" / "overlay_window.py"
OVERLAY_COMPOSITION = PROJECT_ROOT / "src" / "ludiglot" / "ui" / "overlay_composition.py"


def _overlay_class():
    tree = ast.parse(OVERLAY_WINDOW.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OverlayWindow")


def _composition_tree():
    return ast.parse(OVERLAY_COMPOSITION.read_text(encoding="utf-8"))


def _keyword(call: ast.Call, name: str) -> ast.expr:
    return next(keyword.value for keyword in call.keywords if keyword.arg == name)


def test_hotkey_registrar_wiring_uses_existing_overlay_callbacks():
    tree = _composition_tree()
    registrar_call = None
    callbacks_call = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "HotkeyRegistrar":
            registrar_call = node
        if isinstance(node.func, ast.Name) and node.func.id == "HotkeyRegistrarCallbacks":
            callbacks_call = node

    assert registrar_call is not None
    assert callbacks_call is not None

    callbacks_arg = _keyword(registrar_call, "callbacks")
    assert callbacks_arg is callbacks_call

    capture = _keyword(callbacks_call, "capture")
    assert isinstance(capture, ast.Lambda)
    assert isinstance(capture.body, ast.Call)
    assert isinstance(capture.body.func, ast.Attribute)
    assert capture.body.func.attr == "emit"
    assert isinstance(capture.body.func.value, ast.Attribute)
    assert capture.body.func.value.attr == "capture_requested"
    assert isinstance(capture.body.func.value.value, ast.Name)
    assert capture.body.func.value.value.id == "window"
    assert len(capture.body.args) == 1
    assert isinstance(capture.body.args[0], ast.Constant)
    assert capture.body.args[0].value is True

    toggle = _keyword(callbacks_call, "toggle")
    assert isinstance(toggle, ast.Attribute)
    assert isinstance(toggle.value, ast.Name)
    assert toggle.value.id == "window"
    assert toggle.attr == "_toggle_visibility"

    for keyword_name, signal_name in {"log": "log", "error": "error"}.items():
        value = _keyword(callbacks_call, keyword_name)
        assert isinstance(value, ast.Attribute)
        assert value.attr == "emit"
        assert isinstance(value.value, ast.Attribute)
        assert value.value.attr == signal_name
        assert isinstance(value.value.value, ast.Attribute)
        assert value.value.value.attr == "signals"
        assert isinstance(value.value.value.value, ast.Name)
        assert value.value.value.value.id == "window"


def test_hotkey_registrar_uses_native_primary_and_pynput_fallback_adapters():
    tree = _composition_tree()
    registrar_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "HotkeyRegistrar"
    )

    primary_adapter = _keyword(registrar_call, "primary_adapter")
    assert isinstance(primary_adapter, ast.Call)
    assert isinstance(primary_adapter.func, ast.Name)
    assert primary_adapter.func.id == "WindowsNativeHotkeyAdapter"
    application_provider = _keyword(primary_adapter, "application_provider")
    assert isinstance(application_provider, ast.Attribute)
    assert application_provider.attr == "instance"
    assert isinstance(application_provider.value, ast.Name)
    assert application_provider.value.id == "QApplication"

    fallback_adapter = _keyword(registrar_call, "fallback_adapter")
    assert isinstance(fallback_adapter, ast.Call)
    assert isinstance(fallback_adapter.func, ast.Name)
    assert fallback_adapter.func.id == "PynputGlobalHotkeyAdapter"


def test_hotkey_registrar_starts_and_stops_with_overlay_lifecycle():
    overlay_class = _overlay_class()
    calls = [node for node in ast.walk(overlay_class) if isinstance(node, ast.Call)]

    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "start"
        and isinstance(call.func.value, ast.Attribute)
        and call.func.value.attr == "_hotkeys"
        and isinstance(call.func.value.value, ast.Name)
        and call.func.value.value.id == "self"
        for call in calls
    )
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "stop"
        and isinstance(call.func.value, ast.Attribute)
        and call.func.value.attr == "_hotkeys"
        and isinstance(call.func.value.value, ast.Name)
        and call.func.value.value.id == "self"
        for call in calls
    )


def test_old_overlay_hotkey_implementation_is_removed():
    source = OVERLAY_WINDOW.read_text(encoding="utf-8")
    tree = ast.parse(source)
    overlay_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OverlayWindow")
    defined_methods = {
        node.name for node in overlay_class.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "QAbstractNativeEventFilter" not in source
    assert "_hotkey_listener" not in source
    assert "_win_hotkey_filter" not in source
    assert "_win_hotkey_ids" not in source
    assert {
        "_start_hotkeys",
        "_register_windows_hotkeys",
        "_parse_win_hotkey",
        "_convert_hotkey",
        "_unregister_windows_hotkeys",
    }.isdisjoint(defined_methods)
