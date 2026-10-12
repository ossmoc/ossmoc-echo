from types import SimpleNamespace

import pytest
from PIL import Image

from ludiglot.core.capture import CaptureRegion
from ludiglot.core.window_capture import CaptureUnavailable, WindowCapture, client_crop_box, local_crop_box


def test_client_crops_only_known_actual_window_geometry():
    client = {"left":108,"top":130,"width":640,"height":360}
    assert client_crop_box((656,398), client, [(100,100,756,498)]) == (8,30,648,390)
    assert client_crop_box((640,360), client, []) == (0,0,640,360)
    with pytest.raises(CaptureUnavailable):
        client_crop_box((800,600), client, [(100,100,756,498)])


def test_regions_are_relative_to_selected_app_and_cannot_capture_desktop():
    anchor = {"left":-1920,"top":200,"width":1920,"height":1080}
    assert local_crop_box(CaptureRegion(-1800,1060,1200,150), anchor, (1920,1080)) == (120,860,1320,1010)
    with pytest.raises(CaptureUnavailable):
        local_crop_box(CaptureRegion(0,0,100,100), anchor, (1920,1080))


class Native:
    instances = []
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.handlers = {}
        self.stopped = False
        self.instances.append(self)
    def event(self, callback):
        self.handlers[callback.__name__] = callback
    def start_free_threaded(self):
        return self
    def stop(self):
        self.stopped = True
    def frame(self, rgb):
        image = Image.new("RGB", (100,60), rgb)
        buffer = SimpleNamespace(tobytes=lambda: image.tobytes("raw", "BGRX"))
        self.handlers["on_frame_arrived"](SimpleNamespace(width=100,height=60,frame_buffer=buffer), self)


def service():
    return WindowCapture(factory=Native, geometry=lambda target: ({"left":300,"top":100,"width":100,"height":60}, []))


def test_hwnd_capture_copies_latest_frame_and_ignores_old_callbacks():
    capture = service()
    capture.bind(SimpleNamespace(hwnd=123))
    old = Native.instances[-1]
    assert old.kwargs["window_hwnd"] == 123 and old.kwargs["monitor_index"] is None
    old.frame((10,20,30))
    assert capture.image(0).getpixel((0,0)) == (10,20,30)
    capture.bind(SimpleNamespace(hwnd=456))
    current = Native.instances[-1]
    old.frame((200,0,0))
    with pytest.raises(CaptureUnavailable):
        capture.image(0)
    current.frame((1,2,3))
    assert capture.image(0).getpixel((0,0)) == (1,2,3)
    capture.close()
    assert current.stopped


def test_minimized_waits_for_a_new_frame_after_restore():
    capture = service()
    capture.bind(SimpleNamespace(hwnd=123))
    native = Native.instances[-1]
    native.frame((1,2,3))
    geometry = capture._geometry
    def minimized(_):
        raise CaptureUnavailable("minimized")
    capture._geometry = minimized
    with pytest.raises(CaptureUnavailable):
        capture.image(0)
    capture._geometry = geometry
    with pytest.raises(CaptureUnavailable):
        capture.image(0)
    native.frame((4,5,6))
    assert capture.image(0).getpixel((0,0)) == (4,5,6)
    capture.close()


def test_closed_window_never_reuses_last_frame():
    capture = service()
    capture.bind(SimpleNamespace(hwnd=123))
    native = Native.instances[-1]
    native.frame((1,2,3))
    native.handlers["on_closed"]()
    with pytest.raises(CaptureUnavailable):
        capture.image(0)
    capture.close()
