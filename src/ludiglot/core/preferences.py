from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_STORE_LOCK = threading.RLock()


def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


FONT_SIZE_MIN = 8
FONT_SIZE_MAX = 72
LETTER_SPACING_MIN = -10.0
LETTER_SPACING_MAX = 50.0
LINE_SPACING_MIN = 0.5
LINE_SPACING_MAX = 5.0
VALID_FONT_WEIGHTS = {"Light", "Normal", "SemiBold", "Bold", "Heavy"}
VALID_MENU_DIRECTIONS = {"left", "right"}
VALID_OCR_BACKENDS = {"auto", "windows", "paddle_vl"}
VALID_OCR_MODES = {"auto", "gpu", "cpu"}


@dataclass(frozen=True)
class WindowPoint:
    x: int
    y: int


@dataclass(frozen=True)
class WindowSize:
    width: int
    height: int


@dataclass(frozen=True)
class WindowBounds:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class OverlayPreferences:
    window_pos: WindowPoint | None = None
    window_size: WindowSize | None = None
    font_size: int = 13
    font_weight: str = "SemiBold"
    letter_spacing: float = 0.0
    line_spacing: float = 1.2
    menu_direction: str = "right"
    font_en: str | None = None
    font_cn: str | None = None
    ocr_backend: str = "auto"
    ocr_mode: str = "auto"


class ConfigJsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def load_overlay_preferences(self, defaults: OverlayPreferences | None = None) -> OverlayPreferences:
        defaults = normalize_overlay_preferences(defaults or OverlayPreferences())
        raw = self.load_raw()
        ui = raw.get("ui_settings") if isinstance(raw.get("ui_settings"), dict) else {}
        return normalize_overlay_preferences(
            OverlayPreferences(
                window_pos=_parse_window_point(raw.get("window_pos")) or defaults.window_pos,
                window_size=_parse_window_size(raw.get("window_size")) or defaults.window_size,
                font_size=_coerce_int(ui.get("font_size"), defaults.font_size),
                font_weight=str(ui.get("font_weight", defaults.font_weight)),
                letter_spacing=_coerce_float(ui.get("letter_spacing"), defaults.letter_spacing),
                line_spacing=_coerce_float(ui.get("line_spacing"), defaults.line_spacing),
                menu_direction=str(ui.get("menu_direction", defaults.menu_direction)),
                font_en=str(ui.get("font_en") or raw.get("font_en") or defaults.font_en) if (ui.get("font_en") or raw.get("font_en") or defaults.font_en) else None,
                font_cn=str(ui.get("font_cn") or raw.get("font_cn") or defaults.font_cn) if (ui.get("font_cn") or raw.get("font_cn") or defaults.font_cn) else None,
                ocr_backend=str(raw.get("ocr_backend", defaults.ocr_backend)),
                ocr_mode=str(raw.get("ocr_mode", defaults.ocr_mode)),
            ),
            defaults,
        )

    def save_overlay_preferences(self, preferences: OverlayPreferences) -> None:
        with _STORE_LOCK:
            self._save_overlay_preferences(preferences)

    def _save_overlay_preferences(self, preferences: OverlayPreferences) -> None:
        preferences = normalize_overlay_preferences(preferences)
        raw = self.load_raw()
        if preferences.window_pos is not None:
            raw["window_pos"] = {"x": int(preferences.window_pos.x), "y": int(preferences.window_pos.y)}
        if preferences.window_size is not None:
            raw["window_size"] = {"width": int(preferences.window_size.width), "height": int(preferences.window_size.height)}
        raw["ui_settings"] = {
            **(raw.get("ui_settings") if isinstance(raw.get("ui_settings"), dict) else {}),
            "font_size": preferences.font_size,
            "font_weight": preferences.font_weight,
            "letter_spacing": preferences.letter_spacing,
            "line_spacing": preferences.line_spacing,
            "menu_direction": preferences.menu_direction,
            "font_en": preferences.font_en,
            "font_cn": preferences.font_cn,
        }
        raw["ocr_backend"] = preferences.ocr_backend
        raw["ocr_mode"] = preferences.ocr_mode
        raw["font_en"] = preferences.font_en
        raw["font_cn"] = preferences.font_cn
        atomic_write_json(self.path, raw)

    def update_values(self, **values: Any) -> None:
        with _STORE_LOCK:
            raw = self.load_raw()
            raw.update(values)
            atomic_write_json(self.path, raw)


def normalize_overlay_preferences(
    preferences: OverlayPreferences,
    defaults: OverlayPreferences | None = None,
) -> OverlayPreferences:
    defaults = defaults or OverlayPreferences()
    return OverlayPreferences(
        window_pos=preferences.window_pos or defaults.window_pos,
        window_size=preferences.window_size or defaults.window_size,
        font_size=_clamp_int(preferences.font_size, FONT_SIZE_MIN, FONT_SIZE_MAX, defaults.font_size),
        font_weight=_valid_choice(preferences.font_weight, VALID_FONT_WEIGHTS, defaults.font_weight),
        letter_spacing=_clamp_float(
            preferences.letter_spacing,
            LETTER_SPACING_MIN,
            LETTER_SPACING_MAX,
            defaults.letter_spacing,
        ),
        line_spacing=_clamp_float(
            preferences.line_spacing,
            LINE_SPACING_MIN,
            LINE_SPACING_MAX,
            defaults.line_spacing,
        ),
        menu_direction=_valid_choice(preferences.menu_direction, VALID_MENU_DIRECTIONS, defaults.menu_direction),
        font_en=preferences.font_en or defaults.font_en,
        font_cn=preferences.font_cn or defaults.font_cn,
        ocr_backend=_valid_choice(preferences.ocr_backend, VALID_OCR_BACKENDS, defaults.ocr_backend),
        ocr_mode=_valid_choice(preferences.ocr_mode, VALID_OCR_MODES, defaults.ocr_mode),
    )


def clamp_window_position(position: WindowPoint, size: WindowSize, screens: list[WindowBounds]) -> WindowPoint:
    if not screens:
        return position
    for screen in screens:
        if _point_fits_screen(position, size, screen):
            return position
    screen = screens[0]
    max_x = screen.left + max(screen.width - size.width, 0)
    max_y = screen.top + max(screen.height - size.height, 0)
    return WindowPoint(
        x=max(screen.left, min(position.x, max_x)),
        y=max(screen.top, min(position.y, max_y)),
    )


def _point_fits_screen(position: WindowPoint, size: WindowSize, screen: WindowBounds) -> bool:
    return (
        position.x >= screen.left
        and position.y >= screen.top
        and position.x + size.width <= screen.left + screen.width
        and position.y + size.height <= screen.top + screen.height
    )


def _valid_choice(value: Any, valid: set[str], default: str) -> str:
    text = str(value)
    return text if text in valid else default


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(minimum, min(maximum, number))


def _clamp_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = float(default)
    return max(minimum, min(maximum, number))


def _parse_window_point(raw: Any) -> WindowPoint | None:
    if not isinstance(raw, dict):
        return None
    try:
        return WindowPoint(x=int(raw.get("x")), y=int(raw.get("y")))
    except Exception:
        return None


def _parse_window_size(raw: Any) -> WindowSize | None:
    if not isinstance(raw, dict):
        return None
    try:
        return WindowSize(width=int(raw.get("width")), height=int(raw.get("height")))
    except Exception:
        return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default
