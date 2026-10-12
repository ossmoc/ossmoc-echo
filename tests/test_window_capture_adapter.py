from types import SimpleNamespace

import pytest
from PIL import Image

from ludiglot.core.capture import CaptureRegion
from ludiglot.core.window_capture import CaptureUnavailable
from ludiglot.ui.qt_capture_adapter import QtCaptureAdapter


def test_window_crops_relative_to_anchor_not_current_desktop_position(monkeypatch):
    adapter = QtCaptureAdapter(SimpleNamespace(), lambda _: None)
    adapter.window_settings = {"mode":"window", "anchor":{"left":100,"top":100,"width":500,"height":300}}
    image = Image.new("RGB", (500,300), "green")
    image.paste("red", (20,40,120,100))
    image.info["capture_bounds"] = {"left":800,"top":400,"width":500,"height":300}
    monkeypatch.setattr(adapter.window_capture,"image",lambda: image)
    def forbidden(*args, **kwargs):
        raise AssertionError("Window source fell back to desktop!")
    monkeypatch.setattr("ludiglot.ui.qt_capture_adapter.capture_input_to_memory",forbidden)
    crop = adapter.capture_image_to_memory(CaptureRegion(120,140,100,60))
    assert crop.size == (100,60) and crop.getpixel((0,0)) == (255,0,0)
    assert adapter.capture_auto_frame(None) is image


def test_window_unavailable_never_falls_back_to_mss(monkeypatch):
    adapter = QtCaptureAdapter(SimpleNamespace(), lambda _: None)
    adapter.window_settings = {"mode":"window"}
    monkeypatch.setattr("ludiglot.ui.qt_capture_adapter.capture_input_to_memory",lambda *a, **kw: pytest.fail("desktop fallback"))
    with pytest.raises(CaptureUnavailable):
        adapter.capture_auto_frame(None)


def test_resized_window_cannot_reuse_old_manual_crop(monkeypatch):
    adapter = QtCaptureAdapter(SimpleNamespace(), lambda _: None)
    adapter.window_settings = {"mode":"window", "anchor":{"left":0,"top":0,"width":500,"height":300}}
    monkeypatch.setattr(adapter.window_capture,"image",lambda: Image.new("RGB",(1000,600)))
    with pytest.raises(CaptureUnavailable,match="Kích thước"):
        adapter.capture_image_to_memory(CaptureRegion(20,20,100,80))
