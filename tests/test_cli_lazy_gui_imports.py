from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN = PROJECT_ROOT / "src" / "ludiglot" / "__main__.py"


def _tree() -> ast.Module:
    return ast.parse(MAIN.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def test_main_has_no_top_level_gui_shell_imports():
    tree = _tree()
    top_level_imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]

    assert all(node.module != "ludiglot.ui.overlay_window" for node in top_level_imports)
    assert all(node.module != "ludiglot.ui.app_shell" for node in top_level_imports)


def test_gui_commands_import_app_shell_lazily():
    tree = _tree()

    for function_name in ("cmd_gui", "cmd_audio_build"):
        function = _function(tree, function_name)
        local_imports = [node for node in ast.walk(function) if isinstance(node, ast.ImportFrom)]
        assert any(
            node.module == "ludiglot.ui.app_shell"
            and any(alias.name == "run_gui" for alias in node.names)
            for node in local_imports
        )
