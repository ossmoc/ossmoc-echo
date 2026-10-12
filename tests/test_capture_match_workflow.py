from dataclasses import dataclass

from ludiglot.core.capture_match_workflow import (
    CaptureProcessCallbacks,
    CaptureProcessRequest,
    run_capture_match_workflow,
)


@dataclass
class FakeImage:
    width: int
    height: int


@dataclass
class OcrResult:
    boxes: list
    lines: list
    backend: str | None = "paddle"


class FakeEngine:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def recognize_pipeline(self, img, backend=None):
        self.calls.append(backend)
        return self.results.pop(0)


class FakeMatcher:
    def __init__(self, result):
        self.result = result
        self.lines = None

    def match(self, lines):
        self.lines = lines
        return self.result


def callbacks():
    events = {"status": [], "log": [], "error": [], "result": []}
    return events, CaptureProcessCallbacks(
        status=events["status"].append,
        log=events["log"].append,
        error=events["error"].append,
        result=events["result"].append,
    )


def test_workflow_skips_tiny_capture():
    events, cb = callbacks()
    outcome = run_capture_match_workflow(
        CaptureProcessRequest(
            capture_image=lambda: FakeImage(7, 20),
            ocr_engine=FakeEngine([]),
            matcher=FakeMatcher({}),
        ),
        cb,
    )

    assert outcome.status == "tiny_capture"
    assert "选区过小，已取消" in events["status"]


def test_workflow_reports_no_ocr_boxes():
    events, cb = callbacks()
    outcome = run_capture_match_workflow(
        CaptureProcessRequest(
            capture_image=lambda: FakeImage(100, 50),
            ocr_engine=FakeEngine([OcrResult([], [], "paddle")]),
            matcher=FakeMatcher({}),
        ),
        cb,
    )

    assert outcome.status == "no_text"
    assert "OCR 未识别到文本" in events["status"]


def test_workflow_matches_lines_without_boxes():
    events, cb = callbacks()
    matcher = FakeMatcher({"matches": [{"text_key": "A"}]})

    outcome = run_capture_match_workflow(
        CaptureProcessRequest(
            capture_image=lambda: FakeImage(100, 50),
            ocr_engine=FakeEngine([OcrResult([], [("Readable", 0.9)], "windows")]),
            matcher=matcher,
        ),
        cb,
    )

    assert outcome.status == "success"
    assert matcher.lines == [("Readable", 0.9)]


def test_workflow_reports_missing_matcher():
    events, cb = callbacks()
    outcome = run_capture_match_workflow(
        CaptureProcessRequest(
            capture_image=lambda: FakeImage(100, 50),
            ocr_engine=FakeEngine([OcrResult([{"box": 1}], [("Readable", 0.9)], "paddle")]),
            matcher=None,
        ),
        cb,
    )

    assert outcome.status == "matcher_not_ready"
    assert events["error"] == ["匹配服务未就绪"]


def test_workflow_deepcopies_successful_result():
    events, cb = callbacks()
    result = {"matches": [{"text_key": "A"}]}
    outcome = run_capture_match_workflow(
        CaptureProcessRequest(
            capture_image=lambda: FakeImage(100, 50),
            ocr_engine=FakeEngine([OcrResult([{"box": 1}], [("Readable", 0.9)], "paddle")]),
            matcher=FakeMatcher(result),
        ),
        cb,
    )

    assert outcome.status == "success"
    assert events["result"] == [result]
    assert events["result"][0] is not result
    assert events["result"][0]["matches"] is not result["matches"]
    assert events["status"][-1] == "就绪"
