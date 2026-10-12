from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
import threading
import time

from ludiglot.core.capture import CaptureRegion
from ludiglot.core.capture_match_workflow import (
    CaptureProcessCallbacks,
    CaptureProcessRequest,
    run_capture_match_workflow,
)


class CaptureAdapter(Protocol):
    def capture_desktop_snapshot(self) -> Any:
        ...

    def select_region(self, snapshot: Any | None = None) -> CaptureRegion | None:
        ...

    def capture_image_to_memory(self, selected_region: CaptureRegion | None, snapshot: Any | None = None) -> Any:
        ...


@dataclass(frozen=True)
class CaptureSessionCallbacks:
    status: Callable[[str], None]
    log: Callable[[str], None]
    error: Callable[[str], None]
    result: Callable[[dict[str, Any]], None]


ThreadStarter = Callable[[Callable[[], None]], None]
WorkflowRunner = Callable[[CaptureProcessRequest, CaptureProcessCallbacks], Any]


def start_daemon_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


class OverlayCaptureSession:
    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        ocr_engine_provider: Callable[[], Any],
        matcher_provider: Callable[[], Any],
        capture_adapter: CaptureAdapter,
        callbacks: CaptureSessionCallbacks,
        stop_audio: Callable[[bool], None],
        clear_result_audio_state: Callable[[], None],
        exception_region_saved: Callable[[CaptureRegion], None] | None = None,
        thread_starter: ThreadStarter = start_daemon_thread,
        workflow_runner: WorkflowRunner = run_capture_match_workflow,
    ) -> None:
        self._config_provider = config_provider
        self._ocr_engine_provider = ocr_engine_provider
        self._matcher_provider = matcher_provider
        self._capture_adapter = capture_adapter
        self._callbacks = callbacks
        self._stop_audio = stop_audio
        self._clear_result_audio_state = clear_result_audio_state
        self._exception_region_saved = exception_region_saved or (lambda region: None)
        self._thread_starter = thread_starter
        self._workflow_runner = workflow_runner
        self._running = False
        self._continuous_active = False
        self._continuous_stop = threading.Event()
        self._last_result_signature: tuple[Any, ...] | None = None
        self._exception_running = False
        self._continuous_pause = threading.Event()
        self._workflow_lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_continuous(self) -> bool:
        return self._continuous_active

    def trigger(self, force_select: bool = False) -> None:
        if self._continuous_active:
            self.stop_continuous()
            return
        if self._running:
            self._callbacks.log("[HOTKEY] 正在处理中，忽略重复触发")
            return
        self._running = True
        t_start = time.time()
        selected_region: CaptureRegion | None = None
        snapshot: Any | None = None
        thread_started = False
        try:
            # 新一轮 OCR 前先停止当前播放，避免旧音频与新结果串音
            self._stop_audio(False)
            self._clear_result_audio_state()
            self._callbacks.log("[HOTKEY] 触发捕获")
            config = self._config_provider()
            continuous = bool(getattr(config, "realtime_enabled", False))
            if continuous:
                force_select = True
            if force_select or getattr(config, "capture_mode", None) == "select":
                self._callbacks.status("冻结屏幕…")
                t_snap_start = time.time()
                try:
                    snapshot = self._capture_adapter.capture_desktop_snapshot()
                except Exception as exc:
                    snapshot = None
                    self._callbacks.log(f"[CAPTURE] 预截图失败，回退实时框选: {exc}")
                t_snap_end = time.time()
                if snapshot is not None:
                    self._callbacks.log(f"[PERF] 屏幕快照耗时: {(t_snap_end - t_snap_start):.3f}s")
                self._callbacks.status("请选择 OCR 区域…")
                t_select_start = time.time()
                selected_region = self._capture_adapter.select_region(snapshot)
                t_select_end = time.time()
                self._callbacks.log(f"[PERF] 区域选择耗时: {(t_select_end - t_select_start):.3f}s")
                if selected_region is None:
                    self._callbacks.status("已取消")
                    return

            if continuous:
                self._continuous_stop.clear()
                self._continuous_active = True
                self._last_result_signature = None
                self._callbacks.status("Realtime OCR: đang chạy — Alt+W để dừng")
                self._callbacks.log("[REALTIME] Đã bắt đầu theo dõi vùng OCR")
                self._thread_starter(lambda: self._run_continuous(selected_region))
            else:
                self._thread_starter(lambda: self._run_worker(selected_region, snapshot))
            thread_started = True
            self._callbacks.log(f"[PERF] 异步调用总耗时: {(time.time() - t_start):.3f}s")
        except Exception as exc:
            self._callbacks.log(f"[CAPTURE] 触发捕获异常: {exc}")
            self._callbacks.status("捕获失败")
        finally:
            # 仅在未成功启动后台线程时（后台线程会自己清除标志）清除
            if not thread_started:
                self._running = False
                self._continuous_active = False

    def stop_continuous(self) -> None:
        if not self._continuous_active:
            return
        self._continuous_stop.set()
        self._continuous_active = False
        self._callbacks.status("Realtime OCR: đã dừng")
        self._callbacks.log("[REALTIME] Đã dừng theo dõi vùng OCR")

    def trigger_exception(self, force_select: bool = False) -> None:
        if self._exception_running:
            self._callbacks.log("[EXCEPTION] Đang xử lý, bỏ qua lần bấm lặp")
            return
        self._exception_running = True
        self._continuous_pause.set()
        thread_started = False
        try:
            config = self._config_provider()
            selected_region = _capture_region_from_mapping(getattr(config, "exception_capture_region", None))
            snapshot = None
            if force_select or selected_region is None:
                self._callbacks.status("Chọn vùng ngoại lệ…")
                try:
                    snapshot = self._capture_adapter.capture_desktop_snapshot()
                except Exception as exc:
                    self._callbacks.log(f"[EXCEPTION] Không thể chụp nền, dùng chọn vùng trực tiếp: {exc}")
                selected_region = self._capture_adapter.select_region(snapshot)
                if selected_region is None:
                    self._callbacks.status("Đã hủy chọn vùng ngoại lệ")
                    return
                self._exception_region_saved(selected_region)
                self._callbacks.log(f"[EXCEPTION] Đã lưu vùng: {selected_region}")
            self._callbacks.status("Đang chụp vùng ngoại lệ…")
            self._thread_starter(lambda: self._run_exception_worker(selected_region, snapshot))
            thread_started = True
        except Exception as exc:
            self._callbacks.log(f"[EXCEPTION] Lỗi: {exc}")
            self._callbacks.error(str(exc))
        finally:
            if not thread_started:
                self._exception_running = False
                self._continuous_pause.clear()

    def _run_exception_worker(self, selected_region: CaptureRegion, snapshot: Any | None) -> None:
        try:
            with self._workflow_lock:
                self._run_workflow(selected_region, snapshot)
        finally:
            self._exception_running = False
            self._continuous_pause.clear()

    def _run_worker(self, selected_region: CaptureRegion | None, snapshot: Any | None) -> None:
        try:
            self._run_workflow(selected_region, snapshot)
        finally:
            self._running = False

    def _run_continuous(self, selected_region: CaptureRegion | None) -> None:
        config = self._config_provider()
        interval = max(0.1, float(getattr(config, "realtime_interval_ms", 350)) / 1000.0)
        threshold = max(0.0, min(1.0, float(getattr(config, "realtime_change_threshold", 0.005))))
        previous_signature: bytes | None = None
        try:
            while not self._continuous_stop.is_set():
                if self._continuous_pause.is_set():
                    self._continuous_stop.wait(0.05)
                    continue
                try:
                    with self._workflow_lock:
                        image = self._capture_adapter.capture_image_to_memory(selected_region, None)
                        signature = _frame_signature(image)
                        if previous_signature is None or _frame_changed(previous_signature, signature, threshold):
                            previous_signature = signature
                            self._run_workflow(selected_region, None, captured_image=image, suppress_duplicates=True)
                except Exception as exc:
                    self._callbacks.log(f"[REALTIME] Lỗi vòng quét: {exc}")
                    self._callbacks.error(str(exc))
                self._continuous_stop.wait(interval)
        finally:
            self._continuous_active = False
            self._running = False

    def _run_workflow(
        self,
        selected_region: CaptureRegion | None,
        snapshot: Any | None,
        *,
        captured_image: Any | None = None,
        suppress_duplicates: bool = False,
    ) -> None:
        config = self._config_provider()
        status_callback = self._callbacks.status
        if suppress_duplicates:
            status_callback = lambda message: self._callbacks.log(f"[REALTIME][STATUS] {message}")

        def emit_result(result: dict[str, Any]) -> None:
            if suppress_duplicates:
                signature = _result_signature(result)
                if signature == self._last_result_signature:
                    self._callbacks.log("[REALTIME] Bỏ qua kết quả OCR trùng")
                    return
                self._last_result_signature = signature
            self._callbacks.result(result)

        self._workflow_runner(
            CaptureProcessRequest(
                capture_image=(
                    (lambda: captured_image)
                    if captured_image is not None
                    else lambda: self._capture_adapter.capture_image_to_memory(selected_region, snapshot)
                ),
                ocr_engine=self._ocr_engine_provider(),
                matcher=self._matcher_provider(),
                ocr_backend=getattr(config, "ocr_backend", "auto"),
                debug_dump_input=getattr(config, "ocr_debug_dump_input", False),
                debug_dump_dir=config.image_path.parent if getattr(config, "image_path", None) else Path.cwd(),
            ),
            CaptureProcessCallbacks(
                status=status_callback,
                log=self._callbacks.log,
                error=self._callbacks.error,
                result=emit_result,
            ),
        )


def _frame_signature(image: Any, width: int = 64, height: int = 36) -> bytes:
    try:
        from PIL import Image

        if isinstance(image, tuple) and len(image) == 3:
            raw, image_width, image_height = image
            image = Image.frombytes("RGBA", (int(image_width), int(image_height)), raw, "raw", "BGRA")
        if isinstance(image, Image.Image):
            return image.convert("L").resize((width, height)).tobytes()
    except Exception:
        pass
    return repr(image).encode("utf-8", errors="replace")


def _frame_changed(previous: bytes, current: bytes, threshold: float) -> bool:
    if len(previous) != len(current) or not current:
        return previous != current
    difference = sum(abs(left - right) for left, right in zip(previous, current))
    return (difference / (len(current) * 255.0)) >= threshold


def _result_signature(result: dict[str, Any]) -> tuple[Any, ...]:
    items = result.get("items") or result.get("matches") or []
    if isinstance(items, list) and items:
        return tuple(
            (
                item.get("text_key"),
                item.get("official_cn"),
                item.get("ocr"),
            )
            for item in items
            if isinstance(item, dict)
        )
    return (
        result.get("_matched_key"),
        result.get("_official_cn"),
        result.get("_ocr_text"),
    )


def _capture_region_from_mapping(raw: Any) -> CaptureRegion | None:
    if not isinstance(raw, dict):
        return None
    try:
        region = CaptureRegion(
            left=int(raw["left"]),
            top=int(raw["top"]),
            width=int(raw["width"]),
            height=int(raw["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if region.width <= 0 or region.height <= 0:
        return None
    return region
