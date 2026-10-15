import sys
import subprocess

import pytest

from ludiglot.infrastructure import native_extractor as module


def runner(tmp_path, monkeypatch, code):
    tool = tmp_path/'tools'/'FModelCLI.exe'
    tool.parent.mkdir()
    tool.write_bytes(b'fixture')
    popen = subprocess.Popen
    children = []
    def launch(_cmd, **kwargs):
        child = popen([sys.executable, '-u', '-c', code], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    return module.NativeExtractor(tool), children


def test_silent_extractor_times_out_and_reaps_child_and_keys(tmp_path, monkeypatch):
    extractor, children = runner(tmp_path, monkeypatch, 'import time; time.sleep(60)')
    with pytest.raises(TimeoutError, match='thoát game'):
        extractor.run_extraction(tmp_path, 'test-key', tmp_path/'out', 'Config/Json', timeout=.1)
    assert children[0].poll() is not None
    assert not list((tmp_path/'userdata/cache').glob('native_aes_keys_*'))


def test_extractor_reports_output_and_nonzero_failure(tmp_path, monkeypatch):
    extractor, children = runner(tmp_path, monkeypatch, 'print("locked file"); raise SystemExit(2)')
    messages = []
    assert not extractor.run_extraction(tmp_path, 'key', tmp_path/'out', 'ConfigDB/en', progress=messages.append)
    assert any('locked file' in m for m in messages)
    assert children[0].poll() == 2


def test_cancel_from_progress_reaps_child(tmp_path, monkeypatch):
    extractor, children = runner(tmp_path, monkeypatch, 'import time; print("mounting"); time.sleep(60)')
    def cancel(_):
        raise ValueError('cancelled')
    with pytest.raises(ValueError, match='cancelled'):
        extractor.run_extraction(tmp_path, 'key', tmp_path/'out', 'ConfigDB', progress=cancel)
    assert children[0].poll() is not None
