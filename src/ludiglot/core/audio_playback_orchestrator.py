from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class AudioResolverProtocol(Protocol):
    def resolve(self, text_key: str | None, db_event: str | None = None, db_hash: int | None = None) -> Any: ...
    def get_cached_path(self, hash_value: int, event_name: str | None = None, *, trusted_only: bool = True) -> Path | None: ...
    def ensure_playable_audio(self, hash_value: int, text_key: str | None, event_name: str | None, log_callback: Any = None, skip_cache: bool = False) -> Path | None: ...


class AudioIndexProtocol(Protocol):
    def find(self, hash_value: int) -> Path | None: ...


@dataclass(frozen=True)
class AudioIntent:
    text_key: str | None
    db_event: str | None = None
    db_hash: int | str | None = None
    origin: str = "single"


@dataclass(frozen=True)
class AudioPlaybackIdentity:
    text_key: str | None
    hash_value: int
    event_name: str | None
    source_type: str


@dataclass(frozen=True)
class AudioPlaybackDecision:
    enabled: bool
    path: Path | None = None
    identity: AudioPlaybackIdentity | None = None
    status_message: str | None = None


class AudioPlaybackOrchestrator:
    def __init__(
        self,
        resolver: AudioResolverProtocol | None = None,
        audio_index: AudioIndexProtocol | None = None,
        log_callback: Any = None,
    ) -> None:
        self.resolver = resolver
        self.audio_index = audio_index
        self.log_callback = log_callback

    def resolve_intent(self, intent: AudioIntent) -> AudioPlaybackIdentity | None:
        if not intent.text_key:
            return None
        if self.resolver:
            resolution = self.resolver.resolve(intent.text_key, db_event=intent.db_event, db_hash=intent.db_hash)
            if resolution:
                return AudioPlaybackIdentity(
                    text_key=intent.text_key,
                    hash_value=int(resolution.hash_value),
                    event_name=resolution.event_name,
                    source_type=resolution.source_type,
                )
        if intent.db_hash is None:
            return None
        try:
            hash_value = int(intent.db_hash)
        except (TypeError, ValueError):
            return None
        return AudioPlaybackIdentity(
            text_key=intent.text_key,
            hash_value=hash_value,
            event_name=intent.db_event,
            source_type="db_fallback",
        )

    def prepare_playback(self, identity: AudioPlaybackIdentity) -> AudioPlaybackDecision:
        path: Path | None = None
        if self.resolver:
            resolution = self.resolver.resolve(identity.text_key, db_event=identity.event_name, db_hash=identity.hash_value)
            active = identity
            if resolution:
                active = AudioPlaybackIdentity(
                    text_key=identity.text_key,
                    hash_value=int(resolution.hash_value),
                    event_name=resolution.event_name,
                    source_type=resolution.source_type,
                )
                if resolution.source_type == "cache":
                    path = self.resolver.get_cached_path(
                        active.hash_value,
                        active.event_name,
                        trusted_only=True,
                    )
                elif resolution.source_type in {"wem", "bnk"}:
                    path = self.resolver.ensure_playable_audio(
                        active.hash_value,
                        active.text_key,
                        active.event_name,
                        log_callback=self.log_callback,
                        skip_cache=True,
                    )
            identity = active

        if path is None and self.resolver:
            path = self.resolver.get_cached_path(
                identity.hash_value,
                identity.event_name,
                trusted_only=True,
            )
        if path is None and self.audio_index:
            path = self.audio_index.find(identity.hash_value)

        if path is None and self.resolver:
            path = self.resolver.ensure_playable_audio(
                identity.hash_value,
                identity.text_key,
                identity.event_name,
                log_callback=self.log_callback,
                skip_cache=True,
            )

        if path is None:
            return AudioPlaybackDecision(
                enabled=False,
                identity=identity,
                status_message="未找到对应音频文件",
            )
        return AudioPlaybackDecision(enabled=True, path=path, identity=identity)

    def resolve_and_prepare(self, intent: AudioIntent) -> AudioPlaybackDecision:
        identity = self.resolve_intent(intent)
        if identity is None:
            return AudioPlaybackDecision(enabled=False, status_message="未找到对应音频文件")
        return self.prepare_playback(identity)
