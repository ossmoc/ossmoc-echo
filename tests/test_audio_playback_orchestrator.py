from pathlib import Path
from typing import NamedTuple

from ludiglot.core.audio_playback_orchestrator import (
    AudioIntent,
    AudioPlaybackIdentity,
    AudioPlaybackOrchestrator,
)


class Resolution(NamedTuple):
    hash_value: int
    event_name: str
    source_type: str


class FakeResolver:
    def __init__(self, resolution=None, cached_path=None, ensured_path=None):
        self.resolution = resolution
        self.cached_path = cached_path
        self.ensured_path = ensured_path
        self.resolve_calls = []
        self.cache_calls = []
        self.ensure_calls = []

    def resolve(self, text_key, db_event=None, db_hash=None):
        self.resolve_calls.append((text_key, db_event, db_hash))
        return self.resolution

    def get_cached_path(self, hash_value, event_name=None, *, trusted_only=True):
        self.cache_calls.append((hash_value, event_name, trusted_only))
        return self.cached_path

    def ensure_playable_audio(self, hash_value, text_key, event_name, log_callback=None, skip_cache=False):
        self.ensure_calls.append((hash_value, text_key, event_name, skip_cache))
        return self.ensured_path


class FakeIndex:
    def __init__(self, path=None):
        self.path = path
        self.find_calls = []

    def find(self, hash_value):
        self.find_calls.append(hash_value)
        return self.path


def test_resolve_intent_uses_resolver_resolution():
    resolver = FakeResolver(Resolution(123, "vo_test", "cache"))

    identity = AudioPlaybackOrchestrator(resolver).resolve_intent(AudioIntent("Text", "db_event", "99"))

    assert identity == AudioPlaybackIdentity("Text", 123, "vo_test", "cache")
    assert resolver.resolve_calls == [("Text", "db_event", "99")]


def test_resolve_intent_falls_back_to_db_hash_without_resolver():
    identity = AudioPlaybackOrchestrator(None).resolve_intent(AudioIntent("Text", "vo_db", "456"))

    assert identity == AudioPlaybackIdentity("Text", 456, "vo_db", "db_fallback")


def test_prepare_playback_prefers_trusted_cache_for_cache_resolution(tmp_path):
    wav = tmp_path / "123.wav"
    resolver = FakeResolver(Resolution(123, "vo_test", "cache"), cached_path=wav)

    decision = AudioPlaybackOrchestrator(resolver).prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "cache"))

    assert decision.enabled is True
    assert decision.path == wav
    assert resolver.cache_calls == [(123, "vo_test", True)]
    assert resolver.ensure_calls == []


def test_prepare_playback_ensures_wem_source(tmp_path):
    wav = tmp_path / "123.wav"
    resolver = FakeResolver(Resolution(123, "vo_test", "wem"), ensured_path=wav)

    decision = AudioPlaybackOrchestrator(resolver).prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "wem"))

    assert decision.enabled is True
    assert decision.path == wav
    assert resolver.ensure_calls == [(123, "Text", "vo_test", True)]


def test_prepare_playback_uses_index_when_no_resolver(tmp_path):
    wav = tmp_path / "123.wav"
    index = FakeIndex(wav)

    decision = AudioPlaybackOrchestrator(None, index).prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "db_fallback"))

    assert decision.enabled is True
    assert decision.path == wav
    assert index.find_calls == [123]


def test_prepare_playback_uses_index_after_resolver_cache_miss(tmp_path):
    wav = tmp_path / "123.wav"
    resolver = FakeResolver(Resolution(123, "vo_test", "computed"), cached_path=None, ensured_path=None)
    index = FakeIndex(wav)

    decision = AudioPlaybackOrchestrator(resolver, index).prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "computed"))

    assert decision.enabled is True
    assert decision.path == wav
    assert index.find_calls == [123]
    assert resolver.ensure_calls == []


def test_prepare_playback_reports_missing_audio():
    resolver = FakeResolver(Resolution(123, "vo_test", "computed"), cached_path=None, ensured_path=None)

    decision = AudioPlaybackOrchestrator(resolver).prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "computed"))

    assert decision.enabled is False
    assert decision.path is None
    assert decision.status_message == "未找到对应音频文件"
    assert resolver.ensure_calls == [(123, "Text", "vo_test", True)]
