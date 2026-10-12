from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ludiglot.core.capture import (
    CaptureError,
    CaptureRegion,
    capture_fullscreen_to_image,
    capture_fullscreen_to_image_native,
    capture_fullscreen_to_raw,
    capture_region_to_image,
    capture_region_to_image_native,
    capture_region_to_raw,
    capture_window_to_image,
    capture_window_to_image_native,
    capture_window_to_raw,
)


@dataclass(frozen=True)
class CaptureInputOptions:
    capture_mode: str
    capture_backend: str = "mss"
    window_title: str | None = None
    capture_region: dict[str, Any] | CaptureRegion | None = None
    image_path: Path | None = None
    ocr_backend: str = "auto"
    ocr_raw_capture: bool = False
    ocr_windows_input: str = "auto"


@dataclass(frozen=True)
class CaptureInputAdapters:
    select_region: Callable[[Any | None], CaptureRegion | None] | None = None
    crop_snapshot: Callable[[Any, CaptureRegion], Any] | None = None
    on_fallback: Callable[[str], None] | None = None


def capture_options_from_config(config: Any) -> CaptureInputOptions:
    return CaptureInputOptions(
        capture_mode=str(getattr(config, "capture_mode", "select")),
        capture_backend=str(getattr(config, "capture_backend", "mss")),
        window_title=getattr(config, "window_title", None),
        capture_region=getattr(config, "capture_region", None),
        image_path=getattr(config, "image_path", None),
        ocr_backend=str(getattr(config, "ocr_backend", "auto")),
        ocr_raw_capture=bool(getattr(config, "ocr_raw_capture", False)),
        ocr_windows_input=str(getattr(config, "ocr_windows_input", "auto")),
    )


def should_use_raw_capture(ocr_raw_capture: bool, ocr_backend: str, ocr_windows_input: str) -> bool:
    return bool(ocr_raw_capture) and str(ocr_backend).lower() in {"windows", "auto"} and str(ocr_windows_input).lower() != "png"


def parse_capture_region(raw: dict[str, Any] | CaptureRegion | None) -> CaptureRegion:
    if isinstance(raw, CaptureRegion):
        return raw
    if not isinstance(raw, dict):
        raise RuntimeError("capture_mode=region 需要 capture_region")
    return CaptureRegion(
        left=int(raw["left"]),
        top=int(raw["top"]),
        width=int(raw["width"]),
        height=int(raw["height"]),
    )


def pil_image_to_bgra_raw(img_obj: Any) -> Any:
    if isinstance(img_obj, tuple) and len(img_obj) == 3:
        return img_obj
    try:
        from PIL import Image

        if isinstance(img_obj, Image.Image):
            if img_obj.mode != "RGBA":
                img_obj = img_obj.convert("RGBA")
            raw = img_obj.tobytes("raw", "BGRA")
            return (raw, img_obj.width, img_obj.height)
    except Exception:
        return img_obj
    return img_obj


def capture_input_to_memory(
    options: CaptureInputOptions,
    *,
    selected_region: CaptureRegion | None = None,
    snapshot: Any | None = None,
    adapters: CaptureInputAdapters | None = None,
) -> Any:
    use_raw = should_use_raw_capture(options.ocr_raw_capture, options.ocr_backend, options.ocr_windows_input)
    try:
        if selected_region is not None:
            return _capture_selected_region(selected_region, options, use_raw, snapshot, adapters)

        mode = str(options.capture_mode or "").lower()
        if mode == "window":
            return _capture_window(options, use_raw)
        if mode == "region":
            return _capture_region(parse_capture_region(options.capture_region), options, use_raw)
        if mode == "image":
            return _capture_image_mode(options, use_raw)
        if mode == "select":
            return _capture_select_mode(options, use_raw, snapshot, adapters)
        raise RuntimeError(f"未知 capture_mode: {options.capture_mode}")
    except CaptureError as exc:
        if adapters and adapters.on_fallback:
            adapters.on_fallback(f"捕获失败：{exc}，将回退到全屏截图")
        return _capture_fullscreen(options, use_raw)


def _capture_selected_region(
    selected_region: CaptureRegion,
    options: CaptureInputOptions,
    use_raw: bool,
    snapshot: Any | None,
    adapters: CaptureInputAdapters | None,
) -> Any:
    if snapshot is not None:
        if not adapters or not adapters.crop_snapshot:
            raise RuntimeError("capture_mode=select 需要 crop_snapshot adapter")
        return _maybe_raw(adapters.crop_snapshot(snapshot, selected_region), use_raw)
    return _capture_region(selected_region, options, use_raw)


def _capture_select_mode(
    options: CaptureInputOptions,
    use_raw: bool,
    snapshot: Any | None,
    adapters: CaptureInputAdapters | None,
) -> Any:
    if not adapters or not adapters.select_region:
        raise RuntimeError("capture_mode=select 需要 select_region adapter")
    region = adapters.select_region(snapshot)
    if region is None:
        raise RuntimeError("未选择区域")
    if snapshot is not None:
        if not adapters.crop_snapshot:
            raise RuntimeError("capture_mode=select 需要 crop_snapshot adapter")
        return _maybe_raw(adapters.crop_snapshot(snapshot, region), use_raw)
    return _capture_region(region, options, use_raw)


def _capture_window(options: CaptureInputOptions, use_raw: bool) -> Any:
    if not options.window_title:
        raise RuntimeError("capture_mode=window 需要 window_title")
    if _is_native_backend(options):
        return _maybe_raw(capture_window_to_image_native(options.window_title), use_raw)
    if use_raw:
        return capture_window_to_raw(options.window_title)
    return capture_window_to_image(options.window_title)


def _capture_region(region: CaptureRegion, options: CaptureInputOptions, use_raw: bool) -> Any:
    if _is_native_backend(options):
        return _maybe_raw(capture_region_to_image_native(region), use_raw)
    if use_raw:
        return capture_region_to_raw(region)
    return capture_region_to_image(region)


def _capture_image_mode(options: CaptureInputOptions, use_raw: bool) -> Any:
    if options.image_path and options.image_path.exists():
        from PIL import Image

        img = Image.open(options.image_path)
        img.load()
        return _maybe_raw(img, use_raw)
    return _capture_fullscreen(options, use_raw)


def _capture_fullscreen(options: CaptureInputOptions, use_raw: bool) -> Any:
    if _is_native_backend(options):
        return _maybe_raw(capture_fullscreen_to_image_native(), use_raw)
    if use_raw:
        return capture_fullscreen_to_raw()
    return capture_fullscreen_to_image()


def _maybe_raw(img_obj: Any, use_raw: bool) -> Any:
    return pil_image_to_bgra_raw(img_obj) if use_raw else img_obj


def _is_native_backend(options: CaptureInputOptions) -> bool:
    return str(options.capture_backend or "mss").lower() == "winrt"
