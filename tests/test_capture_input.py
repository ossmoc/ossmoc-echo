from pathlib import Path

import pytest
from PIL import Image

from ludiglot.core import capture_input
from ludiglot.core.capture import CaptureError, CaptureRegion
from ludiglot.core.capture_input import (
    CaptureInputAdapters,
    CaptureInputOptions,
    capture_input_to_memory,
    parse_capture_region,
    should_use_raw_capture,
)


def test_raw_capture_decision_matrix():
    assert should_use_raw_capture(True, "windows", "auto") is True
    assert should_use_raw_capture(True, "auto", "raw") is True
    assert should_use_raw_capture(True, "paddle", "auto") is False
    assert should_use_raw_capture(True, "windows", "png") is False
    assert should_use_raw_capture(False, "windows", "auto") is False


def test_parse_capture_region_from_dict_and_region():
    region = parse_capture_region({"left": "10", "top": "20", "width": "300", "height": "120"})

    assert region == CaptureRegion(left=10, top=20, width=300, height=120)
    assert parse_capture_region(region) == region


def test_selected_region_overrides_configured_window_mode(monkeypatch):
    calls = []
    selected = CaptureRegion(1, 2, 3, 4)

    def fake_capture(region):
        calls.append(region)
        return "image"

    monkeypatch.setattr(capture_input, "capture_region_to_image", fake_capture)

    result = capture_input_to_memory(
        CaptureInputOptions(capture_mode="window", window_title=None),
        selected_region=selected,
    )

    assert result == "image"
    assert calls == [selected]


def test_mss_region_mode_uses_raw_capture(monkeypatch):
    calls = []
    region = CaptureRegion(5, 6, 7, 8)
    raw = (b"raw", 7, 8)

    def fake_capture(region_arg):
        calls.append(region_arg)
        return raw

    monkeypatch.setattr(capture_input, "capture_region_to_raw", fake_capture)

    result = capture_input_to_memory(
        CaptureInputOptions(
            capture_mode="region",
            capture_region=region,
            ocr_raw_capture=True,
            ocr_backend="windows",
        )
    )

    assert result == raw
    assert calls == [region]


def test_winrt_region_mode_converts_native_image_to_raw(monkeypatch):
    region = CaptureRegion(5, 6, 1, 1)
    img = Image.new("RGBA", (1, 1), (1, 2, 3, 4))
    calls = []

    def fake_capture(region_arg):
        calls.append(region_arg)
        return img

    monkeypatch.setattr(capture_input, "capture_region_to_image_native", fake_capture)

    result = capture_input_to_memory(
        CaptureInputOptions(
            capture_mode="region",
            capture_backend="winrt",
            capture_region=region,
            ocr_raw_capture=True,
            ocr_backend="windows",
        )
    )

    assert result == (bytes([3, 2, 1, 4]), 1, 1)
    assert calls == [region]


def test_image_mode_loads_existing_image(tmp_path):
    image_path = tmp_path / "capture.png"
    Image.new("RGB", (2, 1), (10, 20, 30)).save(image_path)

    result = capture_input_to_memory(CaptureInputOptions(capture_mode="image", image_path=image_path))

    assert result.size == (2, 1)
    assert result.getpixel((0, 0)) == (10, 20, 30)


def test_image_mode_can_load_existing_image_as_raw(tmp_path):
    image_path = tmp_path / "capture.png"
    Image.new("RGBA", (1, 1), (10, 20, 30, 40)).save(image_path)

    result = capture_input_to_memory(
        CaptureInputOptions(
            capture_mode="image",
            image_path=image_path,
            ocr_raw_capture=True,
            ocr_backend="windows",
        )
    )

    assert result == (bytes([30, 20, 10, 40]), 1, 1)


def test_missing_image_mode_falls_back_to_fullscreen(monkeypatch, tmp_path):
    calls = []

    def fake_fullscreen():
        calls.append("image")
        return "fullscreen-image"

    monkeypatch.setattr(capture_input, "capture_fullscreen_to_image", fake_fullscreen)

    result = capture_input_to_memory(CaptureInputOptions(capture_mode="image", image_path=tmp_path / "missing.png"))

    assert result == "fullscreen-image"
    assert calls == ["image"]


def test_capture_error_falls_back_to_raw_fullscreen(monkeypatch):
    calls = []
    fallbacks = []

    def fake_window(title):
        raise CaptureError("boom")

    def fake_fullscreen_raw():
        calls.append("raw")
        return (b"screen", 1, 1)

    monkeypatch.setattr(capture_input, "capture_window_to_raw", fake_window)
    monkeypatch.setattr(capture_input, "capture_fullscreen_to_raw", fake_fullscreen_raw)

    result = capture_input_to_memory(
        CaptureInputOptions(
            capture_mode="window",
            window_title="Game",
            ocr_raw_capture=True,
            ocr_backend="windows",
        ),
        adapters=CaptureInputAdapters(on_fallback=fallbacks.append),
    )

    assert result == (b"screen", 1, 1)
    assert calls == ["raw"]
    assert fallbacks == ["捕获失败：boom，将回退到全屏截图"]


def test_capture_error_fallback_honors_png_windows_input(monkeypatch):
    calls = []

    def fake_window(title):
        raise CaptureError("boom")

    def fake_fullscreen_image():
        calls.append("image")
        return "fullscreen-image"

    def fake_fullscreen_raw():
        calls.append("raw")
        return (b"screen", 1, 1)

    monkeypatch.setattr(capture_input, "capture_window_to_image", fake_window)
    monkeypatch.setattr(capture_input, "capture_fullscreen_to_image", fake_fullscreen_image)
    monkeypatch.setattr(capture_input, "capture_fullscreen_to_raw", fake_fullscreen_raw)

    result = capture_input_to_memory(
        CaptureInputOptions(
            capture_mode="window",
            window_title="Game",
            ocr_raw_capture=True,
            ocr_backend="windows",
            ocr_windows_input="png",
        )
    )

    assert result == "fullscreen-image"
    assert calls == ["image"]


def test_select_mode_uses_adapters_with_snapshot():
    snapshot = object()
    region = CaptureRegion(1, 2, 3, 4)
    calls = []
    img = Image.new("RGBA", (1, 1), (4, 3, 2, 1))

    def select_region(snapshot_arg):
        calls.append(("select", snapshot_arg))
        return region

    def crop_snapshot(snapshot_arg, region_arg):
        calls.append(("crop", snapshot_arg, region_arg))
        return img

    result = capture_input_to_memory(
        CaptureInputOptions(
            capture_mode="select",
            ocr_raw_capture=True,
            ocr_backend="windows",
        ),
        snapshot=snapshot,
        adapters=CaptureInputAdapters(select_region=select_region, crop_snapshot=crop_snapshot),
    )

    assert result == (bytes([2, 3, 4, 1]), 1, 1)
    assert calls == [("select", snapshot), ("crop", snapshot, region)]


def test_select_mode_cancel_raises():
    with pytest.raises(RuntimeError, match="未选择区域"):
        capture_input_to_memory(
            CaptureInputOptions(capture_mode="select"),
            adapters=CaptureInputAdapters(select_region=lambda snapshot: None),
        )


def test_unknown_mode_raises():
    with pytest.raises(RuntimeError, match="未知 capture_mode: unknown"):
        capture_input_to_memory(CaptureInputOptions(capture_mode="unknown"))
