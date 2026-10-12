"""Opt-in portable app updates from ossmoc/ossmoc-echo only.

SHA-256 is an integrity check, not a publisher signature. Never execute a
script from the downloaded archive. A locally generated PowerShell helper
waits for this process to exit, replaces EXE/runtime and rolls back on
failure. userdata/tools, documentation and license notices are never replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import threading
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile

from ludiglot.version import APP_VERSION

REPO_URL = 'https://github.com/ossmoc/ossmoc-echo'
RELEASE_API = 'https://api.github.com/repos/ossmoc/ossmoc-echo/releases/latest'
PACKAGE_NAMES = frozenset({'ossmocecho.exe', 'ludiglot.exe', '_internal', 'licenses',
                       'license', 'notice.md', 'third_party.md', 'sources.md',
                       'huong-dan.md', 'license-ocr-models.md', 'app-icon.ico'})
REQUIRED_FILES = ('OssmocEcho.exe', '_internal/python312.dll',
                  '_internal/PyQt6/Qt6/bin/Qt6Core.dll')


class AppUpdateError(RuntimeError):
    pass


def version_tuple(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise AppUpdateError('Phiên bản app không hợp lệ.')
    return tuple(int(part) for part in value.removeprefix('v').split('.'))


@dataclass(frozen=True)
class AppRelease:
    tag: str
    url: str
    size: int
    sha256: str

    @property
    def available(self):
        return version_tuple(self.tag) > version_tuple(APP_VERSION)

    @property
    def summary(self):
        if self.available:
            return f'Có bản cập nhật Ossmoc Echo {self.tag}'
        return f'Ossmoc Echo đã là bản mới nhất (v{APP_VERSION})'


@dataclass(frozen=True)
class PreparedAppUpdate:
    application: Path
    stage: Path
    plan_path: Path


def file_hash(path: Path):
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def validate_url(url: str, tag: str):
    parsed = urlparse(url)
    expected = f'/ossmoc/ossmoc-echo/releases/download/{tag}/'
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or
            not parsed.path.startswith(expected) or parsed.query or parsed.fragment or
            parsed.path.count('/') != expected.count('/')):
        raise AppUpdateError('Chỉ nhận file từ release chính thức Ossmoc Echo.')


class AppUpdater:
    def __init__(self, application: Path, *, opener=None):
        self.application = Path(application).resolve()
        self._open = opener or urlopen

    def _read(self, url, limit):
        request = Request(url, headers={'User-Agent': 'Ossmoc-Echo', 'Accept': 'application/vnd.github+json'})
        with self._open(request, timeout=25) as response:
            value = response.read(limit + 1)
        if len(value) > limit:
            raise AppUpdateError('Phản hồi cập nhật quá lớn.')
        return value

    def check_latest(self):
        value = json.loads(self._read(RELEASE_API, 2 * 1024**2).decode('utf-8'))
        tag = str(value.get('tag_name') or '')
        version_tuple(tag)
        if value.get('draft') or value.get('prerelease'):
            raise AppUpdateError('Không có bản app ổn định hợp lệ.')
        name = f'OssmocEcho-{tag}-Windows-x64.zip'
        found = [a for a in value.get('assets', []) if a.get('name') == name]
        if len(found) != 1:
            raise AppUpdateError('Release thiếu hoặc trùng gói Windows-x64.')
        asset = found[0]
        url = str(asset.get('browser_download_url') or '')
        validate_url(url, tag)
        size = int(asset.get('size') or 0)
        if not 0 < size <= 1024**3:
            raise AppUpdateError('Dung lượng gói app không hợp lệ.')
        checksum = str(asset.get('digest') or '').removeprefix('sha256:').lower()
        if not re.fullmatch('[a-f0-9]{64}', checksum):
            sums = [a for a in value.get('assets', []) if a.get('name') == 'SHA256SUMS.txt']
            if len(sums) != 1:
                raise AppUpdateError('Release thiếu checksum app.')
            sums_url = str(sums[0].get('browser_download_url') or '')
            validate_url(sums_url, tag)
            matches = re.findall(r'^([a-fA-F0-9]{64})\s+\*?' + re.escape(name) + r'\s*$',
                                 self._read(sums_url, 128 * 1024).decode('utf-8-sig'), re.M)
            if len(matches) != 1:
                raise AppUpdateError('Không xác định được checksum gói app.')
            checksum = matches[0].lower()
        return AppRelease(tag, url, size, checksum)

    def prepare(self, release: AppRelease, *, cancel=None, progress=lambda _: None):
        if not release.available:
            raise AppUpdateError('App đang dùng không cũ hơn release này.')
        validate_url(release.url, release.tag)
        if not re.fullmatch('[a-f0-9]{64}', release.sha256):
            raise AppUpdateError('Thiếu SHA-256 hợp lệ.')
        cancel = cancel or threading.Event()
        def cancelled():
            if cancel.is_set():
                raise AppUpdateError('Đã hủy cập nhật app; giữ bản đang dùng.')
        cancelled()
        if shutil.disk_usage(self.application).free < release.size * 3 + 256 * 1024**2:
            raise AppUpdateError('Không đủ dung lượng cập nhật app.')
        work = self.application / 'userdata/app-updates' / uuid4().hex
        work.mkdir(parents=True, exist_ok=False)
        archive = work / 'download.zip'
        request = Request(release.url, headers={'User-Agent': 'Ossmoc-Echo'})
        size = 0
        with self._open(request, timeout=30) as response, archive.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                cancelled()
                size += len(chunk)
                if size > release.size:
                    raise AppUpdateError('Gói app vượt dung lượng công bố.')
                output.write(chunk)
                progress(f'Đang tải app {release.tag} · {size * 100 // release.size}%')
        cancelled()
        if size != release.size or file_hash(archive) != release.sha256:
            raise AppUpdateError('File app bị thiếu/hỏng checksum; giữ bản đang dùng.')
        stage = work / 'new/OssmocEcho'
        self._extract(archive, stage, cancel)
        cancelled()
        # Old shortcut alias, if present, must also receive the new executable.
        if (self.application / 'Ludiglot.exe').is_file():
            shutil.copy2(stage / 'OssmocEcho.exe', stage / 'Ludiglot.exe')
        hashes = {p.relative_to(stage).as_posix(): file_hash(p) for p in stage.rglob('*') if p.is_file()}
        plan = {'application': str(self.application), 'stage': str(stage), 'parent_pid': os.getpid(),
                'tag': release.tag, 'hashes': hashes, 'result': str(work / 'result.json')}
        plan_path = work / 'plan.json'
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        (work / 'apply.ps1').write_text(APPLY_SCRIPT, encoding='utf-8-sig')
        return PreparedAppUpdate(self.application, stage, plan_path)

    @staticmethod
    def _extract(archive, stage, cancel):
        with zipfile.ZipFile(archive) as source:
            files = source.infolist()
            total = sum(info.file_size for info in files)
            if len(files) > 20000 or total > 2 * 1024**3:
                raise AppUpdateError('Gói giải nén quá lớn.')
            if shutil.disk_usage(stage.parents[2]).free < total * 2 + 256 * 1024**2:
                raise AppUpdateError('Không đủ chỗ giải nén và giữ bản app trước.')
            seen = set()
            for info in files:
                parts = PurePosixPath(info.filename).parts
                mode = info.external_attr >> 16
                if (not parts or parts[0] != 'OssmocEcho' or '\\' in info.filename or
                        any(p in ('.', '..') or ':' in p or p.rstrip(' .') != p for p in parts) or
                        stat.S_ISLNK(mode) or info.flag_bits & 1):
                    raise AppUpdateError('Đường dẫn/loại file trong ZIP không an toàn.')
                if len(parts) == 1:
                    if info.is_dir():
                        continue
                    raise AppUpdateError('Gói app thiếu thư mục gốc.')
                if parts[1].casefold() not in PACKAGE_NAMES:
                    raise AppUpdateError('Gói app chứa dữ liệu hoặc file ngoài phạm vi cập nhật.')
                reserved = re.compile(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)
                if any(reserved.match(p) for p in parts):
                    raise AppUpdateError('Tên file Windows không hợp lệ.')
                key = '/'.join(parts).casefold()
                if key in seen:
                    raise AppUpdateError('ZIP chứa tên file trùng.')
                seen.add(key)
            stage.mkdir(parents=True, exist_ok=False)
            for info in files:
                if cancel.is_set():
                    raise AppUpdateError('Đã hủy giải nén app.')
                parts = PurePosixPath(info.filename).parts[1:]
                if not parts:
                    continue
                target = stage.joinpath(*parts)
                if not target.resolve().is_relative_to(stage.resolve()):
                    raise AppUpdateError('Đường dẫn rời thư mục staging.')
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(info) as original, target.open('xb') as output:
                        shutil.copyfileobj(original, output)
        if not all((stage / name).is_file() for name in REQUIRED_FILES):
            raise AppUpdateError('Gói app thiếu EXE, Python hoặc Qt runtime.')
        with (stage / 'OssmocEcho.exe').open('rb') as executable:
            if executable.read(2) != b'MZ':
                raise AppUpdateError('EXE cập nhật không hợp lệ.')

    def launch(self, prepared: PreparedAppUpdate):
        if prepared.application.resolve() != self.application:
            raise AppUpdateError('Sai thư mục app cần cập nhật.')
        work = prepared.plan_path.parent.resolve()
        if not work.is_relative_to(self.application / 'userdata/app-updates'):
            raise AppUpdateError('Sai thư mục staging app.')
        if os.name != 'nt':
            raise AppUpdateError('Tự cập nhật EXE chỉ có trên Windows.')
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                          '-File', str(work / 'apply.ps1'), '-PlanPath', str(prepared.plan_path)],
                         cwd=work, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


APPLY_SCRIPT = r'''param([Parameter(Mandatory=$true)][string]$PlanPath, [switch]$NoRestart)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$application = [IO.Path]::GetFullPath($plan.application).TrimEnd('\')
$stage = [IO.Path]::GetFullPath($plan.stage).TrimEnd('\')
$work = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($PlanPath))
$allowed = @('ossmocecho.exe','ludiglot.exe','_internal')
$movedOld = @(); $movedNew = @()
$backup = Join-Path $work 'previous'
try {
    if (-not $work.StartsWith(($application + '\userdata\app-updates\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid update directory' }
    if ($stage -ne (Join-Path $work 'new\OssmocEcho')) { throw 'Invalid stage directory' }
    if ($plan.result -ne (Join-Path $work 'result.json')) { throw 'Invalid result path' }
    $parentProcess = Get-Process -Id $plan.parent_pid -ErrorAction SilentlyContinue
    if ($parentProcess -and -not $parentProcess.WaitForExit(120000)) { throw 'App did not exit; old app retained' }
    foreach ($entry in $plan.hashes.PSObject.Properties) {
        $relative = $entry.Name.Replace('/', '\')
        $file = [IO.Path]::GetFullPath((Join-Path $stage $relative))
        if (-not $file.StartsWith(($stage + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid hash path' }
        $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
        $inputStream = [IO.File]::OpenRead($file)
        try { $actualHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash($inputStream)).Replace('-', '').ToLowerInvariant() }
        finally { $inputStream.Dispose(); $hashAlgorithm.Dispose() }
        if ($actualHash -ne $entry.Value) { throw 'Staged file checksum mismatch' }
    }
    # Documentation/license files stay in staging; only replace EXE/runtime.
    $entries = @(Get-ChildItem -LiteralPath $stage | Where-Object { $allowed -contains $_.Name.ToLowerInvariant() })
    foreach ($entry in $entries) {
        if ($allowed -notcontains $entry.Name.ToLowerInvariant() -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid update item' }
        $existing = Join-Path $application $entry.Name
        if (Test-Path -LiteralPath $existing) {
            if ((Get-Item -LiteralPath $existing).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing app junction' }
        }
    }
    New-Item -ItemType Directory -Path $backup | Out-Null
    foreach ($entry in $entries) {
        $existing = Join-Path $application $entry.Name
        if (Test-Path -LiteralPath $existing) {
            Move-Item -LiteralPath $existing -Destination (Join-Path $backup $entry.Name)
            $movedOld += $entry.Name
        }
    }
    foreach ($entry in $entries) {
        Move-Item -LiteralPath $entry.FullName -Destination (Join-Path $application $entry.Name)
        $movedNew += $entry.Name
    }
    @{success=$true;version=$plan.tag;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath $plan.result -Encoding UTF8
    if (-not $NoRestart) { Start-Process -FilePath (Join-Path $application 'OssmocEcho.exe') -WorkingDirectory $application -WindowStyle Hidden }
} catch {
    $failure = $_.Exception.Message
    foreach ($name in $movedNew) { Move-Item -LiteralPath (Join-Path $application $name) -Destination (Join-Path $stage $name) }
    foreach ($name in $movedOld) { Move-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $application $name) }
    @{success=$false;error=$failure;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $work 'result.json') -Encoding UTF8
    if (-not $NoRestart -and (Test-Path -LiteralPath (Join-Path $application 'OssmocEcho.exe'))) { Start-Process -FilePath (Join-Path $application 'OssmocEcho.exe') -WorkingDirectory $application -WindowStyle Hidden }
}
'''
