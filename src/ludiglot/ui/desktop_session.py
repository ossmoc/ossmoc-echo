"""Single-worker, generation-safe OCR scheduling for the desktop application.

This module intentionally has no Qt dependency. Callbacks can be bridged through
queued Qt signals; consumers must also reject envelopes from old generations.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable

from ludiglot.core.capture_match_workflow import (
    CaptureProcessCallbacks, CaptureProcessRequest, run_capture_match_workflow,
)
from ludiglot.core.desktop_models import OcrRegion
from ludiglot.core.window_capture import CaptureUnavailable
from ludiglot.ui.capture_session import _frame_changed, _frame_signature


def _ignore(_value: Any) -> None:
    pass


@dataclass(frozen=True)
class SessionCallbacks:
    status: Callable[[str], None] = _ignore
    log: Callable[[str], None] = _ignore
    error: Callable[[str], None] = _ignore
    result: Callable[[dict[str, Any]], None] = _ignore
    preview: Callable[[dict[str, Any]], None] = _ignore
    state_changed: Callable[[dict[str, Any]], None] = _ignore


@dataclass(frozen=True)
class _Job:
    region: OcrRegion
    generation: int
    mode: str
    snapshot: Any = None


class _RecordingEngine:
    """Keep preview OCR even when retrieval returns no matching sentence."""
    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.lines: list[Any] = []

    def recognize_pipeline(self, *args: Any, **kwargs: Any) -> Any:
        result = self.engine.recognize_pipeline(*args, **kwargs)
        self.lines = list(getattr(result, "lines", None) or [])
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.engine, name)


class DesktopCaptureSession:
    def __init__(
        self, *, config_provider: Callable[[], Any],
        ocr_engine_provider: Callable[[], Any], matcher_provider: Callable[[], Any],
        capture_adapter: Any, callbacks: SessionCallbacks,
        thread_starter: Callable[[Callable[[], None]], Any] | None = None,
        workflow_runner: Callable[..., Any] = run_capture_match_workflow,
        auto_processor_provider: Callable[[], Any] | None = None,
        auto_settings_provider: Callable[[], dict] | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._ocr_engine_provider = ocr_engine_provider
        self._matcher_provider = matcher_provider
        self._capture_adapter = capture_adapter
        self._callbacks = callbacks
        self._thread_starter = thread_starter
        self._workflow_runner = workflow_runner
        self._auto_processor_provider = auto_processor_provider
        self._auto_settings_provider = auto_settings_provider
        self._condition = threading.Condition(threading.RLock())
        self._regions: OrderedDict[str, OcrRegion] = OrderedDict()
        # A shared output has only one latest manual intent. Previews are bounded
        # by region count and coalesced, never a stream of background threads.
        self._pending: OrderedDict[tuple[str, str], _Job] = OrderedDict()
        self._signatures: dict[str, bytes] = {}
        self._running = False
        self._pinned = False
        self._manual_pending = False
        self._manual_prior: tuple[bool, bool] | None = None
        self._busy = False
        self._generation = 0
        self._closed = False
        self._worker_started = False
        self._thread: threading.Thread | None = None
        self._cursor = 0
        self._next_realtime_at = 0.0
        self._auto_wakeup = False
        self._auto_poll_ms = None

    @property
    def running(self) -> bool:
        with self._condition:
            return self._running

    @property
    def pinned(self) -> bool:
        with self._condition:
            return self._pinned

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    @property
    def is_busy(self) -> bool:
        with self._condition:
            return self._busy

    def _emit(self, callback: Callable, value: Any) -> None:
        try:
            callback(value)
        except Exception:
            # A failed GUI observer must not kill the only OCR worker.
            pass

    def _state(self) -> None:
        self._emit(self._callbacks.state_changed, {
            "running": self._running, "pinned": self._pinned,
            "manual_pending": self._manual_pending, "generation": self._generation,
        })

    def _invalidate(self) -> None:
        self._generation += 1
        self._pending.clear()
        self._signatures.clear()
        self._next_realtime_at = 0.0
        self._auto_wakeup = False
        self._auto_poll_ms = None
        processor = self._auto_processor_provider() if self._auto_processor_provider else None
        fallback = getattr(processor, 'fallback', None)
        if hasattr(fallback, 'cancel'):
            fallback.cancel()

    def wake_auto(self):
        """One fresh capture after a completed fallback, never replay an old frame."""
        with self._condition:
            if not self._closed and self._running:
                self._auto_wakeup = True
                self._condition.notify_all()

    def _ensure_worker(self) -> None:
        if self._worker_started or self._closed:
            return
        self._worker_started = True
        try:
            if self._thread_starter:
                self._thread_starter(self._worker_loop)
            else:
                self._thread = threading.Thread(target=self._worker_loop, name="Ludiglot-OCR", daemon=True)
                self._thread.start()
        except Exception:
            self._worker_started = False
            raise

    def set_regions(self, regions: list[OcrRegion]) -> None:
        with self._condition:
            if self._closed:
                return
            # UI/screen validation may call this even while a manual translation
            # is held. Invalidate work but preserve a completed hold.
            if self._manual_pending and self._manual_prior:
                self._running, self._pinned = self._manual_prior
            self._manual_pending = False
            self._manual_prior = None
            self._regions = OrderedDict((r.id, r) for r in sorted(regions, key=lambda r: r.order))
            self._cursor = 0
            self._invalidate()
            self._state()
            self._condition.notify_all()

    def start(self) -> bool:
        return self.resume()

    def resume(self) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._invalidate()
            self._manual_pending = False
            self._manual_prior = None
            self._pinned = False
            self._running = any(self._eligible(r, realtime=True) for r in self._regions.values())
            self._ensure_worker()
            self._state()
            self._condition.notify_all()
            return self._running

    def pause(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._invalidate()
            self._running = False
            self._manual_pending = False
            self._manual_prior = None
            # Pausing does not clear a successful manual hold.
            self._state()
            self._condition.notify_all()

    def resume_intent(self) -> None:
        """Keep an already running loop armed even when every region is off."""
        with self._condition:
            if self._closed or self._pinned or self._manual_pending:
                return
            self._running = True
            self._ensure_worker()
            self._state()
            self._condition.notify_all()

    def clear(self) -> None:
        """Discard queued work and a manual hold without starting realtime."""
        with self._condition:
            if self._closed:
                return
            self._invalidate()
            self._pinned = False
            self._manual_pending = False
            self._manual_prior = None
            self._state()
            self._condition.notify_all()

    def request_scan(self, region_id: str, preview: bool = False, snapshot: Any = None) -> bool:
        with self._condition:
            region = self._regions.get(region_id)
            if self._closed or region is None or not self._eligible(region):
                return False
            mode = "preview" if preview else "manual"
            if not preview:
                if not self._manual_pending:
                    self._manual_prior = (self._running, self._pinned)
                self._invalidate()
                self._running = False
                self._manual_pending = True
                # Keep an existing hold visible until the new result succeeds.
            self._pending[(mode, region_id)] = _Job(region, self._generation, mode, snapshot)
            self._ensure_worker()
            self._state()
            self._condition.notify_all()
            return True

    def close(self, timeout: float = 0.5) -> None:
        with self._condition:
            if not self._closed:
                self._closed = True
                self._invalidate()
                self._running = self._pinned = self._manual_pending = False
                self._manual_prior = None
                self._state()
                self._condition.notify_all()
            thread = self._thread
        # OCR APIs are not cancellable; never freeze shutdown waiting for them.
        if thread and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    @staticmethod
    def _eligible(region: OcrRegion, *, realtime: bool = False) -> bool:
        return bool(region.enabled and region.capture_region is not None and (not realtime or region.mode == "realtime"))

    def _valid(self, job: _Job) -> bool:
        region = self._regions.get(job.region.id)
        return bool(not self._closed and job.generation == self._generation and region and
                    region.revision == job.region.revision and self._eligible(region) and
                    (job.mode != "realtime" or self._running))

    def _interval(self) -> float:
        try:
            seconds = float(getattr(self._config_provider(), "realtime_interval_ms", 350)) / 1000
            if self._auto_poll_ms is not None:
                seconds = min(seconds, self._auto_poll_ms / 1000)
            return max(0.05, min(60.0, seconds)) if math.isfinite(seconds) else 0.35
        except Exception:
            return 0.35

    def _next_job(self) -> _Job | None:
        while not self._closed:
            if self._pending:
                key = next((key for key in self._pending if key[0] == "manual"), next(iter(self._pending)))
                job = self._pending.pop(key)
                if self._valid(job):
                    return job
                continue
            active = [r for r in self._regions.values() if self._eligible(r, realtime=True)]
            if self._running and active:
                remaining = self._next_realtime_at - time.monotonic()
                if self._auto_wakeup and any(r.id == '__auto_wuwa__' for r in active):
                    self._auto_wakeup = False
                    remaining = 0
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                region = active[self._cursor % len(active)]
                self._cursor = (self._cursor + 1) % len(active)
                return _Job(region, self._generation, "realtime")
            self._condition.wait()
        return None

    def _worker_loop(self) -> None:
        try:
            while True:
                with self._condition:
                    job = self._next_job()
                    if job is None:
                        return
                    self._busy = True
                    started = time.monotonic()
                try:
                    self._process(job)
                except CaptureUnavailable as exc:
                    # Minimized/closed/no-frame is waiting, not an OCR failure.
                    self._finish(job, None, None, "", "capture_unavailable", str(exc))
                except Exception as exc:
                    # Config/provider failures should not strand a manual hold.
                    self._finish(job, None, None, "", "error", str(exc))
                finally:
                    with self._condition:
                        self._busy = False
                        if job.mode == "realtime" and job.generation == self._generation:
                            # Start-to-start cadence, not OCR time plus a full
                            # extra delay. Never enqueue missed/catch-up ticks.
                            self._next_realtime_at = started + self._interval()
                        self._condition.notify_all()
        finally:
            with self._condition:
                self._worker_started = False
                self._busy = False

    def _process(self, job: _Job) -> None:
        started = time.perf_counter()
        config = self._config_provider()
        capture = getattr(self._capture_adapter, "capture_auto_frame", None) if job.region.id == "__auto_wuwa__" else None
        image = (capture or self._capture_adapter.capture_image_to_memory)(job.region.capture_region, job.snapshot)
        if job.region.id == "__auto_wuwa__":
            captured = time.perf_counter()
            with self._condition:
                if not self._valid(job):
                    return
            processor = self._auto_processor_provider() if self._auto_processor_provider else None
            if processor is None:
                raise RuntimeError("Chỉ mục Auto chưa sẵn sàng.")
            settings = dict(self._auto_settings_provider() if self._auto_settings_provider else {})
            settings['generation'] = (settings.get('generation'), job.generation)
            result = processor.process(image, self._ocr_engine_provider(), settings,
                                       getattr(config, "ocr_backend", "windows"), force=job.mode == "preview")
            self._auto_poll_ms = getattr(processor, 'poll_interval_ms', None)
            timing = {**getattr(processor, 'last_timings', {}), 'capture_ms':1000*(captured-started),
                      'worker_ms':1000*(time.perf_counter()-started), 'queued_at':time.perf_counter()}
            self._finish(job, {"auto_frame": result, "capture_bounds": getattr(image, "info", {}).get("capture_bounds"), 'timings':timing}, image if job.mode == "preview" else None,
                         "\n".join(result.diagnostics), "success")
            return
        with self._condition:
            if not self._valid(job):
                return
            signature = _frame_signature(image)
            previous = self._signatures.get(job.region.id)
            threshold = max(0.0, min(1.0, float(getattr(config, "realtime_change_threshold", 0.005))))
            if job.mode == "realtime" and previous is not None and (previous == signature or not _frame_changed(previous, signature, threshold)):
                return
        engine = _RecordingEngine(self._ocr_engine_provider())
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        self._emit(self._callbacks.status, f"Đang quét {job.region.name}…")
        outcome = self._workflow_runner(
            CaptureProcessRequest(
                capture_image=lambda: image, ocr_engine=engine, matcher=self._matcher_provider(),
                ocr_backend=getattr(config, "ocr_backend", "windows"),
                # Desktop sessions never silently save screenshots.
                debug_dump_input=False,
                debug_dump_dir=Path(getattr(config, "image_path", None) or ".").parent,
            ),
            CaptureProcessCallbacks(status=lambda msg: self._emit(self._callbacks.log, f"[OCR][STATUS] {msg}"),
                log=lambda msg: self._emit(self._callbacks.log, msg),
                error=errors.append, result=results.append),
        )
        result = results[-1] if results else getattr(outcome, "result", None)
        status = getattr(outcome, "status", "success" if result is not None else "no_match")
        ocr = "\n".join(str(line[0] if isinstance(line, (tuple, list)) else line) for line in engine.lines)
        if not ocr and isinstance(result, dict):
            items = result.get("matches") or result.get("items") or []
            ocr = str(result.get("_ocr_text") or "\n".join(str(item.get("ocr", "")) for item in items if isinstance(item, dict)))
        self._finish(job, result, image, ocr, status, "\n".join(errors), signature)

    def _finish(self, job: _Job, result: dict | None, image: Any, ocr: str, status: str,
                error: str = "", signature: bytes | None = None) -> None:
        with self._condition:
            if not self._valid(job):
                return
            envelope = {"region_id": job.region.id, "revision": job.region.revision,
                        "generation": job.generation, "mode": job.mode,
                        "result": result, "image": image, "ocr": ocr, "status": status, "error": error}
            if error:
                self._emit(self._callbacks.status if status == "capture_unavailable" else self._callbacks.error, error)
            success = isinstance(result, dict) and status == "success" and not error
            if job.mode == "preview":
                self._emit(self._callbacks.preview, envelope)
            elif job.mode == "manual":
                self._manual_pending = False
                if success:
                    self._pinned = True
                    self._running = False
                    self._emit(self._callbacks.result, envelope)
                else:
                    self._running, self._pinned = self._manual_prior or (False, False)
                    self._emit(self._callbacks.status, "Quét không thành công; giữ bản dịch trước đó.")
                self._manual_prior = None
                self._state()
                self._condition.notify_all()
            elif success:
                self._emit(self._callbacks.result, envelope)
            elif job.region.id == "__auto_wuwa__" and error:
                # Let the GUI stop opaque masks after repeated OCR failures.
                self._emit(self._callbacks.result, envelope)
            if self._valid(job) and job.mode == "realtime" and signature is not None and (success or status in {"no_text", "no_match", "tiny_capture"}):
                self._signatures[job.region.id] = signature
