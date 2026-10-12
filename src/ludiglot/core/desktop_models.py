from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import re
from typing import Any
from uuid import uuid4

from ludiglot.core.capture import CaptureRegion


@dataclass(frozen=True)
class OcrRegion:
    id: str
    name: str
    mode: str = "realtime"
    enabled: bool = True
    bounds: dict[str, int] | None = None
    screen: dict[str, Any] | None = None
    hotkey: str = ""
    revision: int = 0
    order: int = 0

    @property
    def capture_region(self) -> CaptureRegion | None:
        try:
            raw = self.bounds or {}
            region = CaptureRegion(**{key: int(raw[key]) for key in ("left", "top", "width", "height")})
            return region if region.width >= 8 and region.height >= 8 else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], order: int = 0) -> "OcrRegion":
        return cls(
            id=str(raw.get("id") or uuid4().hex),
            name=str(raw.get("name") or "Vùng OCR"),
            mode="manual" if raw.get("mode") == "manual" else "realtime",
            enabled=bool(raw.get("enabled", True)),
            bounds=raw.get("bounds") if isinstance(raw.get("bounds"), dict) else None,
            screen=raw.get("screen") if isinstance(raw.get("screen"), dict) else None,
            hotkey=str(raw.get("hotkey") or ""),
            revision=max(0, int(raw.get("revision", 0))),
            order=order,
        )


OVERLAY_DEFAULTS = {
    "font_family": "Segoe UI", "font_size": 24, "text_color": "#ffffff",
    "outline_color": "#141820", "outline_width": 2.0, "line_spacing": 1.2,
    "alignment": "center", "background": "none", "background_opacity": 45,
    "show_source": False, "width": 900, "height": 200,
}

HOTKEY_DEFAULTS = {"hotkey_capture": "alt+w", "hotkey_toggle": "alt+h",
                   "hotkey_exception": "alt+e", "hotkey_exception_select": "alt+shift+e"}
AUTO_DEFAULTS = {
    "mode": "regions", "bounds": None, "screen": None,
    "dialogue": True, "walking": True, "choices": True, "document": True, "glossary": True, "transition": True,
    "font_family": "Segoe UI", "font_size": 28, "choice_font_size": 20, "text_color": "#ffffff",
    "outline_color": "#101215", "outline_width": 2,
    "background_color": "#3b3e43", "background_opacity": 88,
    "reader_geometry": {}, "reader_font_size": 22, "show_bounds": False,
    "context_hint": "",
    "story_group": "",
    "presentation_version": 2,
    "light_ocr": True,
}


def normalize_auto_settings(value: Any) -> dict[str, Any]:
    """Recover malformed optional Auto fields without touching legacy settings."""
    raw = value if isinstance(value, dict) else {}
    result = {**copy.deepcopy(AUTO_DEFAULTS), **raw}
    if raw.get('presentation_version') != 2:
        # Upgrade only the old default palette; retain user-customized colors.
        if str(raw.get('background_color', '')).lower() == '#25282d' and raw.get('background_opacity') == 95:
            result.update(background_color=AUTO_DEFAULTS['background_color'],
                          background_opacity=AUTO_DEFAULTS['background_opacity'])
        result['show_bounds'] = False
        result['presentation_version'] = 2
    if result["mode"] not in ("regions", "auto"):
        result["mode"] = "regions"
    for key, low, high in (("font_size", 18, 64), ("choice_font_size", 18, 64), ("reader_font_size", 14, 64),
                           ("outline_width", 0, 8), ("background_opacity", 0, 100)):
        try:
            result[key] = max(low, min(high, int(result[key])))
        except (TypeError, ValueError, OverflowError):
            result[key] = AUTO_DEFAULTS[key]
    for key in ("text_color", "outline_color", "background_color"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(result[key])):
            result[key] = AUTO_DEFAULTS[key]
    result["font_family"] = str(result.get("font_family") or "Segoe UI")
    result["context_hint"] = str(result.get("context_hint") or "")[:160]
    result['story_group'] = str(result.get('story_group') or '')[:160]
    for key in ("bounds", "reader_geometry"):
        fields = ("left", "top", "width", "height") if key == "bounds" else ("x", "y", "width", "height")
        try:
            geometry = {k: int(result[key][k]) for k in fields}
            if geometry["width"] < 8 or geometry["height"] < 8:
                raise ValueError("Invalid geometry")
            result[key] = geometry
        except (TypeError, KeyError, ValueError, OverflowError):
            result[key] = None if key == "bounds" else {}
    if not isinstance(result["screen"], dict):
        result["screen"] = None
    return result


def migrate_desktop_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Add desktop settings without rewriting unrelated or legacy configuration."""
    result = dict(raw)
    region_values = result.get("regions")
    if not isinstance(region_values, list):
        region_values = [
            {"id": "dialogue", "name": "Hội thoại", "mode": "realtime", "bounds": raw.get("capture_region")},
            {"id": "special", "name": "Vùng đặc biệt", "mode": "manual", "bounds": raw.get("exception_capture_region")},
        ]
    regions = []
    seen = set()
    for value in region_values:
        if not isinstance(value, dict):
            continue
        try:
            region = OcrRegion.from_mapping(value, len(regions))
        except (TypeError, ValueError):
            continue
        if region.id in seen:
            region = OcrRegion.from_mapping({**region.to_dict(), "id": uuid4().hex}, len(regions))
        seen.add(region.id)
        regions.append(region.to_dict())
    result["regions"] = regions
    overlay = {**OVERLAY_DEFAULTS, **(raw.get("overlay") if isinstance(raw.get("overlay"), dict) else {})}
    if "overlay" not in raw:
        pos = raw.get("window_pos")
        if isinstance(pos, dict):
            overlay.update({k: int(pos[k]) for k in ("x", "y") if k in pos})
    result["overlay"] = overlay
    result["auto_wuwa"] = normalize_auto_settings(raw.get("auto_wuwa"))
    result.setdefault("special_region_id", next((r["id"] for r in regions if r["mode"] == "manual"), ""))
    result.setdefault("profiles", {})
    # Focus is no longer a scheduler state transition. Window capture runs in
    # the background; the legacy key stays readable for old profiles.
    result["pause_on_focus_loss"] = False
    result.setdefault("window_capture", {"mode": "desktop"})
    result.setdefault("realtime_interval_ms", 350)
    for key, value in HOTKEY_DEFAULTS.items():
        result.setdefault(key, value)
    result["desktop_config_version"] = 1
    return result


def display_signature(blocks: list[dict[str, Any]], show_source: bool = False) -> tuple:
    return tuple((str(b.get("name", "")) if len(blocks) > 1 else "", str(b.get("target", "")), str(b.get("source", "")) if show_source else "") for b in blocks)


def count_translation_coverage(database: dict) -> dict[str, int]:
    """Count official identities once, even when a cache contains search aliases."""
    seen = set()
    vietnamese = fallback = missing = 0
    for group in database.values():
        for match in group.get("matches", []):
            identity = (str(match.get("source_json", "")), str(match.get("source_table", match.get("tbl", ""))),
                        str(match.get("text_key", "")), str(match.get("official_en", "")))
            if identity in seen:
                continue
            seen.add(identity)
            if match.get("official_vi"):
                vietnamese += 1
            elif match.get("official_en"):
                fallback += 1
            else:
                missing += 1
    return {"vietnamese": vietnamese, "english_fallback": fallback, "missing": missing}
