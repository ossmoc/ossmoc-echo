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


def check_translated_screenshot(controller,report_path):
    """Real HWND -> compositor -> Windows image clipboard -> PNG round trip."""
    import ctypes
    import win32gui
    from PyQt6.QtCore import QMimeData
    from PyQt6.QtGui import QImage,QPixmap
    from PIL import Image,ImageDraw,ImageFont
    from ludiglot.core.auto_wuwa import AutoTextBlock
    from ludiglot.core.desktop_models import AUTO_DEFAULTS
    app=QApplication.instance()
    clipboard=app.clipboard()
    previous=QMimeData()
    old_mime=clipboard.mimeData()
    if old_mime:
        if old_mime.hasImage():previous.setImageData(old_mime.imageData())
        for fmt in old_mime.formats():previous.setData(fmt,old_mime.data(fmt))
    target=QLabel(); target.setWindowTitle('Ossmoc screenshot synthetic QA')
    target.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.Tool)
    target.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    game=Image.new('RGB',(1920,1080),'#1b365d')
    ImageDraw.Draw(game).text((550,900),'Game English subtitle.',font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',28),fill='white')
    data=game.tobytes()
    pixmap=QPixmap.fromImage(QImage(data,game.width,game.height,game.width*3,QImage.Format.Format_RGB888))
    screen=app.primaryScreen(); dpi=screen.devicePixelRatio()
    pixmap.setDevicePixelRatio(dpi); target.setPixmap(pixmap)
    target.setGeometry(screen.geometry().x(),screen.geometry().y(),round(1920/dpi),round(1080/dpi))
    capture=WindowCapture()
    old_service=controller.capture_adapter.window_capture
    old_window=controller.raw['window_capture']
    old_bounds=controller._last_capture_bounds
    old_hidden=controller.overlay.user_hidden
    timer_active=controller.environment_timer.isActive()
    controller.environment_timer.stop()
    try:
        target.show(); app.processEvents()
        selected=window_target(int(target.winId())); capture.bind(selected)
        base=capture.image(3); bounds=base.info['capture_bounds']
        controller.capture_adapter.window_capture=capture
        controller.raw['window_capture']={'mode':'window','target':selected.identity(),'anchor':bounds}
        controller._last_capture_bounds=bounds; controller.overlay.user_hidden=False
        block=AutoTextBlock('dialogue',(500,891,920,55),'Game English subtitle.',
            'Kiểm tra ảnh chụp kèm bản dịch Việt.',('qa','Main_1'))
        controller.auto_overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
        controller.auto_overlay.show(); app.processEvents()
        focus=win32gui.GetForegroundWindow()
        before=(controller.session.generation,controller.session.running)
        affinity=ctypes.c_uint()
        ctypes.windll.user32.GetWindowDisplayAffinity(int(controller.auto_overlay.winId()),ctypes.byref(affinity))
        old_affinity=affinity.value
        image=controller.capture_translated_screenshot()
        if image is None:raise RuntimeError(controller.status)
        files=sorted((controller.paths.data_root/'screenshots').glob('*.png'),key=lambda p:p.stat().st_mtime_ns)
        saved=QImage(str(files[-1])); copied=clipboard.image()
        mask=controller.auto_overlay._mask_rect(block)
        x=round(mask.center().x()*bounds['width']/controller.auto_overlay.width())
        y=round((mask.top()+5)*bounds['height']/controller.auto_overlay.height())
        def same_pixels(a,b):
            a=a.convertToFormat(QImage.Format.Format_RGBA8888)
            b=b.convertToFormat(QImage.Format.Format_RGBA8888)
            return a.size()==b.size() and a.constBits().asstring(a.sizeInBytes())==b.constBits().asstring(b.sizeInBytes())
        result={'image_clipboard':clipboard.mimeData().hasImage(),
            'physical_size':image.width()==bounds['width'] and image.height()==bounds['height'],
            'clipboard_matches_png':same_pixels(copied,saved) and same_pixels(copied,image),
            'includes_translation':image.pixelColor(x,y).getRgb()[:3]!=base.getpixel((x,y)),
            'background_unchanged':image.pixelColor(10,10).getRgb()[:3]==base.getpixel((10,10)),
            'no_focus_change':focus==win32gui.GetForegroundWindow(),
            'session_unchanged':before==(controller.session.generation,controller.session.running),
            'ocr_input_still_clean':capture.image(1).tobytes()==base.tobytes()}
        ctypes.windll.user32.GetWindowDisplayAffinity(int(controller.auto_overlay.winId()),ctypes.byref(affinity))
        result['exclusion_unchanged']=affinity.value==old_affinity
        controller.auto_overlay.hide()
        hidden=controller.capture_translated_screenshot()
        result['button_works_with_background_hidden_overlay']=hidden is not None and hidden==image and not controller.auto_overlay.isVisible()
        from ludiglot.core.auto_wuwa import AutoDocument
        reader=controller.auto_reader
        reader.set_document(AutoDocument(('qa','document'),'Tài liệu Việt','Một đoạn tài liệu để kiểm tra ảnh chụp.'),bounds,
            {**AUTO_DEFAULTS,'reader_geometry':{'x':target.x()+30,'y':target.y()+100,'width':400,'height':240}})
        app.processEvents()
        with_reader=controller.capture_translated_screenshot()
        reader.hide()
        hidden_reader=controller.capture_translated_screenshot()
        result['reader_included_without_stealing_focus']=with_reader is not None and not same_pixels(with_reader,image) and focus==win32gui.GetForegroundWindow()
        result['background_hidden_reader_included']=hidden_reader is not None and same_pixels(hidden_reader,with_reader) and not reader.isVisible()
        reader.close()
        dismissed_reader=controller.capture_translated_screenshot()
        result['dismissed_reader_excluded']=dismissed_reader is not None and same_pixels(dismissed_reader,image)
        image.save(str(report_path.with_suffix('.screenshot-native.png')))
        result['success']=all(result.values())
        return result
    finally:
        clipboard.setMimeData(previous)
        controller.capture_adapter.window_capture=old_service
        controller.raw['window_capture']=old_window
        controller._last_capture_bounds=old_bounds
        controller.overlay.user_hidden=old_hidden
        controller.auto_overlay.hide()
        controller.auto_reader.set_document(None,{}, {})
        capture.close(); target.close(); target.deleteLater()
        if timer_active:controller.environment_timer.start()
