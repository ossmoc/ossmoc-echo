from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SHELL = PROJECT_ROOT / "src" / "ludiglot" / "ui" / "app_shell.py"
OVERLAY_WINDOW = PROJECT_ROOT / "src" / "ludiglot" / "ui" / "overlay_window.py"


def test_app_shell_owns_run_gui_and_tray_lifecycle():
    source = APP_SHELL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert "run_gui" in functions
    assert "QApplication" in source
    assert "QSystemTrayIcon" in source
    assert "OverlayWindow" in source
    assert "load_config" in source


def test_overlay_window_no_longer_owns_app_shell_lifecycle():
    source = OVERLAY_WINDOW.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert "run_gui" not in functions
    assert "QSystemTrayIcon" not in source
    assert "from ludiglot.core.config import AppConfig, load_config" not in source
