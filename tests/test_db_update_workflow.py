import json
from pathlib import Path

from ludiglot.core import db_update_workflow
from ludiglot.core.db_update_workflow import run_database_update


def write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def base_config(tmp_path, **overrides):
    data = {
        "db_path": str(tmp_path / "db.json"),
        "image_path": str(tmp_path / "capture.png"),
        "auto_rebuild_db": False,
    }
    data.update(overrides)
    return data


def test_run_database_update_uses_game_paks(monkeypatch, tmp_path):
    config_path = tmp_path / "settings.json"
    output_path = tmp_path / "db.json"
    write_config(config_path, base_config(tmp_path, use_game_paks=True, game_install_root=str(tmp_path / "game")))
    calls = []

    def fake_update(cfg, cfg_path, out_path, progress=None):
        calls.append((cfg.use_game_paks, cfg_path, out_path))
        if progress:
            progress("pak-progress")

    monkeypatch.setattr(db_update_workflow, "update_from_game_paks", fake_update)
    progress = []

    result = run_database_update(config_path, output_path, progress.append)

    assert result.success is True
    assert result.source == "game_paks"
    assert result.message == "成功！已从游戏 Pak 更新数据库"
    assert calls == [(True, config_path, output_path)]
    assert progress == ["从游戏 Pak 解包并构建数据库...", "pak-progress"]


def test_run_database_update_uses_local_data(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    config_path = tmp_path / "settings.json"
    output_path = tmp_path / "db.json"
    write_config(config_path, base_config(tmp_path, data_root=str(data_root)))
    saved = []

    monkeypatch.setattr(db_update_workflow, "build_text_db_from_root_all", lambda root: {"a": {}, "b": {}})
    monkeypatch.setattr(db_update_workflow, "save_text_db", lambda db, path: saved.append((db, path)))
    progress = []

    result = run_database_update(config_path, output_path, progress.append)

    assert result.success is True
    assert result.source == "local_data"
    assert result.entry_count == 2
    assert result.message == "成功！数据库包含 2 个条目"
    assert saved == [({"a": {}, "b": {}}, output_path)]
    assert progress == ["从本地数据目录构建数据库...", f"数据库已保存到 {output_path}"]


def test_run_database_update_reports_unconfigured(tmp_path):
    config_path = tmp_path / "settings.json"
    output_path = tmp_path / "db.json"
    write_config(config_path, base_config(tmp_path))

    result = run_database_update(config_path, output_path)

    assert result.success is False
    assert result.source == "unconfigured"
    assert result.message == "未配置 game_pak_root / game_install_root，无法更新数据库"
