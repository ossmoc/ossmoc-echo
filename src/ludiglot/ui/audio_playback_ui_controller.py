from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Protocol

from ludiglot.core.audio_player import AudioPlayerProtocol
from ludiglot.core.audio_playback_orchestrator import AudioIntent, AudioPlaybackIdentity
from ludiglot.core.display_shaper import DisplayAudioCandidate
from ludiglot.ui.audio_controls_presenter import AudioControlsPresenter, AudioControlsViewState


class AudioControlsAdapter(Protocol):
    def apply(self, state: AudioControlsViewState) -> None: ...
    def is_dragging(self) -> bool: ...



class AudioPlaybackUiController:
    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        runtime_provider: Callable[[], Any | None],
        player: AudioPlayerProtocol,
        controls: AudioControlsAdapter,
        presenter: AudioControlsPresenter,
        status: Callable[[str], None],
        log: Callable[[str], None],
        error: Callable[[str], None],
        audio_index_updated: Callable[[Any], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config_provider = config_provider
        self._runtime_provider = runtime_provider
        self._player = player
        self._controls = controls
        self._presenter = presenter
        self._status = status
        self._log = log
        self._error = error
        self._audio_index_updated = audio_index_updated
        self._clock = clock
        self._current_identity: AudioPlaybackIdentity | None = None
        self._last_seek_time: float | None = None
        self._current_source_name: str | None = None

    @property
    def has_current_audio(self) -> bool:
        return self._current_identity is not None

    @property
    def current_identity(self) -> AudioPlaybackIdentity | None:
        return self._current_identity

    def clear_candidate(self) -> None:
        self._current_identity = None
        self._current_source_name = None
        self._last_seek_time = None
        self._controls.apply(self._presenter.disabled())

    def stop(self, emit_status: bool = True) -> None:
        self._player.stop()
        self._controls.apply(self._presenter.disabled())
        if emit_status:
            self._status("已停止播放")

    def load_result_candidate(self, candidate: DisplayAudioCandidate | None, *, is_multi: bool) -> bool:
        self._current_identity = None
        self._current_source_name = None
        if candidate is None:
            if is_multi:
                self._log("[WINDOW] 禁用音频控件（多条目模式）")
            self._controls.apply(self._presenter.disabled())
            return False

        if candidate.origin == "multi":
            self._log("[WINDOW] 多条目模式：检测到高置信度音频，启用音频控件")
            return self._load_intent(AudioIntent(text_key=candidate.text_key, origin="multi"), log_prefix="[AUDIO]")

        intent = AudioIntent(
            text_key=candidate.text_key,
            db_event=candidate.db_event,
            db_hash=candidate.db_hash,
            origin=candidate.origin,
        )
        return self._load_intent(intent, log_prefix="[MATCH]")

    def play_for_key(self, text_key: str) -> None:
        if not text_key:
            return
        if self._load_intent(AudioIntent(text_key=text_key, origin="multi"), log_prefix="[AUDIO]"):
            if getattr(self._config_provider(), "play_audio", False):
                self.play_current()

    def play_current(self) -> None:
        config = self._config_provider()
        runtime = self._runtime_provider()
        if self._current_identity is None or not getattr(config, "audio_cache_path", None) or not runtime:
            return

        try:
            print("[DEBUG] play_audio started", flush=True)
            decision = runtime.prepare_playback(self._current_identity)
            if self._audio_index_updated is not None:
                self._audio_index_updated(getattr(runtime, "audio_index", None))

            if decision.identity:
                self._current_identity = decision.identity
                print(f"[DEBUG] play_audio resolved: {decision.identity.source_type}", flush=True)

            if decision.path is None:
                self.stop(emit_status=False)
                self._status(decision.status_message or "未找到对应音频文件")
                print("[DEBUG] play_audio: path not found", flush=True)
                return

            self._current_source_name = decision.path.name
            self._status(f"正在播放: {decision.path.name}")
            print(f"[DEBUG] Invoking self.player.play: {decision.path}", flush=True)
            self._player.play(str(decision.path), block=False)
            self._controls.apply(self._presenter.playing(decision.path.name))
        except Exception as exc:
            print(f"[ERROR] play_audio crashed: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            self._error(f"播放失败: {exc}")

    def toggle(self) -> None:
        if self._player.is_playing():
            self._player.pause()
            self._controls.apply(self._presenter.paused(self._player.get_position(), self._player.get_duration()))
            return

        if self._player.get_position() >= 0.99:
            self._player.seek(0.0)
        self._player.resume()
        self._controls.apply(
            self._presenter.playing(
                self._current_source_name or self._player_source_name(),
                progress=self._player.get_position(),
                duration_ms=self._player.get_duration(),
            )
        )

    def seek_started(self) -> None:
        pass

    def seek_finished(self, position: float) -> None:
        self._last_seek_time = self._clock()
        was_playing = self._player.is_playing()
        self._player.seek(position)
        duration = self._player.get_duration()
        if duration > 0:
            if was_playing:
                self._controls.apply(self._presenter.progress(position, duration))
            else:
                self._controls.apply(self._presenter.seeked(position, duration))

    def update_progress(self) -> None:
        if self._last_seek_time is not None and self._clock() - self._last_seek_time < 0.2:
            return

        is_natural_end = self._has_reached_end()
        if is_natural_end:
            self._player.stop()

        if not self._player.is_playing() or is_natural_end:
            duration = self._player.get_duration()
            if duration > 0:
                state = self._presenter.ended(duration)
                if not is_natural_end:
                    state = replace(state, status_message=None)
                self._controls.apply(state)
            else:
                self._controls.apply(self._presenter.disabled())
            return

        position = self._player.get_position()
        duration = self._player.get_duration()
        if duration > 0:
            state = self._presenter.progress(
                position,
                duration,
                update_progress=not self._controls.is_dragging(),
            )
            self._controls.apply(state)

    def _load_intent(self, intent: AudioIntent, *, log_prefix: str) -> bool:
        runtime = self._runtime_provider()
        identity = runtime.resolve_intent(intent) if runtime else None
        if identity:
            self._current_identity = identity
            if log_prefix == "[MATCH]" and identity.source_type == "db_fallback":
                self._log(f"[MATCH] text_key={intent.text_key} 使用数据库哈希={identity.hash_value}")
            else:
                self._log(f"{log_prefix} text_key={intent.text_key} hash={identity.hash_value} ({identity.source_type})")
            self._controls.apply(self._presenter.ready())
            return True
        if log_prefix == "[AUDIO]":
            self._log(f"[AUDIO] text_key={intent.text_key} 未找到对应音频，跳过播放")
        else:
            self._log(f"[MATCH] text_key={intent.text_key} 未找到对应音频")
        self._controls.apply(self._presenter.disabled())
        return False

    def _has_reached_end(self) -> bool:
        try:
            return bool(self._player.has_reached_end())
        except Exception:
            return False

    def _player_source_name(self) -> str | None:
        try:
            return self._player.current_source_name()
        except Exception:
            return None
