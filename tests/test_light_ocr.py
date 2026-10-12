from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image

from ludiglot.core.light_ocr import LightDialogueOcr


def test_missing_models_disable_fallback_without_network_or_repeated_errors(tmp_path):
    messages=[]; engine=LightDialogueOcr(tmp_path,messages.append)
    assert engine.recognize(Image.new('RGB',(100,30)))==[]
    assert engine.failed and engine.engine is None and len(messages)==1
    assert engine.recognize(Image.new('RGB',(100,30)))==[] and len(messages)==1


def test_prepare_runtime_is_optional_idempotent_and_does_not_run_inference(tmp_path):
    engine = LightDialogueOcr(tmp_path)
    assert not engine.prepare_runtime() and engine.failed
    calls = []
    engine.engine = lambda image: calls.append(image)
    assert engine.prepare_runtime() and calls == []


def test_adapter_preserves_actual_polygon_and_handles_empty_result():
    engine=LightDialogueOcr(Path('unused'))
    points=np.array([[[10,4],[90,4],[90,25],[10,25]]],dtype=float)
    engine.engine=lambda image:SimpleNamespace(txts=('Recognized text',),boxes=points)
    assert engine.recognize(Image.new('RGB',(100,30)))==[{'text':'Recognized text','box':points[0].tolist()}]
    engine.engine=lambda image:SimpleNamespace(txts=None,boxes=None)
    assert engine.recognize(Image.new('RGB',(100,30)))==[]


def test_broken_native_runtime_is_disabled_once():
    engine=LightDialogueOcr(Path('unused'))
    def broken(image):
        raise RuntimeError('native failure')
    engine.engine=broken
    assert engine.recognize(Image.new('RGB',(100,30)))==[]
    assert engine.failed and engine.engine is None
