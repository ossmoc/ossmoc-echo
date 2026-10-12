from __future__ import annotations

import io
import sys

from ludiglot.infrastructure.terminal_log_tee import _TeeStream, install_process_log_tee


def test_tee_stream_writes_to_wrapped_stream_and_log_file(tmp_path):
    wrapped = io.StringIO()
    log_path = tmp_path / "gui.log"
    stream = _TeeStream(wrapped, log_path)

    stream.write("hello\n")
    stream.flush()

    assert wrapped.getvalue() == "hello\n"
    assert log_path.read_text(encoding="utf-8") == "hello\n"


def test_install_process_log_tee_is_idempotent(monkeypatch, tmp_path):
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    log_path = tmp_path / "gui.log"

    install_process_log_tee(log_path)
    first_stdout = sys.stdout
    first_stderr = sys.stderr
    install_process_log_tee(log_path)

    assert sys.stdout is first_stdout
    assert sys.stderr is first_stderr

    sys.stdout.write("out\n")
    sys.stderr.write("err\n")

    assert stdout.getvalue() == "out\n"
    assert stderr.getvalue() == "err\n"
    assert log_path.read_text(encoding="utf-8") == "out\nerr\n"
