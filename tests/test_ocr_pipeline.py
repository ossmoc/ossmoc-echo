from __future__ import annotations

from ludiglot.core.ocr import OCREngine, OcrPipelineResult


class FakeOCREngine(OCREngine):
    def recognize_with_boxes(self, image_input, backend=None):
        self.last_backend = backend or "windows"
        return [
            {
                "text": "Hello",
                "conf": 0.9,
                "box": [[0, 0], [40, 0], [40, 20], [0, 20]],
            },
            {
                "text": "Rover",
                "conf": 0.8,
                "box": [[45, 0], [95, 0], [95, 20], [45, 20]],
            },
            {
                "text": "Next line",
                "conf": 0.7,
                "box": [[0, 40], [90, 40], [90, 60], [0, 60]],
            },
        ]


def test_recognize_pipeline_returns_boxes_lines_and_backend() -> None:
    engine = FakeOCREngine(lang="en")

    result = engine.recognize_pipeline("fake.png", backend="windows")

    assert isinstance(result, OcrPipelineResult)
    assert result.backend == "windows"
    assert [text for text, _ in result.lines] == ["Hello Rover", "Next line"]
    assert result.boxes[0]["text"] == "Hello"


def test_recognize_with_confidence_uses_pipeline_lines() -> None:
    engine = FakeOCREngine(lang="en")

    assert engine.recognize_with_confidence("fake.png", backend="windows") == [
        ("Hello Rover", 0.8500000000000001),
        ("Next line", 0.7),
    ]
