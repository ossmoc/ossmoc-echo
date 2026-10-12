from __future__ import annotations

import json
from pathlib import Path

import pytest

from ludiglot.core.config import load_config
from ludiglot.core.game_pak_update import GamePakOptions, build_game_pak_update_plan


def test_pak_update_config_loads_without_existing_generated_data(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir()
    (tmp_path / "data").mkdir()
    config_path.write_text(
        json.dumps(
            {
                "use_game_paks": True,
                "game_install_root": str(tmp_path / "game"),
                "game_platform": "Windows",
                "game_server": "CN",
                "game_languages": ["en", "zh-Hans"],
                "data_root": str(tmp_path / "data"),
                "db_path": str(tmp_path / "cache" / "game_text_db.json"),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        load_config(config_path)

    cfg = load_config(config_path, validate_data=False)

    assert cfg.use_game_paks is True
    assert cfg.data_root == tmp_path / "data"
    assert cfg.db_path == tmp_path / "cache" / "game_text_db.json"



def test_game_pak_update_plan_includes_text_filters_and_moves(tmp_path: Path) -> None:
    options = GamePakOptions(
        version="2.0",
        platform="Windows",
        server="OS",
        languages=["en", "zh-Hans"],
        audio_languages=["zh"],
        extract_audio=False,
    )

    plan = build_game_pak_update_plan(tmp_path / "data", options)

    assert [step.filter for step in plan.extraction_steps] == [
        "Config/Json",
        "ConfigDB/",
        "ConfigDB/en",
        "ConfigDB/zh-Hans",
        "TextMap/en",
        "TextMap/zh-Hans",
        "UI/Framework/LGUI/Font/",
    ]
    assert [(move.source.name, move.target.name) for move in plan.directory_moves] == [
        ("ConfigDB", "ConfigDB"),
        ("TextMap", "TextMap"),
        ("WwiseAudio_Generated", "WwiseAudio_Generated"),
        ("Config", "Config"),
    ]
    assert plan.audio_wem_root is None
    assert plan.audio_bnk_root is None


def test_game_pak_update_plan_includes_audio_filters_and_config_roots(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    options = GamePakOptions(
        version="2.0",
        platform="Windows",
        server="OS",
        languages=["en"],
        audio_languages=["zh", "ja"],
        extract_audio=True,
    )

    plan = build_game_pak_update_plan(data_root, options)

    assert [step.filter for step in plan.extraction_steps][-6:] == [
        "Event/zh/",
        "Media/zh/",
        "WwiseExternalSource/zh_",
        "Event/ja/",
        "Media/ja/",
        "WwiseExternalSource/ja_",
    ]
    assert plan.audio_wem_root == data_root / "WwiseAudio_Generated" / "Media" / "zh"
    assert plan.audio_bnk_root == data_root / "WwiseAudio_Generated" / "Event" / "zh"
    assert plan.staged_fonts_dir == data_root / "Client" / "Content" / "Aki" / "UI" / "Framework" / "LGUI" / "Font"
    assert plan.fonts_target == data_root / "Fonts"
    assert plan.cleanup_paths == [
        data_root / "Client",
        data_root / "Audio_Extract_Temp",
        data_root / "Audio",
    ]


def test_configdb_language_steps_warn_on_failure(tmp_path: Path) -> None:
    options = GamePakOptions(
        version="2.0",
        platform="Windows",
        server="OS",
        languages=["en"],
        audio_languages=[],
        extract_audio=False,
    )

    plan = build_game_pak_update_plan(tmp_path / "data", options)
    configdb_lang = next(step for step in plan.extraction_steps if step.filter == "ConfigDB/en")

    assert configdb_lang.warn_on_failure is not None
