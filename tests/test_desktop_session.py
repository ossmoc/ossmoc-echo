from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import pytest

from ludiglot.core.capture_match_workflow import CaptureProcessOutcome
from ludiglot.core.desktop_models import OcrRegion
from ludiglot.ui.desktop_session import DesktopCaptureSession, SessionCallbacks


def region(name="dialogue", *, mode="realtime", enabled=True, order=0):
    return OcrRegion(name, name, mode, enabled,
        bounds={"left": order * 100, "top": 0, "width": 80, "height": 40}, order=order)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for OCR worker"


class Adapter:
    def __init__(self):
        self.calls = []
        self.image = "same-image"
        self.error = None

    def capture_image_to_memory(self, bounds, snapshot=None):
        self.calls.append((bounds, snapshot))
        if self.error:
            raise self.error
        return self.image


class DeferredStarter:
    def __init__(self):
        self.targets = []
        self.thread = None

    def __call__(self, target):
        self.targets.append(target)

    def run(self):
        self.thread = threading.Thread(target=self.targets.pop(0), daemon=True)
        self.thread.start()


@pytest.fixture
def make_session():
    sessions = []
    starters = []

    def make(*, workflow=None, starter=None, adapter=None, result_callback=None, engine=None, matcher=None):
        adapter = adapter or Adapter()
        events = SimpleNamespace(results=[], previews=[], states=[], errors=[], statuses=[], logs=[])
        if workflow is None:
            def workflow(request, callbacks):
                request.capture_image()
                callbacks.result({"_official_cn": "Bản dịch", "_ocr_text": "English"})
                return CaptureProcessOutcome("success")
        def on_result(payload):
            events.results.append(payload)
            if result_callback:
                result_callback(payload)
        session = DesktopCaptureSession(config_provider=lambda: SimpleNamespace(
            realtime_interval_ms=50, realtime_change_threshold=0.005, ocr_backend="windows"),
            ocr_engine_provider=lambda: engine, matcher_provider=lambda: matcher,
            capture_adapter=adapter, callbacks=SessionCallbacks(
                status=events.statuses.append, log=events.logs.append, error=events.errors.append,
                result=on_result, preview=events.previews.append, state_changed=events.states.append),
            thread_starter=starter, workflow_runner=workflow)
        sessions.append(session)
        if starter:
            starters.append(starter)
        return session, adapter, events

    yield make
    for session in sessions:
        session.close()
    for starter in starters:
        if starter.thread:
            starter.thread.join(1)


def test_disabled_missing_and_tiny_regions_do_not_run(make_session):
    starter = DeferredStarter()
    session, _, events = make_session(starter=starter)
    session.set_regions([region(enabled=False), replace(region("empty"), bounds=None),
                         replace(region("tiny"), bounds={"left": 0, "top": 0, "width": 4, "height": 4})])
    assert session.start() is False
    assert session.request_scan("missing") is False
    assert session.request_scan("dialogue") is False
    assert session.request_scan("tiny", preview=True) is False
    assert events.results == []
    assert session.running is False


def test_only_one_worker_and_latest_manual_request_is_bounded(make_session):
    starter = DeferredStarter()
    session, _, events = make_session(starter=starter)
    session.set_regions([region(), region("special", mode="manual", order=1)])
    session.start()
    for _ in range(50):
        assert session.request_scan("special")
    assert len(starter.targets) == 1
    assert len(session._pending) == 1
    starter.run()
    wait_for(lambda: session.pinned)
    assert len(events.results) == 1
    assert events.results[0]["region_id"] == "special"
    assert events.results[0]["mode"] == "manual"
    assert session.running is False


def test_round_robin_respects_order_and_serializes_engine(make_session):
    active = 0
    maximum = 0
    count = 0
    holder = {}
    def workflow(request, callbacks):
        nonlocal active, maximum, count
        active += 1
        maximum = max(maximum, active)
        request.capture_image()
        count += 1
        callbacks.result({"_official_cn": str(count)})
        active -= 1
        return CaptureProcessOutcome("success")
    def received(_payload):
        if len(holder["events"].results) == 4:
            holder["session"].pause()
    session, adapter, events = make_session(workflow=workflow, result_callback=received)
    holder.update(session=session, events=events)
    # Different image signatures force each region through the workflow.
    base_capture = adapter.capture_image_to_memory
    adapter.capture_image_to_memory = lambda bounds, snapshot=None: (base_capture(bounds, snapshot), str(len(adapter.calls)) * 100)[1]
    session.set_regions([region("second", order=1), region("disabled", enabled=False, order=2), region("first", order=0)])
    session.start()
    wait_for(lambda: len(events.results) == 4)
    assert [p["region_id"] for p in events.results] == ["first", "second", "first", "second"]
    assert maximum == 1


def test_identical_frames_skip_ocr_and_resume_forces_sweep(make_session):
    session, adapter, events = make_session()
    session.set_regions([region()])
    session.start()
    wait_for(lambda: len(adapter.calls) >= 3)
    assert len(events.results) == 1
    session.pause()
    session.resume()
    wait_for(lambda: len(events.results) == 2)
    assert events.results[0]["generation"] != events.results[1]["generation"]


def test_pause_discards_in_flight_result_and_no_followup_work(make_session):
    entered = threading.Event()
    release = threading.Event()
    def workflow(request, callbacks):
        entered.set()
        assert release.wait(3)
        callbacks.result({"_official_cn": "stale"})
        return CaptureProcessOutcome("success")
    session, adapter, events = make_session(workflow=workflow)
    session.set_regions([region()])
    session.start()
    assert entered.wait(3)
    generation = session.generation
    session.pause()
    release.set()
    wait_for(lambda: not session.is_busy)
    assert session.generation > generation
    assert events.results == []
    assert len(adapter.calls) == 1


def test_region_revision_change_discards_in_flight_result(make_session):
    entered = threading.Event()
    release = threading.Event()
    def workflow(request, callbacks):
        entered.set()
        assert release.wait(3)
        callbacks.result({"_official_cn": "old bounds"})
        return CaptureProcessOutcome("success")
    session, _, events = make_session(workflow=workflow)
    old = region()
    session.set_regions([old])
    session.start()
    assert entered.wait(3)
    session.set_regions([replace(old, revision=1, enabled=False)])
    release.set()
    wait_for(lambda: not session.is_busy)
    assert events.results == []


def test_manual_has_priority_after_current_realtime_and_holds_until_resume(make_session):
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    def workflow(request, callbacks):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(3)
        callbacks.result({"_official_cn": str(calls)})
        return CaptureProcessOutcome("success")
    session, adapter, events = make_session(workflow=workflow)
    session.set_regions([region(), region("special", mode="manual", order=1)])
    session.start()
    assert entered.wait(3)
    assert session.request_scan("special")
    release.set()
    wait_for(lambda: session.pinned)
    assert calls == 2
    assert [p["region_id"] for p in events.results] == ["special"]
    assert adapter.calls[-1][0].left == 100
    capture_count = len(adapter.calls)
    time.sleep(0.12)
    assert len(adapter.calls) == capture_count
    session.resume()
    wait_for(lambda: any(p["mode"] == "realtime" for p in events.results))
    assert session.pinned is False
    assert session.running is True


def test_superseding_manual_failure_restores_original_running_intent(make_session):
    starter = DeferredStarter()
    def workflow(request, callbacks):
        if request.capture_image() == "fail":
            return CaptureProcessOutcome("no_match")
        callbacks.result({"_official_cn": "realtime"})
        return CaptureProcessOutcome("success")
    session, adapter, events = make_session(starter=starter, workflow=workflow)
    session.set_regions([region(), region("one", mode="manual", order=1), region("two", mode="manual", order=2)])
    base_capture = adapter.capture_image_to_memory
    adapter.capture_image_to_memory = lambda bounds, snapshot=None: (base_capture(bounds, snapshot), "fail" if bounds.left else "ok")[1]
    session.start()
    session.request_scan("one")
    session.request_scan("two")
    starter.run()
    wait_for(lambda: events.results)
    assert session.running is True
    assert session.pinned is False
    assert [bounds.left for bounds, _ in adapter.calls[:2]] == [200, 0]
    assert events.results[0]["mode"] == "realtime"
    assert any(not p["manual_pending"] and p["running"] for p in events.states)


def test_manual_capture_exception_restores_prior_paused_state(make_session):
    adapter = Adapter()
    adapter.error = RuntimeError("capture unavailable")
    session, _, events = make_session(adapter=adapter)
    session.set_regions([region("special", mode="manual")])
    session.request_scan("special")
    wait_for(lambda: events.errors)
    wait_for(lambda: not session.is_busy)
    assert events.results == []
    assert session.running is False
    assert session.pinned is False
    assert events.states[-1]["manual_pending"] is False


def test_preview_does_not_change_generation_or_successful_manual_hold(make_session):
    session, adapter, events = make_session()
    session.set_regions([region("special", mode="manual")])
    session.request_scan("special")
    wait_for(lambda: session.pinned)
    generation = session.generation
    snapshot = object()
    session.request_scan("special", preview=True, snapshot=snapshot)
    wait_for(lambda: events.previews)
    assert session.generation == generation
    assert session.pinned is True
    assert session.running is False
    assert len(events.results) == 1
    assert events.previews[0]["image"] == adapter.image
    assert events.previews[0]["ocr"] == "English"
    assert events.previews[0]["mode"] == "preview"
    assert adapter.calls[-1][1] is snapshot


def test_preview_coalesces_per_region_and_never_updates_translation(make_session):
    starter = DeferredStarter()
    session, adapter, events = make_session(starter=starter)
    session.set_regions([region("one"), region("two", order=1)])
    for _ in range(10):
        session.request_scan("one", preview=True)
        session.request_scan("two", preview=True)
    assert len(session._pending) == 2
    starter.run()
    wait_for(lambda: len(events.previews) == 2)
    assert len(adapter.calls) == 2
    assert events.results == []
    assert session.running is False
    assert session.pinned is False


def test_set_regions_and_pause_preserve_completed_hold(make_session):
    session, _, _ = make_session()
    special = region("special", mode="manual")
    session.set_regions([special])
    session.request_scan("special")
    wait_for(lambda: session.pinned)
    session.pause()
    session.set_regions([replace(special, revision=1)])
    assert session.pinned is True
    assert session.running is False


def test_close_is_bounded_and_late_result_is_discarded(make_session):
    entered = threading.Event()
    release = threading.Event()
    def workflow(request, callbacks):
        entered.set()
        assert release.wait(3)
        callbacks.result({"_official_cn": "late"})
        return CaptureProcessOutcome("success")
    session, _, events = make_session(workflow=workflow)
    session.set_regions([region()])
    session.start()
    assert entered.wait(3)
    before = time.monotonic()
    session.close(timeout=0.02)
    assert time.monotonic() - before < 0.5
    assert session.request_scan("dialogue") is False
    assert session.resume() is False
    release.set()
    wait_for(lambda: not session.is_busy)
    assert events.results == []


def test_preview_keeps_ocr_for_no_match_with_real_workflow(make_session):
    from ludiglot.core.capture_match_workflow import run_capture_match_workflow
    engine = SimpleNamespace(recognize_pipeline=lambda image, backend: SimpleNamespace(
        boxes=[], lines=[("A sentence without a match", 0.9)], backend="windows"))
    matcher = SimpleNamespace(match=lambda lines: None)
    session, _, events = make_session(workflow=run_capture_match_workflow, engine=engine, matcher=matcher)
    session.set_regions([region("special", mode="manual")])
    session.request_scan("special", preview=True)
    wait_for(lambda: events.previews)
    assert events.previews[0]["result"] is None
    assert events.previews[0]["ocr"] == "A sentence without a match"
    assert events.previews[0]["status"] == "no_match"
    assert events.results == []


def test_observer_exception_does_not_kill_worker(make_session):
    def failing_observer(_payload):
        raise RuntimeError("GUI slot failed")
    session, _, events = make_session(result_callback=failing_observer)
    session.set_regions([region("special", mode="manual")])
    session.request_scan("special")
    wait_for(lambda: session.pinned)
    session.request_scan("special", preview=True)
    wait_for(lambda: events.previews)
    assert events.results


def test_clear_unpins_invalidates_pending_and_preserves_running_flag(make_session):
    session, _, events = make_session()
    session.set_regions([region(), region("special", mode="manual", order=1)])
    session.request_scan("special")
    wait_for(lambda: session.pinned)
    generation = session.generation
    session.clear()
    assert session.generation > generation
    assert session.pinned is False
    assert session.running is False
    assert events.states[-1]["manual_pending"] is False
    session.start()
    wait_for(lambda: len(events.results) == 2)
    session.clear()
    assert session.running is True


def test_clear_discards_in_flight_manual_result(make_session):
    entered = threading.Event()
    release = threading.Event()
    def workflow(request, callbacks):
        entered.set()
        assert release.wait(3)
        callbacks.result({"_official_cn": "late manual"})
        return CaptureProcessOutcome("success")
    session, _, events = make_session(workflow=workflow)
    session.set_regions([region("special", mode="manual")])
    session.request_scan("special")
    assert entered.wait(3)
    session.clear()
    release.set()
    wait_for(lambda: not session.is_busy)
    assert events.results == []
    assert session.pinned is False
    assert session.running is False


def test_window_temporarily_unavailable_retries_without_toggling_run_state(make_session):
    from ludiglot.core.window_capture import CaptureUnavailable
    adapter = Adapter()
    adapter.error = CaptureUnavailable("game minimized")
    session, _, events = make_session(adapter=adapter)
    session.set_regions([region()])
    session.start()
    wait_for(lambda: len(adapter.calls) >= 3)
    assert session.running and not session.pinned
    assert not events.errors
    assert any("minimized" in msg for msg in events.statuses)
    adapter.error = None
    wait_for(lambda: events.results)
    assert session.running


def test_manual_capture_unavailable_restores_the_previous_realtime_loop(make_session):
    from ludiglot.core.window_capture import CaptureUnavailable
    adapter = Adapter()
    session, _, events = make_session(adapter=adapter)
    session.set_regions([region(), region("special", mode="manual")])
    session.start()
    wait_for(lambda: events.results)
    adapter.error = CaptureUnavailable("game closed")
    session.request_scan("special")
    wait_for(lambda: not events.states[-1]["manual_pending"])
    assert session.running and not session.pinned
    assert not events.errors


@pytest.mark.parametrize('duration', [.01,.8])
def test_realtime_deadline_is_from_start_without_catch_up_jobs(make_session, monkeypatch, duration):
    from ludiglot.ui import desktop_session as module
    clock=[100.0]; monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    session,_,_=make_session(starter=DeferredStarter())
    job=module._Job(region(),session.generation,'realtime')
    calls=[0]
    def next_job():
        calls[0]+=1
        if calls[0]==1:
            return job
        assert session._next_realtime_at==100.05
        assert not session._pending
        return None
    monkeypatch.setattr(session,'_next_job',next_job)
    monkeypatch.setattr(session,'_process',lambda _:clock.__setitem__(0,100+duration))
    session._worker_loop()


def test_stale_worker_completion_does_not_overwrite_new_generation_deadline(make_session,monkeypatch):
    from ludiglot.ui import desktop_session as module
    session,_,_=make_session(starter=DeferredStarter())
    jobs=iter([module._Job(region(),session.generation,'realtime'),None])
    monkeypatch.setattr(session,'_next_job',lambda:next(jobs))
    monkeypatch.setattr(session,'_process',lambda _:session._invalidate())
    session._worker_loop()
    assert session._next_realtime_at==0 and not session._pending


def test_auto_wakeup_fresh_capture_and_adaptive_cadence_are_generation_safe(monkeypatch):
    from ludiglot.ui import desktop_session as module
    from ludiglot.core.auto_wuwa import AutoFrameResult
    from PIL import Image
    adapter = Adapter()
    adapter.image = Image.new('RGB', (100, 100))
    settings = {'generation': 42}
    received = []
    cancellations = []
    class Processor:
        fallback = SimpleNamespace(cancel=lambda: cancellations.append(True))
        poll_interval_ms = 100
        def process(self, image, engine, options, backend, **kwargs):
            received.append(options)
            return AutoFrameResult()
    processor = Processor()
    session = DesktopCaptureSession(
        config_provider=lambda: SimpleNamespace(realtime_interval_ms=350),
        ocr_engine_provider=lambda: None, matcher_provider=lambda: None,
        capture_adapter=adapter, callbacks=SessionCallbacks(), thread_starter=lambda _: None,
        auto_processor_provider=lambda: processor, auto_settings_provider=lambda: settings)
    try:
        assert session._interval() == .35
        session.set_regions([region('__auto_wuwa__')])
        session.start()
        with session._condition:
            job = session._next_job()
        session._process(job)
        assert session._interval() == .1
        assert received == [{'generation': (42, job.generation)}]
        assert settings == {'generation': 42}
        session._next_realtime_at = time.monotonic() + 60
        session.wake_auto()
        with session._condition:
            fresh = session._next_job()
        session._process(fresh)
        assert len(adapter.calls) == 2  # never publishes the old image directly
        assert not session._auto_wakeup
        session.pause()
        session.wake_auto()
        assert not session._auto_wakeup
        assert session._interval() == .35 and cancellations
    finally:
        session.close()
