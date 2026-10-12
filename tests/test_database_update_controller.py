from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog

from ludiglot.ui.database_update_controller import DatabaseUpdateController


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class FakeProgressDialog:
    instances = []

    def __init__(self, title, message, parent=None):
        self.title = title
        self.message = message
        self.parent = parent
        self.labels = []
        self.shown = False
        self.closed = False
        FakeProgressDialog.instances.append(self)

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True

    def setLabelText(self, text):
        self.labels.append(text)


class FakeDialog:
    questions = []
    warnings = []
    criticals = []
    information_calls = []
    question_result = QDialog.DialogCode.Accepted

    @classmethod
    def reset(cls):
        cls.questions = []
        cls.warnings = []
        cls.criticals = []
        cls.information_calls = []
        cls.question_result = QDialog.DialogCode.Accepted

    @classmethod
    def question(cls, parent, title, message):
        cls.questions.append((parent, title, message))
        return cls.question_result

    @classmethod
    def warning(cls, parent, title, message):
        cls.warnings.append((parent, title, message))

    @classmethod
    def critical(cls, parent, title, message):
        cls.criticals.append((parent, title, message))

    @classmethod
    def information(cls, parent, title, message):
        cls.information_calls.append((parent, title, message))


class FakeThread:
    instances = []
    result = (True, "done")
    progress_messages = ["halfway"]

    def __init__(self, config_path: Path, output_path: Path):
        self.config_path = config_path
        self.output_path = output_path
        self.progress = FakeSignal()
        self.finished = FakeSignal()
        self.started = False
        FakeThread.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []
        cls.result = (True, "done")
        cls.progress_messages = ["halfway"]

    def start(self):
        self.started = True
        for message in self.progress_messages:
            self.progress.emit(message)
        self.finished.emit(*self.result)


def setup_function():
    FakeDialog.reset()
    FakeThread.reset()
    FakeProgressDialog.instances = []


def config(tmp_path, **overrides):
    values = {
        "game_pak_root": tmp_path / "game",
        "game_install_root": None,
        "db_path": tmp_path / "db.json",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_controller(tmp_path, *, cfg=None, refresh_runtime=None, logs=None):
    logs = logs if logs is not None else []
    return DatabaseUpdateController(
        parent="parent",
        config_provider=lambda: cfg or config(tmp_path),
        config_path=tmp_path / "settings.json",
        refresh_runtime=refresh_runtime or (lambda: True),
        log=logs.append,
        dialog_cls=FakeDialog,
        progress_dialog_cls=FakeProgressDialog,
        thread_cls=FakeThread,
    ), logs


def test_start_requires_game_path(tmp_path):
    controller, _ = make_controller(
        tmp_path,
        cfg=config(tmp_path, game_pak_root=None, game_install_root=None),
    )

    controller.start()

    assert FakeDialog.warnings
    assert not FakeThread.instances


def test_cancelled_confirmation_does_not_start_thread(tmp_path):
    FakeDialog.question_result = QDialog.DialogCode.Rejected
    controller, _ = make_controller(tmp_path)

    controller.start()

    assert FakeDialog.questions
    assert not FakeThread.instances


def test_success_logs_progress_and_refreshes_runtime(tmp_path):
    refresh_calls = []
    controller, logs = make_controller(tmp_path, refresh_runtime=lambda: refresh_calls.append("refresh") or True)

    controller.start()

    assert FakeThread.instances[0].started is True
    assert FakeProgressDialog.instances[0].shown is True
    assert FakeProgressDialog.instances[0].closed is True
    assert FakeProgressDialog.instances[0].labels == ["halfway"]
    assert refresh_calls == ["refresh"]
    assert FakeDialog.information_calls[-1][1:] == ("成功", "done")
    assert "[DB UPDATE] halfway" in logs
    assert "[DB UPDATE] 成功：done" in logs


def test_update_failure_does_not_refresh_runtime(tmp_path):
    FakeThread.result = (False, "bad")
    refresh_calls = []
    controller, logs = make_controller(tmp_path, refresh_runtime=lambda: refresh_calls.append("refresh") or True)

    controller.start()

    assert refresh_calls == []
    assert FakeDialog.criticals[-1][1] == "失败"
    assert "[DB UPDATE] 失败：bad" in logs


def test_refresh_failure_warns_user(tmp_path):
    controller, logs = make_controller(tmp_path, refresh_runtime=lambda: False)

    controller.start()

    assert FakeDialog.warnings[-1][1] == "警告"
    assert "[DB UPDATE] 更新成功但刷新运行时资源失败：done" in logs
