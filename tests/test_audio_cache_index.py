from __future__ import annotations

import json
import os
from pathlib import Path

from ludiglot.core.audio_mapper import AudioCacheIndex


def test_plan_scan_reports_missing_cache_dir_without_creating_it(tmp_path: Path) -> None:
    cache_dir = tmp_path / "missing-cache"

    plan = AudioCacheIndex(cache_dir).plan_scan()

    assert plan.create_cache_dir is True
    assert plan.entries == {}
    assert plan.remove_paths == []
    assert not cache_dir.exists()


def test_scan_creates_cache_dir_and_writes_index(tmp_path: Path) -> None:
    cache_dir = tmp_path / "missing-cache"
    index_path = tmp_path / "index" / "audio_index.json"

    index = AudioCacheIndex(cache_dir, index_path=index_path)
    index.scan()

    assert cache_dir.exists()
    assert index_path.exists()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["entries"] == []


def test_scan_indexes_numeric_audio_files_only(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    wav = cache_dir / "123.wav"
    wav.write_bytes(b"wav")
    (cache_dir / "not-a-hash.wav").write_bytes(b"wav")
    (cache_dir / "456.txt").write_text("skip", encoding="utf-8")

    index = AudioCacheIndex(cache_dir)
    index.scan()

    assert index.find(123) == wav
    assert index.find(456) is None
    assert set(index.entries) == {123}


def test_load_drops_stale_entries(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    live = cache_dir / "111.wav"
    missing = cache_dir / "222.wav"
    live.write_bytes(b"live")
    index_path = cache_dir / "audio_index.json"
    index_path.write_text(
        json.dumps(
            {
                "entries": [
                    {"hash": 111, "path": str(live), "size": 4, "mtime": live.stat().st_mtime},
                    {"hash": 222, "path": str(missing), "size": 7, "mtime": 1.0},
                ]
            }
        ),
        encoding="utf-8",
    )

    index = AudioCacheIndex(cache_dir, index_path=index_path)
    index.load()

    assert index.find(111) == live
    assert 222 not in index.entries


def test_plan_scan_reports_size_limit_removals_without_deleting(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    old = cache_dir / "1.wav"
    new = cache_dir / "2.wav"
    old.write_bytes(b"o" * 800_000)
    new.write_bytes(b"n" * 800_000)
    os.utime(old, (10, 10))
    os.utime(new, (20, 20))

    plan = AudioCacheIndex(cache_dir, max_mb=1).plan_scan()

    assert plan.remove_paths == [old]
    assert set(plan.entries) == {2}
    assert old.exists()
    assert new.exists()


def test_scan_enforces_size_limit_by_deleting_oldest(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    old = cache_dir / "1.wav"
    new = cache_dir / "2.wav"
    old.write_bytes(b"o" * 800_000)
    new.write_bytes(b"n" * 800_000)
    os.utime(old, (10, 10))
    os.utime(new, (20, 20))

    index = AudioCacheIndex(cache_dir, max_mb=1)
    index.scan()

    assert not old.exists()
    assert new.exists()
    assert set(index.entries) == {2}
