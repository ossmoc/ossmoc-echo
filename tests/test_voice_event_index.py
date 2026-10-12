from __future__ import annotations

import json
import os
from pathlib import Path

from ludiglot.core.voice_event_index import VoiceEventIndex


def test_load_or_build_uses_fresh_cache(tmp_path: Path) -> None:
    bnk_root = tmp_path / "bnk"
    bnk_root.mkdir()
    bnk = bnk_root / "newer_source.bnk"
    bnk.write_bytes(b"bnk")
    os.utime(bnk, (10, 10))
    cache_path = tmp_path / "voice_event_index.json"
    cache_path.write_text(
        json.dumps({"mtime": 20.0, "names": ["cached_event"]}),
        encoding="utf-8",
    )

    index = VoiceEventIndex(bnk_root=bnk_root, cache_path=cache_path, extra_names=["extra_event"])
    index.load_or_build()

    assert index.names == ["cached_event"]
    assert index.find_candidates("cached_event", None) == ["cached_event"]


def test_load_or_build_rebuilds_stale_cache_and_writes_names(tmp_path: Path) -> None:
    bnk_root = tmp_path / "bnk"
    txtp_root = tmp_path / "txtp"
    bnk_root.mkdir()
    txtp_root.mkdir()
    bnk = bnk_root / "play_vo_main_test.bnk"
    txtp = txtp_root / "play_vo_side_test.txtp"
    bnk.write_bytes(b"bnk")
    txtp.write_bytes(b"txtp")
    os.utime(bnk, (30, 30))
    os.utime(txtp, (40, 40))
    cache_path = tmp_path / "voice_event_index.json"
    cache_path.write_text(
        json.dumps({"mtime": 1.0, "names": ["stale_event"]}),
        encoding="utf-8",
    )

    index = VoiceEventIndex(
        bnk_root=bnk_root,
        txtp_root=txtp_root,
        cache_path=cache_path,
        extra_names=["extra_voice_event"],
    )
    index.load_or_build()

    assert index.names == ["extra_voice_event", "play_vo_main_test", "play_vo_side_test"]
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["mtime"] == 40
    assert payload["names"] == index.names


def test_load_or_build_builds_from_extra_names_without_roots(tmp_path: Path) -> None:
    cache_path = tmp_path / "voice_event_index.json"

    index = VoiceEventIndex(
        bnk_root=None,
        txtp_root=None,
        cache_path=cache_path,
        extra_names=["play_vo_main_alpha", "play_vo_main_beta"],
    )
    index.load_or_build()

    assert index.find_candidates("Main_Alpha", None, limit=1) == ["play_vo_main_alpha"]
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["names"] == ["play_vo_main_alpha", "play_vo_main_beta"]


def test_load_or_build_ignores_corrupt_cache(tmp_path: Path) -> None:
    bnk_root = tmp_path / "bnk"
    bnk_root.mkdir()
    (bnk_root / "play_vo_corrupt_cache_rebuild.bnk").write_bytes(b"bnk")
    cache_path = tmp_path / "voice_event_index.json"
    cache_path.write_text("not json", encoding="utf-8")

    index = VoiceEventIndex(bnk_root=bnk_root, cache_path=cache_path)
    index.load_or_build()

    assert index.names == ["play_vo_corrupt_cache_rebuild"]
