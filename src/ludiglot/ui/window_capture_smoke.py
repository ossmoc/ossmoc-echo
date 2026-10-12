"""Native WGC positive controls; captures only disposable synthetic windows."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout

from ludiglot.core.window_capture import WindowCapture, window_target, CaptureUnavailable


def check_window_capture():
    import win32gui
    app = QApplication.instance()
    target = QWidget()
    target.setWindowTitle("Ludiglot WGC native positive control")
    target.setGeometry(180, 180, 700, 380)
    target.setStyleSheet("background: #147832; color: white;")
    layout = QVBoxLayout(target)
    label = QLabel("This is the game window, not the window covering it.")
    label.setStyleSheet("font: 24px 'Segoe UI';")
    layout.addWidget(label)
    cover = QWidget()
    cover.setWindowTitle("Ludiglot WGC occluding controller")
    cover.setStyleSheet("background: #ef17bd;")
    cover.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
    capture = WindowCapture()
    def pump(seconds=.5):
        until = time.monotonic()+seconds
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(.01)
    def frame():
        until = time.monotonic()+5
        while time.monotonic() < until:
            app.processEvents()
            try:
                return capture.image(.05)
            except CaptureUnavailable:
                time.sleep(.02)
        raise RuntimeError("Native WGC did not return a frame")
    try:
        target.show(); pump()
        selected = window_target(int(target.winId()))
        capture.bind(selected)
        first = frame()
        bounds = capture.bounds()
        # Cover completely with an ordinary window, with exclusion DISABLED.
        # This proves separation is from target-HWND capture, not affinity.
        scale = target.devicePixelRatioF()
        cover.setGeometry(round(bounds["left"]/scale)-10, round(bounds["top"]/scale)-40,
                          round(bounds["width"]/scale)+30, round(bounds["height"]/scale)+70)
        cover.show(); cover.raise_(); pump()
        cover.activateWindow()
        pump()
        target.setStyleSheet("background: #236ca0; color: white;")
        pump()
        background = frame()
        pixel = background.getpixel((10,10))
        result = {"client_size": list(background.size), "baseline_pixel": first.getpixel((10,10)),
                  "covered_target_pixel": pixel,
                  "covered_window_updates": pixel == (35,108,160),
                  "not_occluder_pixels": pixel != (239,23,189),
                  "different_foreground": win32gui.GetForegroundWindow() != selected.hwnd}
        cover.hide()
        target.showMinimized(); pump()
        try:
            capture.image(.05)
            result["minimized_waits"] = False
        except CaptureUnavailable:
            result["minimized_waits"] = True
        target.showNormal(); pump()
        restored = frame()
        result["restore_returns_frame"] = restored.getpixel((10,10)) == (35,108,160)
        target.close(); pump()
        try:
            capture.image(.05)
            result["closed_waits"] = False
        except CaptureUnavailable:
            result["closed_waits"] = True
        return result
    finally:
        capture.close()
        target.close(); cover.close(); target.deleteLater(); cover.deleteLater()


def check_captured_dialogue(controller, path):
    """Private reference image -> real HWND capture -> OCR -> inline UI."""
    from PyQt6.QtGui import QPixmap
    from ludiglot.core.desktop_models import AUTO_DEFAULTS
    app = QApplication.instance()
    target = QLabel()
    target.setWindowTitle("Ludiglot private dialogue capture QA")
    target.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
    target.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    screen = app.primaryScreen()
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        raise ValueError("Missing private QA reference")
    pixmap.setDevicePixelRatio(screen.devicePixelRatio())
    target.setPixmap(pixmap)
    target.setGeometry(screen.geometry().x(), screen.geometry().y(),
                       round(pixmap.width()/screen.devicePixelRatio()), round(pixmap.height()/screen.devicePixelRatio()))
    capture = WindowCapture()
    try:
        target.show(); app.processEvents()
        capture.bind(window_target(int(target.winId())))
        image = capture.image(3)
        frame = controller.auto_processor.process(image, controller.engine, AUTO_DEFAULTS, force=True)
        first_timings = dict(controller.auto_processor.last_timings)
        controller.auto_overlay.set_frame(image.info["capture_bounds"], frame.blocks, AUTO_DEFAULTS)
        controller.auto_overlay.show(); app.processEvents()
        # Overlay is visible over the target, yet a second HWND frame must keep
        # the original English pixels and produce identical translations.
        second = capture.image(1)
        stable = controller.auto_processor.process(second, controller.engine, AUTO_DEFAULTS)
        return {"layouts": frame.detected_kinds, "translated_blocks": len(frame.blocks),
                "dialogue_translated": any(b.kind == "dialogue" for b in frame.blocks),
                "choices_translated": any(b.kind == "choices" for b in frame.blocks),
                "overlay_visible": controller.auto_overlay.isVisible(),
                "overlay_not_captured": image.tobytes() == second.tobytes(),
                "stable_targets": [b.target for b in frame.blocks] == [b.target for b in stable.blocks],
                "first_timings": first_timings, "unchanged_frame_timings": dict(controller.auto_processor.last_timings)}
    finally:
        capture.close(); target.close(); target.deleteLater(); controller.auto_overlay.hide()


if __name__ == "__main__":
    import json
    app = QApplication([])
    print(json.dumps(check_window_capture(), ensure_ascii=False))
