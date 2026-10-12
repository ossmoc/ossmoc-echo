from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_COMPOSITION = PROJECT_ROOT / "src" / "ludiglot" / "ui" / "overlay_composition.py"


def _keyword(call: ast.Call, name: str) -> ast.expr:
    return next(keyword.value for keyword in call.keywords if keyword.arg == name)


def _window_attribute(value: ast.expr, attr: str) -> bool:
    return (
        isinstance(value, ast.Attribute)
        and value.attr == attr
        and isinstance(value.value, ast.Name)
        and value.value.id == "window"
    )


def test_result_presentation_adapter_wiring_uses_existing_overlay_methods():
    tree = ast.parse(OVERLAY_COMPOSITION.read_text(encoding="utf-8"))

    adapter_call = None
    controller_call = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "QtResultPresentationAdapter":
            adapter_call = node
        if isinstance(node.func, ast.Name) and node.func.id == "ResultPresentationController":
            controller_call = node

    assert adapter_call is not None
    assert controller_call is not None

    assert _window_attribute(_keyword(adapter_call, "source_editor"), "source_label")
    assert _window_attribute(_keyword(adapter_call, "target_editor"), "cn_label")
    assert _window_attribute(_keyword(adapter_call, "show_single_result"), "show_without_activating")
    assert _window_attribute(_keyword(adapter_call, "show_multi_result"), "show_without_activating")
    assert _window_attribute(_keyword(controller_call, "audio"), "audio_ui")
    assert _window_attribute(_keyword(controller_call, "view"), "result_presentation_view")
