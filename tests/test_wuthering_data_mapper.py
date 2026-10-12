from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ludiglot.adapters.wuthering_waves.data_mapper import WutheringDataMapper
from ludiglot.core.text_builder import find_multitext_paths


def test_wuthering_data_mapper_prefers_configdb_lang_text(tmp_path: Path) -> None:
    en = tmp_path / "ConfigDB" / "en" / "lang_text.db"
    zh = tmp_path / "ConfigDB" / "zh-Hans" / "lang_text.db"
    en.parent.mkdir(parents=True)
    zh.parent.mkdir(parents=True)
    en.write_bytes(b"en")
    zh.write_bytes(b"zh")

    paths = WutheringDataMapper(tmp_path).parse()

    assert paths.en_text == en
    assert paths.zh_text == zh


def test_wuthering_data_mapper_falls_back_to_textmap_json(tmp_path: Path) -> None:
    en = tmp_path / "TextMap" / "en" / "MultiText.json"
    zh = tmp_path / "TextMap" / "zh-CN" / "MultiText.json"
    en.parent.mkdir(parents=True)
    zh.parent.mkdir(parents=True)
    en.write_text("{}", encoding="utf-8")
    zh.write_text("{}", encoding="utf-8")

    paths = WutheringDataMapper(tmp_path).parse()

    assert paths.en_text == en
    assert paths.zh_text == zh


def test_core_find_multitext_paths_delegates_to_wuthering_mapper(tmp_path: Path) -> None:
    en = tmp_path / "TextMap" / "en" / "MultiText.json"
    zh = tmp_path / "TextMap" / "zh-Hans" / "MultiText.json"
    en.parent.mkdir(parents=True)
    zh.parent.mkdir(parents=True)
    en.write_text("{}", encoding="utf-8")
    zh.write_text("{}", encoding="utf-8")

    assert find_multitext_paths(tmp_path) == (en, zh)


def test_wuthering_data_mapper_discovers_seed_and_nested_text_source_roots(tmp_path: Path) -> None:
    configdb = tmp_path / "ConfigDB"
    nested = configdb / "ConfigDB"
    textmap = tmp_path / "Client" / "Content" / "Aki" / "TextMap"
    (configdb / "en").mkdir(parents=True)
    (configdb / "zh-Hans").mkdir(parents=True)
    (nested / "en").mkdir(parents=True)
    (nested / "zh-CN").mkdir(parents=True)
    (textmap / "en").mkdir(parents=True)
    (textmap / "zh-Hans").mkdir(parents=True)

    roots = WutheringDataMapper(tmp_path).text_source_roots()

    assert configdb in roots
    assert nested in roots
    assert textmap in roots


def test_wuthering_data_mapper_discovers_root_blob_dbs(tmp_path: Path) -> None:
    primary = tmp_path / "ConfigDB" / "db_gacha.db"
    staged = tmp_path / "Client" / "Content" / "Aki" / "ConfigDB" / "db_gacha.db"
    primary.parent.mkdir(parents=True)
    staged.parent.mkdir(parents=True)
    primary.write_bytes(b"primary")
    staged.write_bytes(b"staged")

    assert WutheringDataMapper(tmp_path).root_blob_db_paths() == [primary, staged]


def test_wuthering_data_mapper_loads_plot_audio_json(tmp_path: Path) -> None:
    plot_audio = tmp_path / "ConfigDB" / "PlotAudio.json"
    plot_audio.parent.mkdir(parents=True)
    plot_audio.write_text(
        json.dumps(
            {
                "Data": [
                    {
                        "TextKey": "MAIN_JSON_001",
                        "AudioEventName": "play_vo_main_json_001",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert WutheringDataMapper(tmp_path).load_plot_audio_map() == {
        "MAIN_JSON_001": "play_vo_main_json_001"
    }


def test_wuthering_data_mapper_loads_plot_audio_blob_db(tmp_path: Path) -> None:
    db_path = tmp_path / "ConfigDB" / "db_plot_audio.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE PlotAudio (Id TEXT NOT NULL, BinData BLOB)")
    cur.execute(
        "INSERT INTO PlotAudio (Id, BinData) VALUES (?, ?)",
        ("MAIN_DB_001", b"prefix\x00play_vo_main_db_001\x00suffix"),
    )
    conn.commit()
    conn.close()

    assert WutheringDataMapper(tmp_path).load_plot_audio_map() == {
        "MAIN_DB_001": "play_vo_main_db_001"
    }


def test_wuthering_data_mapper_error_names_checked_paths(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        WutheringDataMapper(tmp_path).parse()

    message = str(exc.value)
    assert "lang_text.db" in message
    assert "ConfigDB" in message
    assert "zh-Hans" in message
