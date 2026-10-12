from __future__ import annotations

import sqlite3
from pathlib import Path

from ludiglot.core import text_builder
from ludiglot.core.matcher import TextMatcher


def _create_blob_db(db_path: Path, rows: list[tuple[int, bytes]]) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE gachaviewinfo (Id INT NOT NULL, BinData BLOB)")
    cur.executemany("INSERT INTO gachaviewinfo (Id, BinData) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def test_pick_text_from_blob_prefers_human_text() -> None:
    blob = (
        b"/Game/Aki/UI/UIResources/Common/Image/Luckdraw/T_Test.T_Test\x00"
        b"Text_SummaryTitle999_Text\x00"
        b"Wanderer Knows No Far and Near\x00"
    )
    picked = text_builder._pick_text_from_blob(blob)
    assert picked == "Wanderer Knows No Far and Near"


def test_load_sqlite_map_extracts_text_from_bindata(tmp_path: Path) -> None:
    db_path = tmp_path / "db_gacha.db"
    blob = (
        b"/Game/Aki/UI/UIResources/Common/Image/Luckdraw/T_Test.T_Test\x00"
        b"Text_SummaryTitle999_Text\x00"
        b"Wanderer Knows No Far and Near\x00"
    )
    _create_blob_db(db_path, [(1001, blob)])

    mapping = text_builder._load_sqlite_map(db_path)
    assert mapping.get("1001") == "Wanderer Knows No Far and Near"


def test_build_text_db_from_root_all_includes_root_gacha_db(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configdb = tmp_path / "ConfigDB"
    configdb.mkdir(parents=True, exist_ok=True)

    db_path = configdb / "db_gacha.db"
    title = "Wanderer Knows No Far and Near"
    blob = (
        b"/Game/Aki/UI/UIResources/Common/Image/Luckdraw/T_Test.T_Test\x00"
        b"Text_SummaryTitle999_Text\x00"
        + title.encode("ascii")
        + b"\x00"
    )
    _create_blob_db(db_path, [(1001, blob)])

    monkeypatch.setattr(
        text_builder,
        "build_voice_map_from_configdb",
        lambda *args, **kwargs: {},
    )

    db = text_builder.build_text_db_from_root_all(tmp_path)
    key = text_builder.normalize_en(title)

    assert key in db
    matches = db[key].get("matches", [])
    assert matches, "expected at least one match from root db_gacha.db"
    assert matches[0].get("official_en") == title
    assert matches[0].get("source_json") == "db_gacha.db"


def test_text_db_leaves_audio_empty_without_explicit_mapping() -> None:
    db = text_builder.build_text_db_from_maps(
        {"MENU_ITEM_001": "Wanderer Knows No Far and Near"},
        {},
        "test.json",
    )

    match = db[text_builder.normalize_en("Wanderer Knows No Far and Near")]["matches"][0]

    assert match.get("audio_hash") is None
    assert match.get("audio_event") is None


def test_text_db_uses_explicit_voice_map_for_audio_metadata() -> None:
    db = text_builder.build_text_db_from_maps(
        {"MAIN_AUDIO_001": "That is the spirit."},
        {},
        "test.json",
        voice_map={"MAIN_AUDIO_001": ["play_vo_main_audio_001"]},
    )

    match = db[text_builder.normalize_en("That is the spirit.")]["matches"][0]

    assert match.get("audio_hash") is not None
    assert match.get("audio_event") == "play_vo_main_audio_001"


def test_player_name_placeholder_generates_rover_search_key() -> None:
    db = text_builder.build_text_db_from_maps(
        {"MAIN_TEST_001": "Hello {PlayerName}, welcome back."},
        {},
        "test.json",
    )

    matcher = TextMatcher(db)

    with_rover = matcher.match([("Hello Rover, welcome back.", 0.99)])
    without_rover = matcher.match([("Hello, welcome back.", 0.99)])

    assert (with_rover.get("matches") or [{}])[0].get("text_key") == "MAIN_TEST_001"
    assert with_rover.get("_score") == 1.0
    assert (without_rover.get("matches") or [{}])[0].get("text_key") == "MAIN_TEST_001"
    assert without_rover.get("_score") == 1.0


def test_missing_long_title_does_not_match_short_ngram() -> None:
    db = text_builder.build_text_db_from_maps(
        {"NPC_FARID_NAME": "Farid"},
        {},
        "test.json",
    )

    matcher = TextMatcher(db)
    result = matcher.match([("Wanderer Knows No Far and Near", 0.99)])

    assert result is None


def test_prefixed_item_name_can_match_exact_ngram() -> None:
    db = text_builder.build_text_db_from_maps(
        {"ITEM_TEST": "Wildfire Mark"},
        {},
        "test.json",
    )

    matcher = TextMatcher(db)
    result = matcher.match([("New Weapon Wildfire Mark Obtained", 0.99)])

    assert result is not None
    assert (result.get("matches") or [{}])[0].get("text_key") == "ITEM_TEST"
