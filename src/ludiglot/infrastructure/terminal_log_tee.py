from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import TextIO

_STREAM_LOCK = threading.Lock()


def _append_to_log(log_path: Path, data: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(data)
    except Exception:
        # Best-effort file logging; ignore write errors
        pass


class _TeeStream:
    def __init__(self, stream: TextIO, log_path: Path) -> None:
        self._stream = stream
        self._log_path = log_path
        self._ludiglot_tee = True

    def write(self, data: str) -> int:
        if not data:
            return 0
        with _STREAM_LOCK:
            written = 0
            try:
                written = self._stream.write(data)
            except Exception:
                # Best-effort output; ignore stream write errors
                pass
            _append_to_log(self._log_path, data)
            return written

    def flush(self) -> None:
        with _STREAM_LOCK:
            try:
                self._stream.flush()
            except Exception:
                # Best-effort flush; ignore stream flush errors
                pass


def install_process_log_tee(log_path: Path) -> None:
    """将 stdout/stderr/Qt 警告同步写入日志文件。"""
    if not hasattr(sys.stdout, "_ludiglot_tee"):
        sys.stdout = _TeeStream(sys.stdout, log_path)  # type: ignore[assignment]
    if not hasattr(sys.stderr, "_ludiglot_tee"):
        sys.stderr = _TeeStream(sys.stderr, log_path)  # type: ignore[assignment]

    try:
        from PyQt6.QtCore import qInstallMessageHandler
    except Exception:
        return

    def qt_message_handler(mode, context, message):
        log_msg = f"[Qt {mode.name}] {message}\n"
        _append_to_log(log_path, log_msg)

    qInstallMessageHandler(qt_message_handler)
