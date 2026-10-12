from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from ludiglot.core.overlay_runtime import OverlayRuntimeCallbacks
from ludiglot.core.preferences import ConfigJsonStore
from ludiglot.ui.audio_controls_presenter import AudioControlsPresenter
from ludiglot.ui.audio_playback_ui_controller import AudioPlaybackUiController
from ludiglot.ui.capture_session import CaptureSessionCallbacks, OverlayCaptureSession
from ludiglot.ui.hotkey_registrar import HotkeyRegistrar, HotkeyRegistrarCallbacks
from ludiglot.ui.pynput_hotkey_adapter import PynputGlobalHotkeyAdapter
from ludiglot.ui.qt_audio_controls_adapter import QtAudioControlsAdapter
from ludiglot.infrastructure.qt_audio_player import AudioPlayer
from ludiglot.ui.qt_capture_adapter import QtCaptureAdapter
from ludiglot.ui.qt_hotkey_adapter import WindowsNativeHotkeyAdapter
from ludiglot.ui.qt_result_presentation_adapter import QtResultPresentationAdapter
from ludiglot.ui.result_presentation_controller import ResultPresentationController


def create_runtime_callbacks(window: Any) -> OverlayRuntimeCallbacks:
    return OverlayRuntimeCallbacks(
        status=window.signals.status.emit,
        log=window.signals.log.emit,
        error=window.signals.error.emit,
    )


def install_hotkey_registrar(window: Any) -> None:
    window._hotkeys = HotkeyRegistrar(
        config_provider=lambda: window.config,
        callbacks=HotkeyRegistrarCallbacks(
            capture=lambda: window.capture_requested.emit(True),
            toggle=window._toggle_visibility,
            log=window.signals.log.emit,
            error=window.signals.error.emit,
            exception=lambda: window.exception_capture_requested.emit(False),
            exception_select=lambda: window.exception_capture_requested.emit(True),
        ),
        primary_adapter=WindowsNativeHotkeyAdapter(application_provider=QApplication.instance),
        fallback_adapter=PynputGlobalHotkeyAdapter(),
    )


def install_audio_player(window: Any) -> None:
    window.player = AudioPlayer()


def install_audio_playback_controls(window: Any) -> None:
    # 音频进度更新定时器
    window.audio_timer = QTimer(window)
    window.audio_timer.timeout.connect(window._update_audio_progress)
    window.audio_timer.setInterval(100)  # 每100ms更新一次

    window.audio_controls_adapter = QtAudioControlsAdapter(
        play_pause_button=window.play_pause_btn,
        slider=window.audio_slider,
        time_label=window.time_label,
        timer=window.audio_timer,
        status=window.signals.status.emit,
    )
    window.audio_ui = AudioPlaybackUiController(
        config_provider=lambda: window.config,
        runtime_provider=lambda: window.audio_runtime,
        player=window.player,
        controls=window.audio_controls_adapter,
        presenter=AudioControlsPresenter(),
        status=window.signals.status.emit,
        log=window.signals.log.emit,
        error=window.signals.error.emit,
        audio_index_updated=window._set_audio_index,
    )


def install_result_presentation(window: Any) -> None:
    window.result_presentation_view = QtResultPresentationAdapter(
        source_editor=window.source_label,
        target_editor=window.cn_label,
        show_single_result=window.show_without_activating,
        show_multi_result=window.show_without_activating,
    )
    window.result_presentation = ResultPresentationController(
        config_provider=lambda: window.config,
        preferences_provider=window._display_preferences,
        param_resolver_provider=lambda: window.skill_param_resolver,
        title_resolver=window._translate_title,
        voice_map_provider=lambda: window.voice_map,
        voice_event_index_provider=lambda: window.voice_event_index,
        audio=window.audio_ui,
        view=window.result_presentation_view,
        log=window.signals.log.emit,
        error=window.signals.error.emit,
    )


def install_capture_session(window: Any) -> None:
    def save_exception_region(region) -> None:
        payload = {
            "left": int(region.left),
            "top": int(region.top),
            "width": int(region.width),
            "height": int(region.height),
        }
        window.config.exception_capture_region = payload
        ConfigJsonStore(window._config_path).update_values(exception_capture_region=payload)

    window.capture_adapter = QtCaptureAdapter(window.config, log=window.signals.log.emit)
    window.capture_session = OverlayCaptureSession(
        config_provider=lambda: window.config,
        ocr_engine_provider=lambda: window.engine,
        matcher_provider=lambda: window.matcher,
        capture_adapter=window.capture_adapter,
        callbacks=CaptureSessionCallbacks(
            status=window.signals.status.emit,
            log=window.signals.log.emit,
            error=window.signals.error.emit,
            result=window.signals.result.emit,
        ),
        stop_audio=window.stop_audio,
        clear_result_audio_state=window._clear_capture_audio_state,
        exception_region_saved=save_exception_region,
    )


def connect_overlay_composition_signals(window: Any) -> None:
    window.capture_requested.connect(window.capture_session.trigger)
    window.exception_capture_requested.connect(window.capture_session.trigger_exception)
    window.resources_initialized.connect(window._on_runtime_resources_initialized)


def install_app_event_filter(window: Any) -> None:
    app = QApplication.instance()
    if app:
        app.installEventFilter(window)


def install_sync_timer(window: Any) -> None:
    window._sync_config_timer = QTimer(window)
    window._sync_config_timer.timeout.connect(window._persist_window_position)
    window._sync_config_timer.start(5000)  # 每 5 秒同步一次
