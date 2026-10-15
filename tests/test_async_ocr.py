"""No native OCR required: bounded work, stale rejection and helper trust."""
import threading
from pathlib import Path

from PIL import Image
import pytest

from ludiglot.core.async_ocr import LatestOnlyOcr
from ludiglot.core.one_ocr import PreferredDialogueOcr
from ludiglot.core import one_ocr


class BlockedBackend:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.closed = False

    def recognize(self, image):
        self.calls.append(image.getpixel((0, 0)))
        self.entered.set()
        assert self.release.wait(3), 'test backend timed out'
        return [{'text': str(self.calls[-1])}]

    def close(self):
        self.closed = True
        self.release.set()


def image(value):
    return Image.new('L', (8, 8), value)


def test_latest_only_nonblocking_coalesces_and_copies_input():
    backend = BlockedBackend()
    service = LatestOnlyOcr(backend)
    ready = threading.Event()
    service.set_wakeup(ready.set)
    first = image(1)
    try:
        assert service.poll_or_submit(first, 'first') is None
        assert backend.entered.wait(1)
        first.putpixel((0, 0), 99)
        for value in range(2, 30):
            assert service.poll_or_submit(image(value), value) is None
        assert len(backend.calls) == 1
        assert service.pending
        worker = service._thread
        assert service.poll_or_submit(image(29), 29) is None
        backend.release.set()
        assert ready.wait(2)
        assert service.poll_or_submit(image(29), 29) == [{'text': '29'}]
        assert backend.calls == [1, 29]
        assert service._thread is worker
        assert not service.pending
    finally:
        service.close()
    assert backend.closed
    assert service.poll_or_submit(image(5), 'closed') == []


def test_cancel_rejects_old_result_even_if_token_reused():
    backend = BlockedBackend()
    service = LatestOnlyOcr(backend)
    ready = threading.Event()
    service.set_wakeup(ready.set)
    try:
        service.poll_or_submit(image(1), 'same-token')
        assert backend.entered.wait(1)
        service.cancel()
        assert service.poll_or_submit(image(2), 'same-token') is None
        backend.release.set()
        assert ready.wait(2)
        assert backend.calls == [1, 2]
        assert service.poll_or_submit(image(2), 'same-token') == [{'text': '2'}]
    finally:
        service.close()


def test_backend_and_observer_errors_do_not_kill_worker():
    class Failing:
        def recognize(self, _):
            raise RuntimeError('bad native input')
    service = LatestOnlyOcr(Failing())
    completed = threading.Event()
    def wakeup():
        completed.set()
        raise RuntimeError('observer failed')
    service.set_wakeup(wakeup)
    try:
        service.poll_or_submit(image(1), 1)
        assert completed.wait(2)
        assert service.poll_or_submit(image(1), 1) == []
        completed.clear()
        service.poll_or_submit(image(2), 2)
        assert completed.wait(2)
    finally:
        service.close()


def test_preferred_falls_back_once_and_closes_without_recognize_lock(tmp_path, monkeypatch):
    from ludiglot.core import light_ocr
    class Light:
        def __init__(self, *_):
            self.calls = 0
        def recognize(self, _):
            self.calls += 1
            return ['light']
    class BrokenOne:
        closed = 0
        def recognize(self, _):
            raise RuntimeError('helper crashed')
        def close(self):
            self.closed += 1
    monkeypatch.setattr(light_ocr, 'LightDialogueOcr', Light)
    logs = []
    service = PreferredDialogueOcr(tmp_path, tmp_path, logs.append)
    one = service.one = BrokenOne()
    assert service.recognize(image(1)) == ['light']
    assert service.recognize(image(2)) == ['light']
    assert one.closed == 1 and len(logs) == 1
    with service._lock:
        service.close()
    assert service.recognize(image(3)) == []
    assert service.light.calls == 2


def test_untrusted_runtime_and_bad_receipt_rejected(tmp_path):
    assert not one_ocr._trusted_runtime(tmp_path)
    receipt = tmp_path / 'installed-source.json'
    receipt.write_text('{bad json', encoding='utf-8')
    assert not one_ocr._trusted_runtime(tmp_path)
    receipt.write_text('{"source":"C:/Temp/untrusted", "hashes":{}}', encoding='utf-8')
    assert not one_ocr._trusted_runtime(tmp_path)


def test_staging_rechecks_bytes_and_rejects_tampered_model(tmp_path, monkeypatch):
    source = tmp_path / 'installed' / 'SnippingTool'
    source.mkdir(parents=True)
    names = ('oneocr.dll', 'onnxruntime.dll', 'oneocr.onemodel')
    for name in names:
        (source / name).write_bytes(name.encode())
    monkeypatch.setattr(one_ocr, 'discover_oneocr', lambda: source)
    monkeypatch.setattr(one_ocr, '_installed_directory', lambda path: path == source)
    runtime = one_ocr.prepare_local_runtime(tmp_path / 'private-cache')
    assert one_ocr._trusted_runtime(runtime)
    (runtime / 'oneocr.onemodel').write_bytes(b'not the installed model')
    assert not one_ocr._trusted_runtime(runtime)
    assert one_ocr.prepare_local_runtime(tmp_path / 'private-cache') == runtime
    assert one_ocr._trusted_runtime(runtime)
    (source / 'oneocr.onemodel').write_bytes(b'updated installed package')
    assert not one_ocr._trusted_runtime(runtime)


def test_absent_package_does_not_create_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(one_ocr, 'discover_oneocr', lambda: None)
    cache = tmp_path / 'cache'
    assert one_ocr.prepare_local_runtime(cache) is None
    assert not cache.exists()


@pytest.mark.parametrize('width,height,raw', [(0, 1, b''), (5000, 1, b''), (1, 1, b'x'), (4096, 4096, b'')])
def test_native_rejects_malformed_buffers_before_call(width, height, raw):
    # No Microsoft DLL is loaded by this test.
    native = object.__new__(one_ocr.NativeOneOcr)
    with pytest.raises(ValueError):
        native.recognize_bgra(raw, width, height)


def test_helper_failure_closes_process(tmp_path, monkeypatch):
    service = one_ocr.OneOcrProcess(tmp_path)
    closed = []
    monkeypatch.setattr(service, '_start', lambda: (_ for _ in ()).throw(RuntimeError('no helper')))
    monkeypatch.setattr(service, 'close', lambda: closed.append(True))
    assert not service.prepare_runtime()
    with pytest.raises(RuntimeError):
        service.recognize(image(1))
    assert len(closed) == 2


def test_closed_helper_cannot_respawn_during_shutdown(tmp_path, monkeypatch):
    service = one_ocr.OneOcrProcess(tmp_path)
    monkeypatch.setattr(one_ocr.subprocess, 'Popen', lambda *_a, **_k:pytest.fail('closed helper respawned'))
    service.close()
    assert not service.prepare_runtime()
    with pytest.raises(RuntimeError, match='closed'):
        service.recognize(image(1))


def test_cpu_budget_targets_only_current_helper(monkeypatch):
    from types import SimpleNamespace
    changes = []
    def current():
        return 123
    def get_mask(handle, allowed, system):
        assert handle == 123
        allowed._obj.value = 0b11111111
        return True
    def set_mask(handle, mask):
        changes.append((handle, mask))
    monkeypatch.setattr(one_ocr.C, 'WinDLL', lambda *_a, **_k: SimpleNamespace(
        GetCurrentProcess=current, GetProcessAffinityMask=get_mask, SetProcessAffinityMask=set_mask))
    one_ocr.limit_worker_cpu()
    assert changes == [(123, 0b11000000)]


@pytest.mark.parametrize('budget', [-1, 65, 1.5, '2'])
def test_invalid_cpu_budget_never_starts_helper(tmp_path, budget):
    with pytest.raises(ValueError):
        one_ocr.OneOcrProcess(tmp_path, cpu_budget=budget)


def test_installed_one_is_preferred_without_affinity_and_closes_cleanly(tmp_path, monkeypatch):
    from ludiglot.core import light_ocr
    sequence = []
    class Light:
        def __init__(self, *_):
            pass
        def prepare_runtime(self):
            sequence.append('prepare light before WinRT')
            return True
        def recognize(self, _):
            pytest.fail('Available OneOCR must be used first')
    class One:
        def __init__(self, directory, *, cpu_budget):
            assert directory == tmp_path and cpu_budget == 0
            sequence.append('create isolated one')
        def prepare_runtime(self):
            return True
        def recognize(self, _):
            return ['one']
        def close(self):
            sequence.append('closed')
    monkeypatch.setattr(light_ocr, 'LightDialogueOcr', Light)
    monkeypatch.setattr(one_ocr, 'prepare_local_runtime', lambda _:tmp_path)
    monkeypatch.setattr(one_ocr, 'OneOcrProcess', One)
    service = PreferredDialogueOcr(tmp_path, tmp_path)
    assert service.prepare_runtime()
    assert sequence[:2] == ['prepare light before WinRT','create isolated one']
    assert service.recognize(image(1)) == ['one']
    service.close()
    assert sequence[-1] == 'closed'
    assert service.recognize(image(1)) == []


def test_absent_installed_package_uses_shipped_offline_model(tmp_path, monkeypatch):
    from ludiglot.core import light_ocr
    class Light:
        def __init__(self, *_):
            pass
        def prepare_runtime(self):
            return True
        def recognize(self, _):
            return ['compatible backup']
    monkeypatch.setattr(light_ocr, 'LightDialogueOcr', Light)
    monkeypatch.setattr(one_ocr, 'prepare_local_runtime', lambda _:None)
    monkeypatch.setattr(one_ocr, 'OneOcrProcess', lambda *_a, **_k:pytest.fail('No package: no helper'))
    service = PreferredDialogueOcr(tmp_path, tmp_path)
    assert service.prepare_runtime() and service.one is None
    assert service.recognize(image(1)) == ['compatible backup']


def test_failed_worker_startup_keeps_shipped_model(tmp_path, monkeypatch):
    from ludiglot.core import light_ocr
    class Light:
        def __init__(self, *_):
            pass
        def prepare_runtime(self):
            return True
        def recognize(self, _):
            return ['compatible backup']
    class One:
        def __init__(self, *_a, **_k):
            pass
        def prepare_runtime(self):
            return False
    monkeypatch.setattr(light_ocr, 'LightDialogueOcr', Light)
    monkeypatch.setattr(one_ocr, 'prepare_local_runtime', lambda _:tmp_path)
    monkeypatch.setattr(one_ocr, 'OneOcrProcess', One)
    service = PreferredDialogueOcr(tmp_path, tmp_path)
    assert service.prepare_runtime() and service.one is None
    assert service.recognize(image(1)) == ['compatible backup']


def test_frozen_helper_uses_runtime_directory_and_piped_hidden_process(tmp_path, monkeypatch):
    import io
    launched = []
    class Process:
        stdin = io.StringIO()
        stdout = io.StringIO('{"ready":true}\n')
        def __init__(self, command, **kwargs):
            launched.append((command, kwargs))
        def poll(self):
            return 0
        def wait(self, **_):
            return 0
    monkeypatch.setattr(one_ocr.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(one_ocr.sys, '_MEIPASS', str(tmp_path/'_internal'), raising=False)
    monkeypatch.setattr(one_ocr.subprocess, 'Popen', Process)
    service = one_ocr.OneOcrProcess(tmp_path/'cache')
    try:
        assert service.prepare_runtime()
        command, kwargs = launched[0]
        assert command == [str(tmp_path/'_internal/OneOcrWorker.exe'),'--oneocr-worker',str(tmp_path/'cache'),'0']
        assert kwargs['stdin'] == one_ocr.subprocess.PIPE
        assert kwargs['stdout'] == one_ocr.subprocess.PIPE
        assert kwargs['creationflags'] & one_ocr.subprocess.CREATE_NO_WINDOW
        assert kwargs['creationflags'] & one_ocr.subprocess.BELOW_NORMAL_PRIORITY_CLASS
    finally:
        service.close()
