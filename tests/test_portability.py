"""Negative/relocation cases; these do not emulate a different Windows kernel."""
import json
from pathlib import Path
import sys

import pytest

from ludiglot.core.app_paths import AppPaths
from ludiglot.core.config import load_config
from ludiglot.core.desktop_models import migrate_desktop_settings
from ludiglot.core.preferences import atomic_write_json


@pytest.mark.parametrize('folder', ['OssmocEcho', 'Ứng dụng có dấu và khoảng trắng'])
@pytest.mark.parametrize('old_version', ['1.0.3', '1.0.4', '1.1.0', '1.1.1', '1.1.2', '1.1.3'])
def test_old_settings_relocate_without_losing_personal_choices(tmp_path, monkeypatch, folder, old_version):
    application = tmp_path / folder
    config = application / 'userdata' / 'config' / 'settings.json'
    original = {'app_version': old_version, 'db_path': 'cache/game_text_db.json',
                'auto_rebuild_db': False, 'auto_wuwa': {'player_name': 'Ossmoc', 'font_size': 25},
                'story_memory': [{'id': 'Side_Test_1_1'}], 'unknown_plugin_option': {'keep': True}}
    atomic_write_json(config, original)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(application / 'OssmocEcho.exe'))
    monkeypatch.chdir(tmp_path)
    paths = AppPaths.discover()
    cfg = load_config(paths.config_path, validate_data=False, base_root=paths.config_base)
    assert cfg.db_path == application / 'userdata' / 'cache' / 'game_text_db.json'
    migrated = migrate_desktop_settings(original)
    assert migrated['auto_wuwa']['player_name'] == 'Ossmoc'
    assert migrated['auto_wuwa']['font_size'] == 25
    assert migrated['story_memory'] == original['story_memory']
    assert migrated['unknown_plugin_option'] == original['unknown_plugin_option']
    assert json.loads(config.read_text(encoding='utf-8')) == original


def test_discovery_of_clean_portable_app_does_not_copy_dev_data(tmp_path, monkeypatch):
    application = tmp_path / 'Fresh app'
    application.mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(application / 'OssmocEcho.exe'))
    monkeypatch.chdir(tmp_path)
    paths = AppPaths.discover()
    assert paths.data_root == application / 'userdata'
    assert not paths.config_path.exists()
    assert not paths.updates.exists()
    assert list(application.iterdir()) == []
