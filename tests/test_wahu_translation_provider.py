from __future__ import annotations

import sqlite3
from pathlib import Path

from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider


def _make_wahu_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE entries (dbfile TEXT, key TEXT, vi TEXT, status TEXT, PRIMARY KEY(dbfile, key))"
        )
        connection.executemany(
            "INSERT INTO entries(dbfile, key, vi, status) VALUES (?, ?, ?, ?)",
            [
                ("lang_multi_text.db", "DIALOG_1", "Xin chao", "translated"),
                ("lang_multi_text.db", "EMPTY", "", "untranslated"),
                ("lang_multi_text.db", "DRAFT", "Ban nhap", "untranslated"),
            ],
        )


def test_enrich_uses_exact_id_and_falls_back_to_english(tmp_path: Path) -> None:
    wahu_path = tmp_path / "project.db"
    _make_wahu_database(wahu_path)
    translated = {
        "hello": {
            "matches": [
                {
                    "source_json": "lang_multi_text.db",
                    "text_key": "DIALOG_1",
                    "official_en": "Hello",
                    "official_cn": "你好",
                }
            ]
        },
        "new": {
            "matches": [
                {
                    "source_json": "lang_multi_text.db",
                    "text_key": "NEW_ENTRY",
                    "official_en": "New text",
                    "official_cn": "新文本",
                }
            ]
        },
    }

    stats = WahuTranslationProvider(wahu_path).enrich(translated)

    exact = translated["hello"]["matches"][0]
    fallback = translated["new"]["matches"][0]
    assert exact["official_cn"] == "Xin chao"
    assert exact["official_zh"] == "你好"
    assert exact["translation_source"] == "wahu_exact_id_reviewed"
    assert fallback["official_cn"] == "New text"
    assert fallback["translation_source"] == "official_english_fallback"
    assert stats.official_entries == 2
    assert stats.exact_id_matches == 1
    assert stats.vietnamese_matches == 1
    assert stats.english_fallbacks == 1
    assert stats.missing_entries == 0


def test_provider_opens_database_read_only(tmp_path: Path) -> None:
    wahu_path = tmp_path / "project.db"
    _make_wahu_database(wahu_path)
    provider = WahuTranslationProvider(wahu_path)

    provider.load_exact_translations()

    with sqlite3.connect(wahu_path) as connection:
        assert connection.execute("SELECT count(*) FROM entries").fetchone()[0] == 3


def test_repeated_merge_preserves_original_chinese(tmp_path: Path) -> None:
    wahu_path = tmp_path / "project.db"
    _make_wahu_database(wahu_path)
    row = {"source_json": "lang_multi_text.db", "text_key": "DIALOG_1", "official_en": "Hello", "official_cn": "Original Chinese"}
    cache = {"hello": {"matches": [row]}}
    provider = WahuTranslationProvider(wahu_path)
    provider.enrich(cache)
    provider.enrich(cache)
    assert row["official_zh"] == "Original Chinese"
    assert row["official_cn"] == "Xin chao"


def test_alias_objects_are_updated_but_original_identity_counted_once(tmp_path: Path) -> None:
    wahu_path = tmp_path / "project.db"
    _make_wahu_database(wahu_path)
    row = {"source_json": "lang_multi_text.db", "text_key": "DIALOG_1", "official_en": "Hello", "official_cn": "Original Chinese"}
    alias = dict(row)
    result = WahuTranslationProvider(wahu_path).enrich({"first": {"matches": [row]}, "second": {"matches": [alias]}})
    assert row["official_vi"] == alias["official_vi"] == "Xin chao"
    assert result.official_entries == result.vietnamese_matches == 1


def test_english_source_mismatch_and_missing_cross_version_source_fallback(tmp_path: Path) -> None:
    wahu_path = tmp_path / "project.db"
    _make_wahu_database(wahu_path)
    with sqlite3.connect(wahu_path) as connection:
        connection.execute("ALTER TABLE entries ADD COLUMN en TEXT")
        connection.execute("UPDATE entries SET en='Changed source' WHERE key='DIALOG_1'")
    row = {"source_json": "lang_multi_text.db", "text_key": "DIALOG_1", "official_en": "Hello", "official_cn": "Original Chinese"}
    result = WahuTranslationProvider(wahu_path).enrich({"hello": {"matches": [row]}})
    assert row["official_cn"] == "Hello"
    assert result.incompatible_source_entries == 1
    with sqlite3.connect(wahu_path) as connection:
        connection.execute("UPDATE entries SET en=NULL")
    result = WahuTranslationProvider(wahu_path, require_source_english=True).enrich({"hello": {"matches": [row]}})
    assert row["official_cn"] == "Hello"
    assert result.incompatible_source_entries == 1


def test_canonical_en_targets_are_still_gated_by_reviewed_ids(tmp_path: Path) -> None:
    wahu_path = tmp_path / "project.db"
    _make_wahu_database(wahu_path)
    translations = WahuTranslationProvider(wahu_path, canonical_translations={
        ("lang_multi_text.db", "DIALOG_1"): "Xin chao Jinhsi",
        ("lang_multi_text.db", "DRAFT"): "Not reviewed",
    }).load_exact_translations()
    assert translations[("lang_multi_text.db", "DIALOG_1")] == "Xin chao Jinhsi"
    assert ("lang_multi_text.db", "DRAFT") not in translations
