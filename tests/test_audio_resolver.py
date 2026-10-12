from __future__ import annotations

import json
from pathlib import Path

from ludiglot.core.config import AppConfig
from ludiglot.core.audio_resolver import AudioResolver


def _config(tmp_path: Path, **overrides) -> AppConfig:
    cache = tmp_path / "cache"
    values = {
        "data_root": None,
        "en_json": tmp_path / "en.json",
        "zh_json": tmp_path / "zh.json",
        "db_path": tmp_path / "db.json",
        "image_path": tmp_path / "capture.png",
        "audio_cache_path": cache,
        "audio_cache_index_path": cache / "audio_index.json",
        "audio_wem_root": None,
        "audio_bnk_root": None,
        "audio_external_root": None,
        "audio_txtp_cache": None,
        "vgmstream_path": tmp_path / "vgmstream.exe",
        "wwiser_path": tmp_path / "wwiser.pyz",
        "gender_preference": "female",
        "scan_audio_on_start": True,
    }
    values.update(overrides)
    return AppConfig(**values)


def test_get_candidates_orders_gender_variants_by_preference(tmp_path: Path) -> None:
    female = AudioResolver(_config(tmp_path, gender_preference="female"))
    male = AudioResolver(_config(tmp_path, gender_preference="male"))

    female_candidates = female.get_candidates(None, "play_vo_rover_line")
    male_candidates = male.get_candidates(None, "play_vo_rover_line")

    assert female_candidates.index("play_vo_rover_line_f") < female_candidates.index("play_vo_rover_line_m")
    assert male_candidates.index("play_vo_rover_line_m") < male_candidates.index("play_vo_rover_line_f")


def test_get_cached_path_requires_trusted_event_metadata(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    wav = cache / "123.wav"
    wav.write_bytes(b"wav")

    cfg = _config(tmp_path)
    resolver = AudioResolver(cfg)

    assert resolver.get_cached_path(123, "play_vo_line", trusted_only=True) is None
    assert resolver.get_cached_path(123, "play_vo_line", trusted_only=False) == wav

    (cache / "audio_resolver_cache_meta.json").write_text(
        json.dumps(
            {
                "entries": {
                    "123": {
                        "event_name": "play_vo_line",
                        "source_type": "wem",
                        "updated_at": 1.0,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    trusted = AudioResolver(cfg)

    assert trusted.get_cached_path(123, "play_vo_line", trusted_only=True) == wav
    assert trusted.get_cached_path(123, "other_event", trusted_only=True) is None


def test_resolve_returns_none_for_text_key_without_explicit_audio_mapping(tmp_path: Path) -> None:
    resolver = AudioResolver(_config(tmp_path))

    assert resolver.resolve("MENU_ITEM_WITHOUT_AUDIO") is None


def test_ensure_playable_audio_does_not_fallback_to_similar_event(tmp_path: Path, monkeypatch) -> None:
    class FakeVoiceEventIndex:
        def find_candidates(self, text_key, voice_event, limit=8, min_score=0.65):
            return ["play_vo_wrong_line"]

    cfg = _config(
        tmp_path,
        audio_wem_root=tmp_path / "wem_root",
        audio_bnk_root=tmp_path / "bnk_root",
        audio_txtp_cache=tmp_path / "txtp",
    )
    resolver = AudioResolver(cfg, voice_event_index=FakeVoiceEventIndex())
    bnk = tmp_path / "wrong.bnk"
    bnk.write_bytes(b"bnk")

    monkeypatch.setattr("ludiglot.core.audio_resolver.find_wem_by_hash", lambda root, hash_value: None)
    monkeypatch.setattr("ludiglot.core.audio_resolver.find_wem_by_event_name", lambda root, event_name: None)
    monkeypatch.setattr(
        "ludiglot.core.audio_resolver.find_bnk_for_event",
        lambda root, event_name: bnk if event_name == "play_vo_wrong_line" else None,
    )
    monkeypatch.setattr("ludiglot.core.audio_resolver.find_txtp_for_event", lambda root, event_name, hash_value=None: None)

    path = resolver.ensure_playable_audio(456, "MISSING_TEXT_KEY", "play_vo_missing_line")

    assert path is None


def test_ensure_playable_audio_converts_wem_and_marks_cache_trusted(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    wem = tmp_path / "source.wem"
    wem.write_bytes(b"wem")
    cfg = _config(tmp_path, audio_wem_root=tmp_path / "wem_root")
    resolver = AudioResolver(cfg)

    monkeypatch.setattr("ludiglot.core.audio_resolver.find_wem_by_hash", lambda root, hash_value: wem)
    monkeypatch.setattr("ludiglot.core.audio_resolver.find_wem_by_event_name", lambda root, event_name: None)

    def fake_convert(wem_path, vgmstream_path, output_dir, output_name=None, skip_existing=True):
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "converted.wav"
        path.write_bytes(b"wav")
        return path

    monkeypatch.setattr("ludiglot.core.audio_resolver.convert_single_wem_to_wav", fake_convert)

    path = resolver.ensure_playable_audio(456, "TEXT_KEY", "play_vo_line")

    assert path == cache / "456.wav"
    assert path.exists()
    assert resolver.get_cached_path(456, "play_vo_line") == path


def test_ensure_playable_audio_uses_bnk_txtp_fallback(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    wem_root = tmp_path / "wem_root"
    bnk_root = tmp_path / "bnk_root"
    txtp_cache = tmp_path / "txtp"
    bnk = tmp_path / "voice.bnk"
    txtp = tmp_path / "voice.txtp"
    bnk.write_bytes(b"bnk")
    cfg = _config(
        tmp_path,
        audio_wem_root=wem_root,
        audio_bnk_root=bnk_root,
        audio_txtp_cache=txtp_cache,
    )
    resolver = AudioResolver(cfg)

    monkeypatch.setattr("ludiglot.core.audio_resolver.find_wem_by_hash", lambda root, hash_value: None)
    monkeypatch.setattr("ludiglot.core.audio_resolver.find_wem_by_event_name", lambda root, event_name: None)
    monkeypatch.setattr("ludiglot.core.audio_resolver.find_bnk_for_event", lambda root, event_name: bnk)

    def fake_generate(bnk_path, wem_path, output_dir, wwiser_path, log_callback=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        txtp.write_bytes(b"txtp")
        return [txtp]

    def fake_find_txtp(root, event_name, hash_value=None):
        return txtp if txtp.exists() else None

    def fake_convert(txtp_path, vgmstream_path, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr("ludiglot.core.audio_resolver.generate_txtp_for_bnk", fake_generate)
    monkeypatch.setattr("ludiglot.core.audio_resolver.find_txtp_for_event", fake_find_txtp)
    monkeypatch.setattr("ludiglot.core.audio_resolver.convert_txtp_to_wav", fake_convert)

    path = resolver.ensure_playable_audio(789, "TEXT_KEY", "play_vo_line")

    assert path == cache / "789.wav"
    assert path.exists()
    assert resolver.get_cached_path(789, "play_vo_line") == path
