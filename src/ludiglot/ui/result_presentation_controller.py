from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ludiglot.core.display_shaper import (
    DisplayAudioCandidate,
    DisplayPreferences,
    TranslationDisplayModel,
    shape_translation_display,
)


@dataclass(frozen=True)
class CurrentDisplayState:
    source_text: str | None = None
    target_text: str | None = None
    source_is_html: bool = False
    target_is_html: bool = False

    @classmethod
    def from_model(cls, model: TranslationDisplayModel) -> "CurrentDisplayState":
        return cls(
            source_text=model.source.display_text,
            target_text=model.target.display_text,
            source_is_html=model.source.is_html,
            target_is_html=model.target.is_html,
        )


class ResultAudioPort(Protocol):
    @property
    def has_current_audio(self) -> bool: ...
    def stop(self, emit_status: bool = True) -> None: ...
    def clear_candidate(self) -> None: ...
    def load_result_candidate(self, candidate: DisplayAudioCandidate | None, *, is_multi: bool) -> bool: ...
    def play_current(self) -> None: ...


class ResultPresentationView(Protocol):
    def apply_display_state(self, state: CurrentDisplayState, preferences: DisplayPreferences) -> None: ...
    def refresh_font_settings(self, state: CurrentDisplayState, preferences: DisplayPreferences) -> None: ...
    def activate_for_result(self, *, is_multi: bool) -> None: ...


class ResultPresentationController:
    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        preferences_provider: Callable[[], DisplayPreferences],
        param_resolver_provider: Callable[[], Any | None],
        title_resolver: Callable[[str], str | None],
        voice_map_provider: Callable[[], dict[str, Any]],
        voice_event_index_provider: Callable[[], Any | None],
        audio: ResultAudioPort,
        view: ResultPresentationView,
        log: Callable[[str], None],
        error: Callable[[str], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config_provider = config_provider
        self._preferences_provider = preferences_provider
        self._param_resolver_provider = param_resolver_provider
        self._title_resolver = title_resolver
        self._voice_map_provider = voice_map_provider
        self._voice_event_index_provider = voice_event_index_provider
        self._audio = audio
        self._view = view
        self._log = log
        self._error = error
        self._clock = clock
        self._current_display_state = CurrentDisplayState()

    @property
    def current_display_state(self) -> CurrentDisplayState:
        return self._current_display_state

    def present_result(self, result: dict[str, Any]) -> None:
        t_show_start = self._clock()
        self._log("[DEBUG] _show_result called")
        self._log("[PERF] _show_result 开始")

        try:
            self._audio.stop(emit_status=False)
            self._audio.clear_candidate()

            preferences = self._preferences_provider()
            model = shape_translation_display(
                result,
                preferences=preferences,
                param_resolver=self._param_resolver_provider(),
                title_resolver=self._title_resolver,
                voice_map=self._voice_map_provider(),
                voice_event_index=self._voice_event_index_provider(),
            )

            self._log("[WINDOW] 设置文本内容")
            self._current_display_state = CurrentDisplayState.from_model(model)
            self._view.apply_display_state(self._current_display_state, preferences)

            for line in model.log_lines:
                self._log(line)

            t_audio = self._clock()
            has_audio = self._audio.load_result_candidate(model.audio_candidate, is_multi=model.is_multi)
            self._log(f"[PERF] 音频解析: {(self._clock()-t_audio)*1000:.1f}ms")

            self._view.activate_for_result(is_multi=model.is_multi)

            if getattr(self._config_provider(), "play_audio", False) and has_audio and self._audio.has_current_audio:
                self._log("[DEBUG] Calling play_audio...")
                self._audio.play_current()
                self._log("[DEBUG] play_audio returned.")

            self._log(f"[PERF] _show_result 总耗时: {(self._clock()-t_show_start)*1000:.1f}ms")
        except Exception as exc:
            self._error(f"显示结果失败: {exc}")
            self._log(f"[ERROR] {traceback.format_exc()}")

    def refresh_font_settings(self) -> None:
        self._view.refresh_font_settings(self._current_display_state, self._preferences_provider())
