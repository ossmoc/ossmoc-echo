from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from ludiglot.core.config import load_config
from ludiglot.core.game_pak_update import update_from_game_paks
from ludiglot.core.text_builder import build_text_db_from_root_all, save_text_db
from ludiglot.adapters.wuthering_waves.wahu import apply_configured_wahu_provider


DatabaseUpdateSource = Literal["game_paks", "local_data", "unconfigured"]


@dataclass(frozen=True)
class DatabaseUpdateResult:
    success: bool
    message: str
    source: DatabaseUpdateSource
    entry_count: int | None = None


def run_database_update(
    config_path: Path,
    output_path: Path,
    progress: Callable[[str], None] | None = None,
) -> DatabaseUpdateResult:
    def emit(message: str) -> None:
        if progress:
            progress(message)

    cfg = load_config(config_path, validate_data=False)

    if cfg.game_install_root or cfg.game_pak_root or cfg.use_game_paks:
        emit("从游戏 Pak 解包并构建数据库...")
        update_from_game_paks(cfg, config_path, output_path, progress=progress)
        return DatabaseUpdateResult(True, "成功！已从游戏 Pak 更新数据库", "game_paks")

    if cfg.data_root and cfg.data_root.exists():
        emit("从本地数据目录构建数据库...")
        db = build_text_db_from_root_all(cfg.data_root)
        stats = apply_configured_wahu_provider(db, cfg)
        if stats:
            emit(
                "[WAHU] "
                f"official={stats.official_entries}, exact_id={stats.exact_id_matches}, "
                f"vietnamese={stats.vietnamese_matches}, english_fallback={stats.english_fallbacks}, "
                f"missing={stats.missing_entries}"
            )
        save_text_db(db, output_path)
        emit(f"数据库已保存到 {output_path}")
        return DatabaseUpdateResult(True, f"成功！数据库包含 {len(db)} 个条目", "local_data", len(db))

    return DatabaseUpdateResult(False, "未配置 game_pak_root / game_install_root，无法更新数据库", "unconfigured")
