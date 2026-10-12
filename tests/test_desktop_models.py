import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from ludiglot.core.app_paths import AppPaths
from ludiglot.core.desktop_models import OcrRegion, display_signature, migrate_desktop_settings
from ludiglot.core.preferences import ConfigJsonStore, atomic_write_json


def test_migration_preserves_legacy_and_unknown_fields():
    legacy = {"unknown": {"keep": 1}, "ocr_backend": "auto", "exception_capture_region": {"left": 5, "top": 7, "width": 80, "height": 40}}
    result = migrate_desktop_settings(legacy)
    assert result["unknown"] == legacy["unknown"]
    assert result["regions"][1]["bounds"] == legacy["exception_capture_region"]
    assert result["regions"][0]["bounds"] is None
    assert result["overlay"]["background"] == "none"
    assert migrate_desktop_settings(result) == result


def test_regions_reject_tiny_or_invalid_bounds():
    assert OcrRegion("a", "A", bounds={"left": 0, "top": 0, "width": 1, "height": 20}).capture_region is None
    assert OcrRegion("a", "A", bounds={"left": 0}).capture_region is None
    assert OcrRegion("a", "A", bounds={"left": -100, "top": 5, "width": 80, "height": 20}).capture_region.left == -100


def test_duplicate_ids_are_replaced_during_import():
    result = migrate_desktop_settings({"regions": [{"id": "same"}, {"id": "same"}]})
    assert len({r["id"] for r in result["regions"]}) == 2


def test_final_display_dedupe_ignores_raw_ocr_noise():
    blocks = [{"target": "Xin chào Jinhsi", "source": "Hello Jinhsi", "ocr": "Hel1o"}]
    assert display_signature(blocks) == display_signature([{**blocks[0], "ocr": "Hello"}])
    assert display_signature(blocks) != display_signature([{**blocks[0], "target": "Xin chào Changli"}])


def test_source_visibility_and_region_order_affect_signature():
    a, b = {"name": "A", "target": "1", "source": "one"}, {"name": "B", "target": "2", "source": "two"}
    assert display_signature([a], False) == display_signature([{**a, "source": "noise"}], False)
    assert display_signature([a], True) != display_signature([{**a, "source": "noise"}], True)
    assert display_signature([a, b]) != display_signature([b, a])


def test_atomic_settings_concurrent_updates_preserve_all_fields(tmp_path):
    path = tmp_path / "settings.json"
    atomic_write_json(path, {"old": 42})
    def update(index):
        ConfigJsonStore(path).update_values(**{f"field_{index}": index})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(40)))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["old"] == 42
    assert all(raw[f"field_{i}"] == i for i in range(40))
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_atomic_replace_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    atomic_write_json(path, {"old": 42})
    def fail(*_):
        raise OSError("locked")
    monkeypatch.setattr("ludiglot.core.preferences.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write_json(path, {"old": 0})
    assert json.loads(path.read_text()) == {"old": 42}
    assert not list(tmp_path.glob("*.tmp"))


def test_frozen_paths_ignore_working_directory(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "App có dấu" / "Ludiglot.exe"))
    paths = AppPaths.discover(Path("config/settings.json"))
    assert paths.config_base == tmp_path / "App có dấu" / "userdata"
    assert paths.config_path == paths.config_base / "config" / "settings.json"


def test_explicit_portable_config_uses_its_own_userdata(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Another App" / "Ludiglot.exe"))
    configured = tmp_path / "Dữ liệu cũ" / "userdata" / "config" / "settings.json"
    paths = AppPaths.discover(configured)
    assert paths.config_base == configured.parent.parent


def test_coverage_counts_aliases_once_and_english_fallback():
    from ludiglot.core.desktop_models import count_translation_coverage
    translated = {"source_json": "lang.db", "text_key": "1", "official_en": "Hello", "official_vi": "Chào"}
    fallback = {"source_json": "lang.db", "text_key": "2", "official_en": "Bye"}
    assert count_translation_coverage({"hello": {"matches": [translated]}, "alias": {"matches": [dict(translated)]},
        "bye": {"matches": [fallback]}}) == {"vietnamese": 1, "english_fallback": 1, "missing": 0}
