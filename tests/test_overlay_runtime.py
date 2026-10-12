import json
from pathlib import Path

from ludiglot.core.config import AppConfig
from ludiglot.core import overlay_runtime
from ludiglot.core.overlay_runtime import (
    OverlayRuntimeCallbacks,
    create_overlay_ocr_engine,
    initialize_overlay_runtime,
)


class FakeEngine:
    def __init__(self):
        self.initialize_calls = 0
        self.prewarm_calls = []

    def initialize(self):
        self.initialize_calls += 1

    def prewarm(self, backend, async_=False):
        self.prewarm_calls.append((backend, async_))


class FakeOcrEngine:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.logger = None
        self.status = None
        FakeOcrEngine.instances.append(self)

    def set_logger(self, logger, status):
        self.logger = logger
        self.status = status


class FakeSkillParamResolver:
    available_value = True
    instances = []

    def __init__(self, db_path, logger=None):
        self.db_path = db_path
        self.logger = logger
        self.available = FakeSkillParamResolver.available_value
        FakeSkillParamResolver.instances.append(self)


class FakeVoiceEventIndex:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.names = {"vo_test"}
        self.load_calls = 0
        FakeVoiceEventIndex.instances.append(self)

    def load_or_build(self):
        self.load_calls += 1


class FakeAudioCacheIndex:
    instances = []

    def __init__(self, cache_dir, index_path=None, max_mb=2048):
        self.cache_dir = cache_dir
        self.index_path = index_path
        self.max_mb = max_mb
        self.entries = {123: object()}
        self.load_calls = 0
        self.scan_calls = 0
        FakeAudioCacheIndex.instances.append(self)

    def load(self):
        self.load_calls += 1

    def scan(self):
        self.scan_calls += 1


class FakeMatcher:
    instances = []

    def __init__(self, db, voice_map, voice_event_index, gender_preference="female"):
        self.db = db
        self.voice_map = voice_map
        self.voice_event_index = voice_event_index
        self.gender_preference = gender_preference
        self.logger = None
        FakeMatcher.instances.append(self)

    def set_logger(self, logger):
        self.logger = logger


class FakeAudioResolver:
    instances = []

    def __init__(self, config, voice_event_index=None, audio_index=None):
        self.config = config
        self.voice_event_index = voice_event_index
        self.audio_index = audio_index
        FakeAudioResolver.instances.append(self)


def config(tmp_path, **overrides):
    values = dict(
        data_root=None,
        en_json=tmp_path / "en.json",
        zh_json=tmp_path / "zh.json",
        db_path=tmp_path / "db.json",
        image_path=tmp_path / "capture.png",
        auto_rebuild_db=False,
    )
    values.update(overrides)
    return AppConfig(**values)


def callbacks():
    events = {"status": [], "log": [], "error": []}
    return events, OverlayRuntimeCallbacks(
        status=events["status"].append,
        log=events["log"].append,
        error=events["error"].append,
    )


def patch_lightweight_runtime(monkeypatch):
    FakeSkillParamResolver.instances = []
    FakeVoiceEventIndex.instances = []
    FakeAudioCacheIndex.instances = []
    FakeMatcher.instances = []
    FakeAudioResolver.instances = []
    monkeypatch.setattr(overlay_runtime, "SkillParamResolver", FakeSkillParamResolver)
    monkeypatch.setattr(overlay_runtime, "VoiceEventIndex", FakeVoiceEventIndex)
    monkeypatch.setattr(overlay_runtime, "AudioCacheIndex", FakeAudioCacheIndex)
    monkeypatch.setattr(overlay_runtime, "TextMatcher", FakeMatcher)
    monkeypatch.setattr(overlay_runtime, "AudioResolver", FakeAudioResolver)
    monkeypatch.setattr(overlay_runtime, "build_voice_map_from_configdb", lambda root, cache_path=None: {"Text": ["vo_test"]})
    monkeypatch.setattr(overlay_runtime, "collect_all_voice_event_names", lambda root, voice_map: ["vo_extra"])
    monkeypatch.setattr(overlay_runtime, "resolve_external_wem_root", lambda cfg: Path("external"))


def test_loads_existing_db_without_rebuild(monkeypatch, tmp_path):
    patch_lightweight_runtime(monkeypatch)
    (tmp_path / "db.json").write_text(json.dumps({"Text": {"cn": "译文"}}), encoding="utf-8")
    monkeypatch.setattr(overlay_runtime, "build_text_db_from_root_all", lambda root: (_ for _ in ()).throw(AssertionError("no rebuild")))
    monkeypatch.setattr(overlay_runtime, "build_text_db", lambda en, zh: (_ for _ in ()).throw(AssertionError("no rebuild")))
    events, cb = callbacks()

    result = initialize_overlay_runtime(config(tmp_path), FakeEngine(), cb)

    assert result.success is True
    assert result.resources.db == {"Text": {"cn": "译文"}}
    assert events["error"] == []


def test_rebuilds_db_from_data_root(monkeypatch, tmp_path):
    patch_lightweight_runtime(monkeypatch)
    data_root = tmp_path / "data"
    data_root.mkdir()
    saved = []
    monkeypatch.setattr(overlay_runtime, "build_text_db_from_root_all", lambda root: {"Built": {"cn": "构建"}})
    monkeypatch.setattr(overlay_runtime, "save_text_db", lambda db, path: (saved.append((db, path)), path.write_text(json.dumps(db), encoding="utf-8")))
    events, cb = callbacks()

    result = initialize_overlay_runtime(config(tmp_path, data_root=data_root, auto_rebuild_db=True), FakeEngine(), cb)

    assert result.success is True
    assert result.resources.db == {"Built": {"cn": "构建"}}
    assert saved == [({"Built": {"cn": "构建"}}, tmp_path / "db.json")]
    assert "构建文本数据库…" in events["status"]


def test_missing_db_and_source_reports_failure(tmp_path):
    events, cb = callbacks()

    result = initialize_overlay_runtime(config(tmp_path), FakeEngine(), cb)

    assert result.success is False
    assert result.resources is None
    assert events["error"]
    assert events["error"][0].startswith("DB 初始化失败:")


def test_runtime_wires_optional_services(monkeypatch, tmp_path):
    patch_lightweight_runtime(monkeypatch)
    data_root = tmp_path / "data"
    data_root.mkdir()
    (tmp_path / "db.json").write_text(json.dumps({"Text": {"cn": "译文"}}), encoding="utf-8")
    events, cb = callbacks()
    cfg = config(
        tmp_path,
        data_root=data_root,
        audio_cache_path=tmp_path / "audio",
        audio_cache_index_path=tmp_path / "audio_index.json",
        audio_bnk_root=tmp_path / "bnk",
        audio_txtp_cache=tmp_path / "txtp",
        gender_preference="male",
    )

    result = initialize_overlay_runtime(cfg, FakeEngine(), cb)

    assert result.success is True
    resources = result.resources
    assert resources.skill_param_resolver is FakeSkillParamResolver.instances[0]
    assert resources.voice_map == {"Text": ["vo_test"]}
    assert resources.voice_event_index is FakeVoiceEventIndex.instances[0]
    assert FakeVoiceEventIndex.instances[0].kwargs == {
        "bnk_root": tmp_path / "bnk",
        "txtp_root": tmp_path / "txtp",
        "cache_path": tmp_path / "audio" / "voice_event_index.json",
        "extra_names": ["vo_extra"],
    }
    assert FakeVoiceEventIndex.instances[0].load_calls == 1
    assert resources.audio_index is FakeAudioCacheIndex.instances[0]
    assert FakeAudioCacheIndex.instances[0].load_calls == 1
    assert FakeAudioCacheIndex.instances[0].scan_calls == 1
    assert resources.matcher is FakeMatcher.instances[0]
    assert resources.matcher.gender_preference == "male"
    assert resources.audio_resolver is FakeAudioResolver.instances[0]
    assert resources.audio_resolver.audio_index is resources.audio_index
    assert resources.external_wem_root == Path("external")
    assert "[PARAM] 技能参数解析器已启用: " + str(data_root / "ConfigDB" / "db_skill.db") in events["log"]


def test_unavailable_skill_resolver_is_not_attached(monkeypatch, tmp_path):
    patch_lightweight_runtime(monkeypatch)
    FakeSkillParamResolver.available_value = False
    data_root = tmp_path / "data"
    data_root.mkdir()
    (tmp_path / "db.json").write_text(json.dumps({"Text": {}}), encoding="utf-8")

    try:
        result = initialize_overlay_runtime(config(tmp_path, data_root=data_root), FakeEngine(), callbacks()[1])
    finally:
        FakeSkillParamResolver.available_value = True

    assert result.success is True
    assert result.resources.skill_param_resolver is None


def test_create_overlay_ocr_engine_applies_config(monkeypatch, tmp_path):
    FakeOcrEngine.instances = []
    monkeypatch.setattr(overlay_runtime, "OCREngine", FakeOcrEngine)
    events, cb = callbacks()
    cfg = config(
        tmp_path,
        ocr_lang="zh",
        ocr_gpu=True,
        ocr_mode="gpu",
        ocr_backend="windows",
        ocr_line_refine=True,
        ocr_preprocess=True,
        ocr_word_segment=True,
        ocr_multiscale=True,
        ocr_adaptive=False,
    )

    engine = create_overlay_ocr_engine(cfg, cb)

    assert engine is FakeOcrEngine.instances[0]
    assert engine.kwargs == {
        "lang": "zh",
        "use_gpu": True,
        "mode": "gpu",
    }
    engine.logger("log-message")
    engine.status("status-message")
    assert events["log"] == ["log-message"]
    assert events["status"] == ["status-message"]
    assert engine.win_ocr_line_refine is True
    assert engine.win_ocr_preprocess is True
    assert engine.win_ocr_segment is True
    assert engine.win_ocr_multiscale is True
    assert engine.win_ocr_adaptive is False
