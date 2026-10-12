from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_WINDOW = PROJECT_ROOT / "src" / "ludiglot" / "ui" / "overlay_window.py"


def _overlay_class():
    tree = ast.parse(OVERLAY_WINDOW.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OverlayWindow")


def _method(name: str) -> ast.FunctionDef:
    overlay_class = _overlay_class()
    return next(node for node in overlay_class.body if isinstance(node, ast.FunctionDef) and node.name == name)


def test_update_database_delegates_to_controller():
    method = _method("_update_database")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]

    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "start"
        and isinstance(call.func.value, ast.Attribute)
        and call.func.value.attr == "database_update_controller"
        for call in calls
    )


def test_runtime_refresh_reuses_overlay_runtime_initialization():
    method = _method("_refresh_runtime_resources")
    source = ast.unparse(method)

    assert "initialize_overlay_runtime" in source
    assert "_on_runtime_resources_initialized" in source


def test_overlay_window_no_longer_performs_partial_database_reload():
    source = OVERLAY_WINDOW.read_text(encoding="utf-8")

    assert "json.loads" not in source
    assert "DatabaseUpdateThread" not in source
    assert "StyledProgressDialog" not in source
