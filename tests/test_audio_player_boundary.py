from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_AUDIO_PLAYER = PROJECT_ROOT / "src" / "ludiglot" / "core" / "audio_player.py"
QT_AUDIO_PLAYER = PROJECT_ROOT / "src" / "ludiglot" / "infrastructure" / "qt_audio_player.py"
OLD_UI_QT_AUDIO_PLAYER = PROJECT_ROOT / "src" / "ludiglot" / "ui" / "qt_audio_player.py"


def test_core_audio_player_has_no_pyqt_dependency():
    source = CORE_AUDIO_PLAYER.read_text(encoding="utf-8")

    assert "PyQt6" not in source
    assert "QMediaPlayer" not in source
    assert "QApplication" not in source


def test_infrastructure_qt_audio_player_owns_pyqt_playback_implementation():
    source = QT_AUDIO_PLAYER.read_text(encoding="utf-8")

    assert "class AudioPlayer" in source
    assert "PyQt6" in source
    assert "QMediaPlayer" in source


def test_ui_qt_audio_player_module_was_removed():
    assert not OLD_UI_QT_AUDIO_PLAYER.exists()
