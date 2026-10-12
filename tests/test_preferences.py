import json

from ludiglot.core.preferences import (
    ConfigJsonStore,
    OverlayPreferences,
    WindowBounds,
    WindowPoint,
    WindowSize,
    clamp_window_position,
    normalize_overlay_preferences,
)


def test_save_overlay_preferences_preserves_unknown_fields(tmp_path):
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps({"data_root": "data", "nested": {"keep": True}}, ensure_ascii=False), encoding="utf-8")

    store = ConfigJsonStore(config_path)
    store.save_overlay_preferences(
        OverlayPreferences(
            window_pos=WindowPoint(10, 20),
            window_size=WindowSize(640, 320),
            font_size=15,
            font_weight="Bold",
            letter_spacing=1.5,
            line_spacing=1.4,
            menu_direction="left",
            font_en="EN",
            font_cn="CN",
            ocr_backend="windows",
            ocr_mode="cpu",
        )
    )

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["data_root"] == "data"
    assert raw["nested"] == {"keep": True}
    assert raw["window_pos"] == {"x": 10, "y": 20}
    assert raw["window_size"] == {"width": 640, "height": 320}
    assert raw["ui_settings"] == {
        "font_size": 15,
        "font_weight": "Bold",
        "letter_spacing": 1.5,
        "line_spacing": 1.4,
        "menu_direction": "left",
        "font_en": "EN",
        "font_cn": "CN",
    }
    assert raw["ocr_backend"] == "windows"
    assert raw["ocr_mode"] == "cpu"
    assert raw["font_en"] == "EN"
    assert raw["font_cn"] == "CN"


def test_save_overlay_preferences_normalizes_values(tmp_path):
    config_path = tmp_path / "settings.json"
    store = ConfigJsonStore(config_path)

    store.save_overlay_preferences(
        OverlayPreferences(
            font_size=999,
            font_weight="Bad",
            letter_spacing=999,
            line_spacing=-1,
            menu_direction="up",
            ocr_backend="bad",
            ocr_mode="bad",
        )
    )

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["ui_settings"]["font_size"] == 72
    assert raw["ui_settings"]["font_weight"] == "SemiBold"
    assert raw["ui_settings"]["letter_spacing"] == 50.0
    assert raw["ui_settings"]["line_spacing"] == 0.5
    assert raw["ui_settings"]["menu_direction"] == "right"
    assert raw["ocr_backend"] == "auto"
    assert raw["ocr_mode"] == "auto"


def test_load_overlay_preferences_uses_ui_settings_and_defaults(tmp_path):
    config_path = tmp_path / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "window_pos": {"x": "7", "y": "8"},
                "window_size": {"width": "500", "height": "300"},
                "font_en": "TopEN",
                "font_cn": "TopCN",
                "ocr_backend": "windows",
                "ocr_mode": "gpu",
                "ui_settings": {
                    "font_size": "16",
                    "font_weight": "Heavy",
                    "letter_spacing": "2.5",
                    "line_spacing": "1.6",
                    "menu_direction": "right",
                    "font_en": "UiEN",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prefs = ConfigJsonStore(config_path).load_overlay_preferences(
        OverlayPreferences(font_cn="DefaultCN", font_size=13)
    )

    assert prefs.window_pos == WindowPoint(7, 8)
    assert prefs.window_size == WindowSize(500, 300)
    assert prefs.font_size == 16
    assert prefs.font_weight == "Heavy"
    assert prefs.letter_spacing == 2.5
    assert prefs.line_spacing == 1.6
    assert prefs.menu_direction == "right"
    assert prefs.font_en == "UiEN"
    assert prefs.font_cn == "TopCN"
    assert prefs.ocr_backend == "windows"
    assert prefs.ocr_mode == "gpu"


def test_load_overlay_preferences_handles_missing_or_invalid_file(tmp_path):
    prefs = ConfigJsonStore(tmp_path / "missing.json").load_overlay_preferences(
        OverlayPreferences(font_size=19, menu_direction="left")
    )

    assert prefs.font_size == 19
    assert prefs.menu_direction == "left"


def test_normalize_overlay_preferences_clamps_ranges_and_enums():
    prefs = normalize_overlay_preferences(
        OverlayPreferences(
            font_size=-10,
            font_weight="Ultra",
            letter_spacing=-99,
            line_spacing=99,
            menu_direction="center",
            ocr_backend="invalid",
            ocr_mode="invalid",
        )
    )

    assert prefs.font_size == 8
    assert prefs.font_weight == "SemiBold"
    assert prefs.letter_spacing == -10.0
    assert prefs.line_spacing == 5.0
    assert prefs.menu_direction == "right"
    assert prefs.ocr_backend == "auto"
    assert prefs.ocr_mode == "auto"


def test_clamp_window_position_keeps_position_inside_any_screen():
    screens = [
        WindowBounds(left=0, top=0, width=1920, height=1080),
        WindowBounds(left=-1280, top=0, width=1280, height=720),
    ]

    assert clamp_window_position(WindowPoint(100, 100), WindowSize(400, 300), screens) == WindowPoint(100, 100)
    assert clamp_window_position(WindowPoint(-1000, 100), WindowSize(400, 300), screens) == WindowPoint(-1000, 100)


def test_clamp_window_position_clamps_to_first_screen_when_offscreen():
    screens = [WindowBounds(left=0, top=0, width=1920, height=1080)]

    assert clamp_window_position(WindowPoint(1900, 1000), WindowSize(400, 300), screens) == WindowPoint(1520, 780)
    assert clamp_window_position(WindowPoint(-100, -100), WindowSize(400, 300), screens) == WindowPoint(0, 0)


def test_clamp_window_position_keeps_original_without_screens():
    assert clamp_window_position(WindowPoint(50, 60), WindowSize(400, 300), []) == WindowPoint(50, 60)
