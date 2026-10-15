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


def test_folder_only_options_select_rolling_json_feed(monkeypatch):
    from types import SimpleNamespace
    from ludiglot.core import game_pak_update as module
    from ludiglot.core.aes_archive import parse_aes_archive, select_keys
    monkeypatch.setattr(module, '_is_tty', lambda: False)
    entries = parse_aes_archive(json.dumps({'mainKey':'0x'+'1'*64,
        'dynamicKeys':[{'guid':'fixture', 'key':'0x'+'2'*64}]}))
    cfg = SimpleNamespace(game_version=None, game_platform='Windows', game_server='OS',
                          game_languages=['en','zh-Hans'], game_audio_languages=None, extract_audio=False)
    options = module._choose_options(entries, cfg)
    assert options.version == ''
    selection = select_keys(entries, options.version, options.platform, options.server)
    assert selection.version_label == 'Latest' and len(selection.keys) == 2


def test_automatic_selection_ignores_newer_beta_and_other_platform(monkeypatch):
    from ludiglot.core.aes_archive import AesKeyEntry, select_keys
    entries = [AesKeyEntry('3.7','3.7','main','old','Windows','OS'),
               AesKeyEntry('3.8 (Beta)','3.8','main','beta','Windows','OS'),
               AesKeyEntry('3.9','3.9','main','android','Android','OS')]
    result = select_keys(entries, '', 'Windows', 'OS')
    assert [key.aes_key for key in result.keys] == ['old']


def test_game_root_accepts_client_game_and_launcher_folder(tmp_path):
    from ludiglot.core.game_pak_update import resolve_game_root
    game = tmp_path / 'Wuthering Waves Game'
    paks = game / 'Client/Content/Paks'
    paks.mkdir(parents=True)
    (paks/'pakchunk0-Windows.pak').write_bytes(b'fixture')
    for selected in (tmp_path, game, game/'Client'):
        assert resolve_game_root(selected) == game


def test_game_root_rejects_incomplete_install(tmp_path):
    from ludiglot.core.game_pak_update import resolve_game_root, GamePakUpdateError
    (tmp_path/'Client/Content/Paks').mkdir(parents=True)
    with pytest.raises(GamePakUpdateError, match='chọn lại'):
        resolve_game_root(tmp_path)


@pytest.mark.parametrize('content,expected', [
    ('{"version":"3.7.0"}', '3.7.0'),
    ('\ufeff{"version":"3.8"}', '3.8'),
    ('{"version":"Latest"}', None),
    ('{"version":3.7}', None),
    ('[]', None), ('bad JSON', None), (' '*65537, None)],
    ids=['version','bom','rolling','numeric','array','invalid','too-large'])
def test_optional_launcher_version_is_strict_and_bounded(tmp_path, content, expected):
    from ludiglot.core.game_pak_update import read_launcher_game_version
    path = tmp_path/'launcherDownloadConfig.json'
    assert read_launcher_game_version(tmp_path) is None
    path.write_text(content, encoding='utf-8')
    assert read_launcher_game_version(tmp_path) == expected
