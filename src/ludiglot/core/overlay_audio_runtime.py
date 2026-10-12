from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ludiglot.core.audio_mapper import AudioCacheIndex
from ludiglot.core.audio_playback_orchestrator import (
    AudioIntent,
    AudioPlaybackDecision,
    AudioPlaybackIdentity,
    AudioPlaybackOrchestrator,
    AudioResolverProtocol,
)
from ludiglot.core.config import AppConfig


class OverlayAudioRuntime:
    def __init__(
        self,
        config: AppConfig,
        resolver: AudioResolverProtocol | None = None,
        audio_index: AudioCacheIndex | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.resolver = resolver
        self._audio_index = audio_index
        self.log_callback = log_callback
        if self.resolver is not None and self._audio_index is not None and hasattr(self.resolver, "_audio_index"):
            self.resolver._audio_index = self._audio_index
        self._orchestrator = AudioPlaybackOrchestrator(resolver, audio_index, log_callback)

    @property
    def audio_index(self) -> AudioCacheIndex | None:
        return self._audio_index

    def resolve_intent(self, intent: AudioIntent) -> AudioPlaybackIdentity | None:
        return self._orchestrator.resolve_intent(intent)

    def prepare_playback(self, identity: AudioPlaybackIdentity) -> AudioPlaybackDecision:
        self._ensure_audio_index()
        return self._orchestrator.prepare_playback(identity)

    def _ensure_audio_index(self) -> AudioCacheIndex | None:
        if self._audio_index is not None:
            return self._audio_index
        if not self.config.audio_cache_path:
            return None
        self._audio_index = AudioCacheIndex(
            self.config.audio_cache_path,
            index_path=self.config.audio_cache_index_path,
            max_mb=self.config.audio_cache_max_mb,
        )
        self._audio_index.load()
        self._audio_index.scan()
        if self.resolver is not None and hasattr(self.resolver, "_audio_index"):
            self.resolver._audio_index = self._audio_index
        self._orchestrator = AudioPlaybackOrchestrator(self.resolver, self._audio_index, self.log_callback)
        return self._audio_index
