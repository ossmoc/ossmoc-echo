"""Opt-in, machine-local checks for native Auto windows in source/frozen builds."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ludiglot.core.auto_wuwa import AutoDocument, AutoTextBlock
from ludiglot.core.desktop_models import AUTO_DEFAULTS


def check_auto_windows(controller, report_path: Path, image_paths=()):
    import mss
    import win32gui
    from PIL import Image

    with mss.mss() as capture:
        bounds = dict(capture.monitors[1])
    settings = {**AUTO_DEFAULTS, "reader_geometry": {}}
    app = QApplication.instance()
    inline, reader, fallback = controller.auto_overlay, controller.auto_reader, controller.auto_fallback
    # Smoke reports use a separate QA config, but do not let moving the reader
    # change even that config. No images or paths are persisted as app settings.
    reader.geometry_changed.disconnect()
    reader.font_changed.disconnect()
    controller.overlay.hide()
    foreground = win32gui.GetForegroundWindow()
    def probe(window):
        import ctypes
        # Positive control: without exclusion the real MSS backend MUST see the
        # marker. This also detects a mistaken client/title-bar probe position.
        ctypes.windll.user32.SetWindowDisplayAffinity(int(window.winId()), 0)
        window._affinity_requested = True
        saw_marker = not window.verify_capture_exclusion()
        window._affinity_requested = window._apply_capture_affinity()
        return saw_marker, window.verify_capture_exclusion()
    width, height = bounds["width"], bounds["height"]
    sample = AutoTextBlock("dialogue", (round(width*.25), round(height*.83), round(width*.5), round(height*.06)),
                           "Native window test", "Kiểm tra cửa sổ Việt — chữ có dấu", ("qa", "sample"))
    inline.set_frame(bounds, (sample,), settings)
    inline.show(); app.processEvents()
    inline_positive, inline_ok = probe(inline)
    inline.grab().save(str(report_path.with_suffix(".auto-native.png")))
    from ludiglot.ui.window_capture_smoke import check_window_capture
    result = {"inline_capture_excluded": inline_ok, "inline_positive_control": inline_positive,
              "inline_click_through": bool(inline.windowFlags() & Qt.WindowType.WindowTransparentForInput),
              "inline_no_focus": foreground == win32gui.GetForegroundWindow()}
    from rapidfuzz import fuzz, process
    result['matching_backend']={'ratio':fuzz.ratio.__module__,'extract':process.extract.__module__}
    result['native_matching_backend']=all('_cpp' in value for value in result['matching_backend'].values())
    answer = AutoTextBlock('choices',(round(width*.67),round(height*.50),round(width*.18),round(height*.022)),
        'This is my longtime partner.', 'Đây là người bạn đã luôn đồng hành cùng tôi.', ('qa','answer'),1)
    next_answer = replace(answer,bounds=(answer.bounds[0],round(height*.57),answer.bounds[2],answer.bounds[3]),target='Đáp án tiếp theo',order=2)
    inline.set_frame(bounds,(answer,next_answer),settings); app.processEvents()
    inline.grab().save(str(report_path.with_suffix('.answer-native.png')))
    # Fixed-font text may legitimately exceed an original answer button.
    # In that case keep English; never shrink, clip or open a top fallback.
    expected_font = max(1,round(settings['choice_font_size']*inline.height()/1080))
    result['answer_fixed_font_without_top_box'] = not inline.content_truncated and all(
        (layout[1].pixelSize()==expected_font) if (layout:=inline._layout(block))
        else block in inline.overflow and inline._mask_rect(block) is None
        for block in (answer,next_answer))
    result['answer_inline_blocks'] = 2-len(inline.overflow)
    inline.set_frame(bounds,(sample,),settings)
    frozen_layout=inline._layout(sample)
    signature=inline.content_signature
    stable=True
    for delta in (1,-1,2,-2,0,1,0):
        jitter=replace(sample,bounds=(sample.bounds[0]+delta,sample.bounds[1]+delta,
            sample.bounds[2]-2*abs(delta),sample.bounds[3]),source='OCR variation')
        inline.set_frame(bounds,(jitter,),settings); app.processEvents()
        rect,font=inline._layout(jitter)
        stable &= rect.getRect()==frozen_layout[0].getRect() and font.pixelSize()==frozen_layout[1].pixelSize()
        stable &= inline.content_signature is signature
    result['same_sentence_presentation_stable']=stable
    inline.hide()
    doc = AutoDocument(("qa", "document"), "Kiểm tra khung đọc", "Văn bản Việt có dấu và cuộn riêng.\n"*150)
    reader.set_document(doc, bounds, settings); app.processEvents()
    result["reader_positive_control"], result["reader_capture_excluded"] = probe(reader)
    result["reader_no_initial_focus"] = foreground == win32gui.GetForegroundWindow()
    reader.text.verticalScrollBar().setValue(180)
    reader.set_document(doc, bounds, settings)
    result["reader_preserves_scroll"] = reader.text.verticalScrollBar().value() == 180
    reader.close(); reader.set_document(doc, bounds, settings)
    result["reader_stays_dismissed"] = not reader.isVisible()
    reader.set_document(None, bounds, settings)
    fallback.set_content([{"target": "Đáp án 2: Kiểm tra phần dịch quá dài chuyển lên khung riêng."}])
    fallback.show_output(); app.processEvents()
    result["fallback_positive_control"], result["fallback_capture_excluded"] = probe(fallback)
    fallback.hide()
    result["window_capture"] = check_window_capture()
    if len(image_paths) >= 3:
        from ludiglot.ui.window_capture_smoke import check_captured_dialogue
        result["captured_dialogue"] = check_captured_dialogue(controller, image_paths[2])
    results = []
    if image_paths:
        # Exercise the frozen CPU runtime even when Windows OCR succeeds.
        # This is opt-in QA only; never changes the user's OCR configuration.
        from types import SimpleNamespace
        from ludiglot.core.auto_wuwa import AutoWuwaProcessor
        class EmptyWindowsOcr:
            def recognize_pipeline(self, image, **kwargs):
                return SimpleNamespace(boxes=[])
        with Image.open(image_paths[-1]) as original:
            picture=original.convert('RGB')
        cpu=AutoWuwaProcessor(controller.auto_processor.index, controller.auto_processor.fallback)
        cpu_frame=cpu.process(picture,EmptyWindowsOcr(),{**settings,'document':False,'choices':False,'transition':False},force=True)
        result['cpu_fallback']={'ready':cpu.fallback.engine is not None,
            'verified_dialogue':any(b.kind=='dialogue' and not b.partial for b in cpu_frame.blocks),
            'source_ids':[b.identity for b in cpu_frame.blocks]}
    for number, path in enumerate(image_paths, 1):
        with Image.open(path) as original:
            image = original.convert("RGB")
        frame = controller.auto_processor.process(image, controller.engine, settings, force=True)
        blocks = tuple(replace(b, bounds=(round(b.bounds[0]*width/image.width), round(b.bounds[1]*height/image.height),
                                          round(b.bounds[2]*width/image.width), round(b.bounds[3]*height/image.height))) for b in frame.blocks)
        inline.set_frame(bounds, blocks, settings)
        inline.show(); app.processEvents()
        inline.grab().save(str(report_path.with_suffix(f".auto-{number}.png")))
        fallback_blocks = []  # Auto never opens a top translation box.
        fallback.set_content([{'target': b.target} for b in fallback_blocks])
        fallback.show_output(); app.processEvents()
        if fallback_blocks:
            fallback.grab().save(str(report_path.with_suffix(f".fallback-{number}.png")))
        reader.set_document(frame.document, bounds, settings); app.processEvents()
        if frame.document:
            reader.grab().save(str(report_path.with_suffix(f".reader-{number}.png")))
        results.append({"number": number, "layouts": frame.detected_kinds,
                        "translated_blocks": len(blocks), "inline_blocks": len(blocks)-len(inline.overflow),
                        "fallback_blocks": len(fallback_blocks), "overflow_blocks": len(inline.overflow), "document": bool(frame.document),
                        "dialogue_layouts": [{"bounds": rect.getRect(), "font_pixels": font.pixelSize()}
                            for block in blocks if block.kind == 'dialogue' and (layout := inline._layout(block))
                            for rect, font in (layout,)],
                        "inline_text_truncated": inline.content_truncated,
                        "timings": dict(controller.auto_processor.last_timings),
                        "partial_blocks": sum(b.partial for b in blocks),
                        "caption_modes": [b.caption_mode for b in blocks if b.kind=='dialogue'],
                        "fallback_visible": fallback.isVisible(),
                        "document_characters": len(frame.document.target) if frame.document else 0,
                        "source_ids": [b.identity for b in blocks], "diagnostics": frame.diagnostics})
        inline.hide(); fallback.hide(); reader.set_document(None, bounds, settings)
    result["private_images"] = results
    result["window_capture_success"] = all(result["window_capture"].get(k) for k in (
        "covered_window_updates", "not_occluder_pixels", "different_foreground", "minimized_waits", "restore_returns_frame", "closed_waits"))
    if "captured_dialogue" in result:
        result["window_capture_success"] = result["window_capture_success"] and all(result["captured_dialogue"].get(k) for k in (
            "dialogue_translated", "choices_translated", "overlay_visible", "overlay_not_captured", "stable_targets"))
    result["success"] = all(result[k] for k in ("inline_capture_excluded", "inline_click_through", "inline_no_focus",
        "reader_capture_excluded", "reader_no_initial_focus", "reader_preserves_scroll", "reader_stays_dismissed", "fallback_capture_excluded",
        "inline_positive_control", "reader_positive_control", "fallback_positive_control"))
    result['success'] = result['success'] and result['answer_fixed_font_without_top_box']
    result['success'] = result['success'] and result['same_sentence_presentation_stable']
    result['success'] = result['success'] and result['native_matching_backend']
    if 'cpu_fallback' in result:
        result['success'] = result['success'] and all(result['cpu_fallback'][k] for k in ('ready','verified_dialogue'))
    return result
