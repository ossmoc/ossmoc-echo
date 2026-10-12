from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def summarize_keys(obj: Any, depth: int = 2) -> dict:
    """粗略总结 JSON 结构（占位）。"""
    if depth <= 0:
        return {"type": type(obj).__name__}
    if isinstance(obj, dict):
        return {
            "type": "dict",
            "keys": list(obj.keys())[:20],
            "sample": {k: summarize_keys(v, depth - 1) for k, v in list(obj.items())[:5]},
        }
    if isinstance(obj, list):
        return {
            "type": "list",
            "len": len(obj),
            "sample": summarize_keys(obj[0], depth - 1) if obj else None,
        }
    return {"type": type(obj).__name__, "value": str(obj)[:80]}
