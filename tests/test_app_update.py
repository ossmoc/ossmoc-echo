import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import zipfile
import hashlib

import pytest
from ludiglot.core.app_update import (AppUpdater, AppRelease, AppUpdateError, version_tuple,
    validate_url, REQUIRED_FILES, file_hash, RELEASE_API)
from ludiglot.version import APP_VERSION


def package(extra=(), contents=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name in REQUIRED_FILES:
            archive.writestr('OssmocEcho/' + name, (contents or {}).get(name, b'MZverified app'))
        for name, value in extra:
            info = zipfile.ZipInfo(name)
            info.filename = name  # Keep malformed separators instead of Windows ZipInfo normalization.
            archive.writestr(info, value)
    return output.getvalue()


def release(data):
    return AppRelease('v1.0.2', 'https://github.com/ossmoc/ossmoc-echo/releases/download/v1.0.2/OssmocEcho-v1.0.2-Windows-x64.zip',
                      len(data), hashlib.sha256(data).hexdigest())


def opener_for(data):
    return lambda request, **kwargs: io.BytesIO(data)


def test_version_is_101_and_independent_of_translation():
    assert APP_VERSION == '1.0.1'
    assert version_tuple('v1.10.0') > version_tuple('v1.9.9')
    assert not AppRelease('v1.0.1', '', 0, '').available
    assert not AppRelease('v1.0.0', '', 0, '').available
    assert 'v1.0.1' in AppRelease('v1.0.1', '', 0, '').summary
    assert 'v1.0.2' in AppRelease('v1.0.2', '', 0, '').summary


@pytest.mark.parametrize('tag', ['main', '../1.0.1', 'v1.0.2-rc1', '1.2', '1.2.3.4'])
def test_invalid_tags(tag):
    with pytest.raises(AppUpdateError): version_tuple(tag)


@pytest.mark.parametrize('url', ['http://github.com/ossmoc/ossmoc-echo/releases/download/v1.0.2/file.zip',
    'https://github.com/elsewhere/repo/releases/download/v1.0.2/file.zip',
    'https://github.com.evil/ossmoc/ossmoc-echo/releases/download/v1.0.2/file.zip',
    'https://github.com/ossmoc/ossmoc-echo/releases/download/v1.0.1/file.zip',
    'https://github.com/ossmoc/ossmoc-echo/releases/download/v1.0.2/file.zip?redirect=evil'])
def test_only_official_release_urls(url):
    with pytest.raises(AppUpdateError): validate_url(url, 'v1.0.2')


def github_response(data, **changes):
    asset = release(data)
    return {'tag_name': asset.tag, 'draft': False, 'prerelease': False,
            'assets': [{'name': 'OssmocEcho-v1.0.2-Windows-x64.zip', 'size': asset.size,
                        'browser_download_url': asset.url, 'digest': 'sha256:' + asset.sha256}], **changes}


def test_github_digest(tmp_path):
    value = github_response(package())
    updater = AppUpdater(tmp_path, opener=opener_for(json.dumps(value).encode()))
    assert updater.check_latest().available


def test_checksum_asset_fallback(tmp_path):
    data = package()
    value = github_response(data)
    value['assets'][0]['digest'] = ''
    value['assets'].append({'name': 'SHA256SUMS.txt', 'browser_download_url':
        'https://github.com/ossmoc/ossmoc-echo/releases/download/v1.0.2/SHA256SUMS.txt'})
    seen = []
    def opener(request, **kwargs):
        seen.append(request.full_url)
        return io.BytesIO(json.dumps(value).encode() if request.full_url == RELEASE_API else
                         f'{hashlib.sha256(data).hexdigest()}  OssmocEcho-v1.0.2-Windows-x64.zip\n'.encode())
    assert AppUpdater(tmp_path, opener=opener).check_latest().sha256 == release(data).sha256
    assert len(seen) == 2


@pytest.mark.parametrize('changes', [{'draft': True}, {'prerelease': True}, {'assets': []}])
def test_reject_non_installable_release(tmp_path, changes):
    updater = AppUpdater(tmp_path, opener=opener_for(json.dumps(github_response(package(), **changes)).encode()))
    with pytest.raises(AppUpdateError): updater.check_latest()


def test_prepare_keeps_old_app_and_userdata(tmp_path):
    data = package()
    (tmp_path / 'OssmocEcho.exe').write_bytes(b'old app')
    (tmp_path / 'Ludiglot.exe').write_bytes(b'old alias')
    (tmp_path / 'userdata').mkdir()
    (tmp_path / 'userdata/settings.json').write_text('private settings')
    prepared = AppUpdater(tmp_path, opener=opener_for(data)).prepare(release(data))
    assert (tmp_path / 'OssmocEcho.exe').read_bytes() == b'old app'
    assert (tmp_path / 'userdata/settings.json').read_text() == 'private settings'
    assert file_hash(prepared.stage / 'Ludiglot.exe') == file_hash(prepared.stage / 'OssmocEcho.exe')
    assert (prepared.plan_path.parent / 'apply.ps1').is_file()


@pytest.mark.parametrize('fault', ['checksum', 'truncated', 'oversized', 'cancelled', 'network'])
def test_failed_preparation_leaves_old_app(tmp_path, fault):
    data = package()
    asset = release(data)
    cancel = threading.Event()
    if fault == 'checksum': data = data.replace(b'MZverified app', b'MZcorrupt! app', 1)
    if fault == 'truncated': data = data[:-1]
    if fault == 'oversized': data += b'x'
    if fault == 'cancelled': cancel.set()
    def opener(request, **kwargs):
        if fault == 'network': raise OSError('offline')
        return io.BytesIO(data)
    (tmp_path / 'OssmocEcho.exe').write_bytes(b'old')
    with pytest.raises((AppUpdateError, OSError)):
        AppUpdater(tmp_path, opener=opener).prepare(asset, cancel=cancel)
    assert (tmp_path / 'OssmocEcho.exe').read_bytes() == b'old'
    assert not (tmp_path / 'userdata/settings.json').exists()


@pytest.mark.parametrize('name', ['../outside.exe', 'OssmocEcho/../outside.exe',
    'OssmocEcho/userdata/settings.json', 'OssmocEcho/tools/anything.exe',
    'OssmocEcho/_internal/../../outside.exe', 'OssmocEcho/_internal/C:/outside',
    'OssmocEcho/_internal/nul.txt', 'OssmocEcho/_internal/trailing. ',
    'OssmocEcho\\..\\outside.exe'])
def test_zip_rejects_unsafe_or_private_entries(tmp_path, name):
    data = package([(name, b'bad')])
    with pytest.raises(AppUpdateError):
        AppUpdater(tmp_path, opener=opener_for(data)).prepare(release(data))
    assert not (tmp_path.parent / 'outside.exe').exists()


def test_missing_runtime_or_bad_exe(tmp_path):
    data = package(contents={'OssmocEcho.exe': b'not an exe'})
    with pytest.raises(AppUpdateError):
        AppUpdater(tmp_path, opener=opener_for(data)).prepare(release(data))


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows replacement helper')
@pytest.mark.parametrize('tamper', [False, True])
def test_native_helper_transaction_preserves_private_data(tmp_path, tamper):
    application = tmp_path / 'Ứng dụng có khoảng trắng'
    application.mkdir()
    (application / 'OssmocEcho.exe').write_bytes(b'old executable')
    (application / 'Ludiglot.exe').write_bytes(b'old alias')
    (application / '_internal').mkdir()
    (application / '_internal/old.dll').write_bytes(b'old runtime')
    (application / 'userdata').mkdir()
    (application / 'userdata/settings.json').write_text('private settings')
    (application / 'tools').mkdir()
    (application / 'tools/extractor.exe').write_bytes(b'extractor')
    (application / 'LICENSE').write_bytes(b'keep license')
    (application / 'HUONG-DAN.md').write_bytes(b'keep documentation')
    data = package([('OssmocEcho/LICENSE', b'new license'), ('OssmocEcho/HUONG-DAN.md', b'new docs')])
    prepared = AppUpdater(application, opener=opener_for(data)).prepare(release(data))
    plan = json.loads(prepared.plan_path.read_text(encoding='utf-8'))
    plan['parent_pid'] = 99999999  # No real user process is stopped/waited.
    prepared.plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    if tamper: (prepared.stage / 'OssmocEcho.exe').write_bytes(b'tampered')
    helper = prepared.plan_path.parent / 'apply.ps1'
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    completed = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(helper), '-PlanPath', str(prepared.plan_path), '-NoRestart'], capture_output=True, timeout=30)
    result = json.loads((helper.parent / 'result.json').read_text(encoding='utf-8-sig'))
    assert result['success'] == (not tamper), (result, completed.stderr)
    assert (application / 'userdata/settings.json').read_text() == 'private settings'
    assert (application / 'tools/extractor.exe').read_bytes() == b'extractor'
    assert (application / 'LICENSE').read_bytes() == b'keep license'
    assert (application / 'HUONG-DAN.md').read_bytes() == b'keep documentation'
    if tamper:
        assert (application / 'OssmocEcho.exe').read_bytes() == b'old executable'
        assert (application / '_internal/old.dll').is_file()
    else:
        assert (application / 'OssmocEcho.exe').read_bytes() == b'MZverified app'
        assert file_hash(application / 'Ludiglot.exe') == file_hash(application / 'OssmocEcho.exe')
        assert (helper.parent / 'previous/_internal/old.dll').is_file()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows lock and rollback')
def test_partial_replacement_failure_rolls_back(tmp_path):
    import ctypes
    from ctypes import wintypes
    application = tmp_path / 'app'
    application.mkdir()
    (application / 'OssmocEcho.exe').write_bytes(b'old exe')
    (application / '_internal').mkdir()
    (application / '_internal/old.dll').write_bytes(b'old dll')
    (application / 'LICENSE').write_bytes(b'old license')
    data = package([('OssmocEcho/LICENSE', b'new license')])
    prepared = AppUpdater(application, opener=opener_for(data)).prepare(release(data))
    plan = json.loads(prepared.plan_path.read_text())
    plan['parent_pid'] = 99999999
    prepared.plan_path.write_text(json.dumps(plan), encoding='utf-8')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                                  wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(application / 'OssmocEcho.exe'), 0x80000000, 1, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        helper = prepared.plan_path.parent / 'apply.ps1'
        powershell = Path(os.environ.get('SystemRoot','C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy','Bypass',
            '-File',str(helper),'-PlanPath',str(prepared.plan_path),'-NoRestart'], capture_output=True,timeout=30)
    finally:
        kernel.CloseHandle(handle)
    result = json.loads((helper.parent / 'result.json').read_text(encoding='utf-8-sig'))
    assert result['success'] is False
    assert (application / 'OssmocEcho.exe').read_bytes() == b'old exe'
    assert (application / '_internal/old.dll').read_bytes() == b'old dll'
    assert (application / 'LICENSE').read_bytes() == b'old license'
