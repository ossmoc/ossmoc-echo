from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class CaptureProcessRequest:
    capture_image: Callable[[], Any]
    ocr_engine: Any
    matcher: Any
    ocr_backend: str = "auto"
    debug_dump_input: bool = False
    debug_dump_dir: Path | None = None


@dataclass(frozen=True)
class CaptureProcessOutcome:
    status: str
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class CaptureProcessCallbacks:
    status: Callable[[str], None] | None = None
    log: Callable[[str], None] | None = None
    error: Callable[[str], None] | None = None
    result: Callable[[dict[str, Any]], None] | None = None


def run_capture_match_workflow(
    request: CaptureProcessRequest,
    callbacks: CaptureProcessCallbacks | None = None,
) -> CaptureProcessOutcome:
    callbacks = callbacks or CaptureProcessCallbacks()
    t_total_start = time.time()
    _emit(callbacks.status, "捕获中…")

    t_capture_start = time.time()
    try:
        img_obj = request.capture_image()
    except Exception as exc:
        _emit(callbacks.error, f"截图失败: {exc}")
        return CaptureProcessOutcome(status="capture_error")
    _emit(callbacks.log, f"[PERF] 截图耗时: {(time.time() - t_capture_start):.3f}s")

    size = _image_size(img_obj)
    if size is not None:
        img_w, img_h = size
        if img_w < 8 or img_h < 8:
            _emit(callbacks.log, f"[CAPTURE] 选区过小({img_w}x{img_h})，已跳过")
            _emit(callbacks.status, "选区过小，已取消")
            return CaptureProcessOutcome(status="tiny_capture")

    t_ocr_start = time.time()
    try:
        if request.debug_dump_input:
            _dump_ocr_input(img_obj, request.debug_dump_dir, callbacks)

        ocr_result = request.ocr_engine.recognize_pipeline(
            img_obj,
            backend=request.ocr_backend,
        )
        box_lines = ocr_result.boxes
        lines = ocr_result.lines
        backend = ocr_result.backend or "windows"
        _emit(callbacks.log, f"[OCR] 后端: {_backend_label(backend)}")
        _emit(callbacks.log, f"[PERF] OCR识别耗时: {(time.time() - t_ocr_start):.3f}s")
    except Exception as exc:
        _emit(callbacks.error, f"OCR 失败: {exc}")
        return CaptureProcessOutcome(status="ocr_error")

    if not lines:
        _emit(callbacks.status, "OCR 未识别到文本")
        _emit(callbacks.log, "[OCR] 未识别到文本")
        return CaptureProcessOutcome(status="no_text")

    _emit(callbacks.log, "[OCR] 识别结果:")
    for text, conf in lines:
        _emit(callbacks.log, f"  - {text} (conf={conf:.3f})")

    if not request.matcher:
        _emit(callbacks.error, "匹配服务未就绪")
        return CaptureProcessOutcome(status="matcher_not_ready")

    t_match_start = time.time()
    result = request.matcher.match(lines)
    _emit(callbacks.log, f"[PERF] 文本匹配耗时: {(time.time() - t_match_start):.3f}s")
    if result is None:
        _emit(callbacks.status, "未提取到可用文本")
        _emit(callbacks.log, "[OCR] 未找到有效匹配 (Score too low)")
        return CaptureProcessOutcome(status="no_match")

    _emit(callbacks.log, f"[DEBUG] _capture_and_process: Got result. Keys: {list(result.keys())}")
    t_emit_start = time.time()
    try:
        safe_result = copy.deepcopy(result)
        _emit(callbacks.log, "[DEBUG] _capture_and_process: Emitting safe_result...")
        _emit(callbacks.result, safe_result)
        _emit(callbacks.log, "[DEBUG] _capture_and_process: Result emitted.")
    except Exception as exc:
        _emit(callbacks.log, f"[ERROR] CRITICAL: Failed to emit result signal: {exc}")
        _emit(callbacks.error, f"Internal Error: Signal Emission Failed: {exc}")
        return CaptureProcessOutcome(status="emit_error")
    _emit(callbacks.log, f"[PERF] 结果传递耗时: {(time.time() - t_emit_start):.3f}s")
    _emit(callbacks.log, f"[PERF] ===== 总耗时: {(time.time() - t_total_start):.3f}s =====")
    _emit(callbacks.status, "就绪")
    _emit(callbacks.log, "[DEBUG] _capture_and_process: Status emitted. Done.")
    return CaptureProcessOutcome(status="success", result=safe_result)


def _image_size(img_obj: Any) -> tuple[int, int] | None:
    if img_obj is None:
        return None
    if isinstance(img_obj, tuple) and len(img_obj) == 3:
        return int(img_obj[1]), int(img_obj[2])
    width = getattr(img_obj, "width", None)
    height = getattr(img_obj, "height", None)
    if width is None or height is None:
        return None
    return int(width), int(height)


def _dump_ocr_input(img_obj: Any, debug_dump_dir: Path | None, callbacks: CaptureProcessCallbacks) -> None:
    try:
        debug_dir = debug_dump_dir or Path.cwd()
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / "last_ocr_input.png"
        if isinstance(img_obj, tuple) and len(img_obj) == 3:
            raw_bytes, width, height = img_obj
            from PIL import Image
            img = Image.frombytes("RGBA", (int(width), int(height)), raw_bytes, "raw", "BGRA")
            img.save(debug_path)
        else:
            img_obj.save(debug_path)
        _emit(callbacks.log, f"[OCR] 输入已保存: {debug_path}")
    except Exception as exc:
        _emit(callbacks.log, f"[OCR] 保存输入失败: {exc}")


def _backend_label(backend: str) -> str:
    return {
        "windows": "WindowsOCR",
    }.get(backend, backend)


def _emit(callback: Callable[[Any], None] | None, value: Any) -> None:
    if callback:
        callback(value)
