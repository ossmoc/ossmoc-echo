from pathlib import Path

from ludiglot.core.audio_playback_orchestrator import AudioPlaybackIdentity
from ludiglot.core.config import AppConfig
from ludiglot.core import overlay_audio_runtime
from ludiglot.core.overlay_audio_runtime import OverlayAudioRuntime


class Resolution:
    def __init__(self, hash_value=123, event_name="vo_test", source_type="computed"):
        self.hash_value = hash_value
        self.event_name = event_name
        self.source_type = source_type


class FakeResolver:
    def __init__(self, cached_path=None, ensured_path=None):
        self._audio_index = None
        self.cached_path = cached_path
        self.ensured_path = ensured_path
        self.resolve_calls = []
        self.cache_calls = []
        self.ensure_calls = []

    def resolve(self, text_key, db_event=None, db_hash=None):
        self.resolve_calls.append((text_key, db_event, db_hash))
        return Resolution(int(db_hash or 123), db_event or "vo_test", "computed")

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


class FakeAudioCacheIndex:
    created = []

    def __init__(self, cache_dir, index_path=None, max_mb=2048):
        self.cache_dir = cache_dir
        self.index_path = index_path
        self.max_mb = max_mb
        self.load_calls = 0
        self.scan_calls = 0
        self.entries = {}
        FakeAudioCacheIndex.created.append(self)

    def load(self):
        self.load_calls += 1

    def scan(self):
        self.scan_calls += 1

    def find(self, hash_value):
        return None


def config(tmp_path, **overrides):
    values = dict(
        data_root=None,
        en_json=tmp_path / "en.json",
        zh_json=tmp_path / "zh.json",
        db_path=tmp_path / "db.json",
        image_path=tmp_path / "capture.png",
        audio_cache_path=tmp_path / "audio",
        audio_cache_index_path=tmp_path / "audio_index.json",
    )
    values.update(overrides)
    return AppConfig(**values)


def test_uses_provided_audio_index_without_creating_new_one(monkeypatch, tmp_path):
    monkeypatch.setattr(overlay_audio_runtime, "AudioCacheIndex", FakeAudioCacheIndex)
    resolver = FakeResolver(ensured_path=tmp_path / "123.wav")
    index = FakeIndex()

    runtime = OverlayAudioRuntime(config(tmp_path), resolver, index)
    decision = runtime.prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "computed"))

    assert decision.path == tmp_path / "123.wav"
    assert runtime.audio_index is index
    assert resolver._audio_index is index
    assert FakeAudioCacheIndex.created == []


def test_lazily_creates_audio_index_once(monkeypatch, tmp_path):
    FakeAudioCacheIndex.created = []
    monkeypatch.setattr(overlay_audio_runtime, "AudioCacheIndex", FakeAudioCacheIndex)
    resolver = FakeResolver()
    runtime = OverlayAudioRuntime(config(tmp_path), resolver, None)

    runtime.prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "computed"))
    runtime.prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "computed"))

    assert len(FakeAudioCacheIndex.created) == 1
    created = FakeAudioCacheIndex.created[0]
    assert created.cache_dir == tmp_path / "audio"
    assert created.index_path == tmp_path / "audio_index.json"
    assert created.load_calls == 1
    assert created.scan_calls == 1
    assert runtime.audio_index is created
    assert resolver._audio_index is created


def test_missing_cache_path_leaves_index_absent(tmp_path):
    runtime = OverlayAudioRuntime(config(tmp_path, audio_cache_path=None), None, None)

    decision = runtime.prepare_playback(AudioPlaybackIdentity("Text", 123, "vo_test", "computed"))

    assert runtime.audio_index is None
    assert decision.enabled is False
    assert decision.path is None
    assert decision.status_message == "未找到对应音频文件"
