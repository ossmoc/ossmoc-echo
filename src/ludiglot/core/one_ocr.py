"""Optional local Snipping Tool OCR. Microsoft binaries are never bundled/downloaded.

The undocumented native ABI is isolated in a child process so its private ONNX
runtime cannot conflict with WinRT/RapidOCR in the application process.
"""
from __future__ import annotations

import base64
import ctypes as C
import json
import hashlib
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading


def _installed_directory(path: Path) -> bool:
    try:
        root = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'WindowsApps'
        # WindowsApps itself can deny directory enumeration while package
        # files remain readable. Resolve final paths, then check actual files.
        relative = path.resolve().relative_to(root.resolve())
        return relative.parts[0].startswith('Microsoft.ScreenSketch_') and all(
            (path / name).is_file() and
            (path / name).resolve().is_relative_to(root.resolve())
            for name in ('oneocr.dll', 'oneocr.onemodel', 'onnxruntime.dll'))
    except (OSError, ValueError, IndexError):
        return False


def discover_oneocr() -> Path | None:
    if sys.platform != 'win32':
        return None
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        completed = subprocess.run([
            str(powershell), '-NoProfile', '-NonInteractive', '-Command',
            "Get-AppxPackage -Name Microsoft.ScreenSketch | Select-Object -ExpandProperty InstallLocation | ConvertTo-Json -Compress",
        ], capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
        value = json.loads(completed.stdout.decode('utf-8-sig'))
        for package in ([value] if isinstance(value, str) else value or []):
            path = Path(package) / 'SnippingTool'
            if _installed_directory(path):
                return path
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare_local_runtime(cache_root: Path) -> Path | None:
    """Stage this PC's installed package privately, never in release resources.

    Windows can allow reading WindowsApps files but refuse loading a DLL from
    that package into an unpackaged process. Match each cached byte to its
    installed source, including on subsequent helper launches.
    """
    source = discover_oneocr()
    if source is None:
        return None
    names = ('oneocr.dll', 'oneocr.onemodel', 'onnxruntime.dll')
    target = cache_root / source.parent.name
    target.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in names:
        original = source / name
        if not 0 < original.stat().st_size < 256_000_000:
            raise ValueError('Invalid installed OneOCR asset size')
        hashes[name] = _sha(original)
        destination = target / name
        if not destination.is_file() or _sha(destination) != hashes[name]:
            temporary = destination.with_suffix(destination.suffix + '.partial')
            shutil.copyfile(original, temporary)
            if _sha(temporary) != hashes[name]:
                raise ValueError('OneOCR staging checksum mismatch')
            temporary.replace(destination)
    receipt = target / 'installed-source.json'
    receipt.write_text(json.dumps({'source': str(source), 'hashes': hashes}), encoding='utf-8')
    return target


def _trusted_runtime(path: Path) -> bool:
    if _installed_directory(path):
        return True
    try:
        receipt = json.loads((path / 'installed-source.json').read_text(encoding='utf-8'))
        source = Path(receipt['source'])
        if not _installed_directory(source):
            return False
        return all(_sha(path / name) == _sha(source / name) == receipt['hashes'][name]
                   for name in ('oneocr.dll', 'oneocr.onemodel', 'onnxruntime.dll'))
    except (OSError, ValueError, KeyError, TypeError):
        return False


class NativeOneOcr:
    """Private ABI adapter; instantiate only in the isolated helper or QA."""
    def __init__(self, directory: Path):
        self.directory = directory
        self.cookie = os.add_dll_directory(str(directory))
        try:
            self.dll = C.WinDLL(str(directory / 'oneocr.dll'), winmode=0x1100)
        except Exception:
            self.cookie.close()
            raise
        self.init = C.c_int64()
        self.pipe = C.c_int64()
        self.options = C.c_int64()
        pointer = C.POINTER(C.c_int64)
        self.rect_type = type('Rect', (C.Structure,), {'_fields_': [(k, C.c_float) for k in ('x1','y1','x2','y2','x3','y3','x4','y4')]})
        self.img_type = type('Img', (C.Structure,), {'_fields_': [
            ('type', C.c_int32), ('width', C.c_int32), ('height', C.c_int32),
            ('reserved', C.c_int32), ('step', C.c_int64), ('data', C.POINTER(C.c_ubyte))]})
        signatures = {
            'CreateOcrInitOptions': [pointer], 'OcrInitOptionsSetUseModelDelayLoad': [C.c_int64, C.c_char],
            'CreateOcrPipeline': [C.c_char_p,C.c_char_p,C.c_int64,pointer],
            'CreateOcrProcessOptions': [pointer],
            'OcrProcessOptionsSetMaxRecognitionLineCount': [C.c_int64,C.c_int64],
            'RunOcrPipeline': [C.c_int64,C.POINTER(self.img_type),C.c_int64,pointer],
            'GetOcrLineCount': [C.c_int64,pointer], 'GetOcrLine': [C.c_int64,C.c_int64,pointer],
            'GetOcrLineContent': [C.c_int64,C.POINTER(C.c_char_p)],
            'GetOcrLineBoundingBox': [C.c_int64,C.POINTER(C.POINTER(self.rect_type))],
        }
        for name, arguments in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = arguments, C.c_int64
        for name in ('ReleaseOcrResult','ReleaseOcrInitOptions','ReleaseOcrPipeline','ReleaseOcrProcessOptions'):
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = [C.c_int64], None
        try:
            self._check(self.dll.CreateOcrInitOptions(C.byref(self.init)))
            self._check(self.dll.OcrInitOptionsSetUseModelDelayLoad(self.init, b'\0'))
            self._check(self.dll.CreateOcrPipeline(str(directory / 'oneocr.onemodel').encode('utf-8'),
                b'kj)TGtrK>f]b[Piow.gU+nC@s""""""4', self.init, C.byref(self.pipe)))
            self._check(self.dll.CreateOcrProcessOptions(C.byref(self.options)))
            self._check(self.dll.OcrProcessOptionsSetMaxRecognitionLineCount(self.options, 1000))
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(status):
        if status:
            raise RuntimeError(f'OneOCR native status {status}')

    def recognize_bgra(self, raw: bytes, width: int, height: int) -> list[dict]:
        if not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > 4_194_304 or len(raw) != width * height * 4:
            raise ValueError('Invalid OCR image buffer')
        buffer = (C.c_ubyte * len(raw)).from_buffer_copy(raw)
        image = self.img_type(3, width, height, 0, width * 4, buffer)
        result = C.c_int64()
        self._check(self.dll.RunOcrPipeline(self.pipe, C.byref(image), self.options, C.byref(result)))
        try:
            count = C.c_int64()
            self._check(self.dll.GetOcrLineCount(result, C.byref(count)))
            if not 0 <= count.value <= 1000:
                raise RuntimeError('Invalid OneOCR line count')
            boxes = []
            for n in range(count.value):
                line, content = C.c_int64(), C.c_char_p()
                rect = C.POINTER(self.rect_type)()
                self._check(self.dll.GetOcrLine(result, n, C.byref(line)))
                self._check(self.dll.GetOcrLineContent(line, C.byref(content)))
                self._check(self.dll.GetOcrLineBoundingBox(line, C.byref(rect)))
                if content.value and rect:
                    r = rect.contents
                    boxes.append({'text': content.value.decode('utf-8'),
                                  'box': [[getattr(r,f'x{i}'),getattr(r,f'y{i}')] for i in range(1,5)]})
            return boxes
        finally:
            if result.value:
                self.dll.ReleaseOcrResult(result)

    def recognize(self, image):
        return self.recognize_bgra(image.convert('RGBA').tobytes('raw', 'BGRA'), image.width, image.height)

    def close(self):
        for attribute, function in (('options','ReleaseOcrProcessOptions'),('pipe','ReleaseOcrPipeline'),('init','ReleaseOcrInitOptions')):
            handle = getattr(self, attribute, None)
            if handle is not None and handle.value:
                getattr(self.dll, function)(handle)
                handle.value = 0
        if getattr(self, 'cookie', None):
            self.cookie.close()
            self.cookie = None


def limit_worker_cpu(max_cpus=2):
    """Keep a native library's private thread pools off the whole CPU.

    Applies only inside our helper, never to the game or foreground OCR.
    Set affinity before loading the native pipeline so it sees the budget.
    """
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = C.c_void_p
    kernel.GetProcessAffinityMask.argtypes = [C.c_void_p, C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [C.c_void_p, C.c_size_t]
    handle = kernel.GetCurrentProcess()
    allowed, system = C.c_size_t(), C.c_size_t()
    if kernel.GetProcessAffinityMask(handle, C.byref(allowed), C.byref(system)):
        bits = [1 << n for n in range(C.sizeof(C.c_size_t)*8) if allowed.value & (1 << n)]
        if len(bits) > max_cpus:
            kernel.SetProcessAffinityMask(handle, sum(bits[-max_cpus:]))


def worker_main(directory: Path, cpu_budget=0) -> int:
    if not _trusted_runtime(directory):
        return 2
    if cpu_budget:
        limit_worker_cpu(cpu_budget)
    native = NativeOneOcr(directory)
    try:
        print(json.dumps({'ready': True}), flush=True)
        for line in sys.stdin:
            if len(line) > 24_000_000:
                return 3
            request = json.loads(line)
            raw = base64.b64decode(request['bgra'], validate=True)
            result = native.recognize_bgra(raw, request['width'], request['height'])
            print(json.dumps({'boxes': result}, ensure_ascii=False), flush=True)
    finally:
        native.close()
    return 0


class OneOcrProcess:
    """Persistent private-runtime helper. Calls belong on the fallback thread."""
    def __init__(self, directory: Path, *, response_timeout=3.0, cpu_budget=0):
        if not isinstance(cpu_budget, int) or not 0 <= cpu_budget <= 64:
            raise ValueError('Invalid OneOCR CPU budget')
        self.directory = directory
        self.cpu_budget = cpu_budget
        self.response_timeout = response_timeout
        self.process = None
        self.responses = queue.Queue(maxsize=2)
        self._lock = threading.RLock()
        self._closed = threading.Event()

    def _start(self):
        if self._closed.is_set():
            raise RuntimeError('OneOCR helper is closed')
        if self.process is not None:
            return
        # A previous reader may still deliver EOF into its own old queue.
        self.responses = queue.Queue(maxsize=2)
        if getattr(sys, 'frozen', False):
            # Keep helpers inside the existing runtime directory: older app
            # updaters already replace _internal atomically without new root
            # executable allowlist entries or cluttering the user's folder.
            worker = Path(sys._MEIPASS) / 'OneOcrWorker.exe'
            command = [str(worker), '--oneocr-worker', str(self.directory), str(self.cpu_budget)]
        else:
            command = [sys.executable, '-m', 'ludiglot.core.one_ocr', '--oneocr-worker', str(self.directory), str(self.cpu_budget)]
        environment = os.environ.copy()
        environment['PYTHONIOENCODING'] = 'utf-8'
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', env=environment,
            creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                           | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)))
        if self._closed.is_set():
            self.close()
            raise RuntimeError('OneOCR closed during startup')
        output, responses = self.process.stdout, self.responses
        def reader():
            try:
                for line in output:
                    if len(line) > 4_000_000:
                        break
                    responses.put_nowait(json.loads(line))
            except (OSError, ValueError, queue.Full):
                pass
            finally:
                try:
                    responses.put_nowait({'error': 'OneOCR helper closed'})
                except queue.Full:
                    pass
        threading.Thread(target=reader, name='Echo-OneOCR-IPC', daemon=True).start()
        if not self.responses.get(timeout=15).get('ready'):
            raise RuntimeError('OneOCR helper not ready')

    def prepare_runtime(self):
        try:
            with self._lock:
                self._start()
            return True
        except Exception:
            self.close()
            return False

    def recognize(self, image):
        with self._lock:
            try:
                self._start()
                message = {'width': image.width, 'height': image.height,
                           'bgra': base64.b64encode(image.convert('RGBA').tobytes('raw', 'BGRA')).decode('ascii')}
                self.process.stdin.write(json.dumps(message) + '\n')
                self.process.stdin.flush()
                response = self.responses.get(timeout=self.response_timeout)
                if 'error' in response or not isinstance(response.get('boxes'), list):
                    raise RuntimeError('Invalid OneOCR helper response')
                return response['boxes']
            except Exception:
                self.close()
                raise

    def close(self):
        self._closed.set()
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()


class PreferredDialogueOcr:
    """Installed OneOCR fallback, with the shipped CPU model as compatibility backup."""
    def __init__(self, model_root, cache_root, logger=lambda _: None, *, cpu_budget=0):
        from ludiglot.core.light_ocr import LightDialogueOcr
        self.light = LightDialogueOcr(model_root, logger)
        self.cache_root = cache_root
        self.logger = logger
        self.cpu_budget = cpu_budget
        self.one = None
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def prepare_runtime(self):
        # Keep native import ordering before WinRT, including on PCs that later
        # lose access to their Snipping Tool package.
        light_ready = self.light.prepare_runtime()
        try:
            from ludiglot.core.one_ocr import OneOcrProcess, prepare_local_runtime
            directory = prepare_local_runtime(self.cache_root)
            if directory:
                candidate = OneOcrProcess(directory, cpu_budget=self.cpu_budget)
                if candidate.prepare_runtime():
                    if self._closed.is_set():
                        candidate.close()
                        return False
                    self.one = candidate
                    self.logger('OCR dự phòng: OneOCR trên máy đã sẵn sàng (offline).')
                    return True
        except Exception as exc:
            self.logger(f'OneOCR chưa sẵn sàng; giữ OCR dự phòng hiện có: {exc}')
        return light_ready

    def recognize(self, image):
        with self._lock:
            if self._closed.is_set():
                return []
            candidate = self.one
            if candidate is not None:
                try:
                    return candidate.recognize(image)
                except Exception:
                    candidate.close()
                    self.one = None
                    self.logger('OneOCR không còn khả dụng; chuyển về OCR dự phòng hiện có.')
            return self.light.recognize(image)

    def close(self):
        # Do not wait for the inference lock during shutdown; terminating the
        # private helper releases its blocked request, bounded by its timeout.
        self._closed.set()
        candidate = self.one
        if candidate is not None:
            candidate.close()

if __name__ == '__main__':
    if len(sys.argv) not in (3,4) or sys.argv[1] != '--oneocr-worker':
        raise SystemExit('Experimental OCR helper: --oneocr-worker <local-runtime> [CPU-budget]')
    budget = int(sys.argv[3]) if len(sys.argv) == 4 else 0
    if not 0 <= budget <= 64:
        raise SystemExit('Invalid OneOCR CPU budget')
    raise SystemExit(worker_main(Path(sys.argv[2]), budget))
