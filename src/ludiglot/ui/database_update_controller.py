from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PyQt6.QtWidgets import QDialog

from ludiglot.core.config import AppConfig
from ludiglot.ui.db_updater import DatabaseUpdateThread
from ludiglot.ui.dialogs import StyledDialog, StyledProgressDialog


class DatabaseUpdateController:
    """Coordinates database update dialogs, thread lifetime, and runtime refresh."""

    def __init__(
        self,
        *,
        parent: Any,
        config_provider: Callable[[], AppConfig],
        config_path: Path,
        refresh_runtime: Callable[[], bool],
        log: Callable[[str], None],
        dialog_cls: type = StyledDialog,
        progress_dialog_cls: type = StyledProgressDialog,
        thread_cls: type = DatabaseUpdateThread,
    ) -> None:
        self._parent = parent
        self._config_provider = config_provider
        self._config_path = config_path
        self._refresh_runtime = refresh_runtime
        self._log = log
        self._dialog_cls = dialog_cls
        self._progress_dialog_cls = progress_dialog_cls
        self._thread_cls = thread_cls
        self._thread: Any | None = None
        self._progress_dialog: Any | None = None

    def start(self) -> None:
        config = self._config_provider()
        if not (config.game_pak_root or config.game_install_root):
            self._dialog_cls.warning(
                self._parent,
                "配置错误",
                "未设置游戏路径。\n请在 config/settings.json 中配置 game_pak_root 或 game_install_root。"
            )
            return

        reply = self._dialog_cls.question(
            self._parent,
            "更新数据库",
            f"即将从游戏 Pak 解包并重建数据库。\n\n"
            f"游戏路径: {config.game_pak_root or config.game_install_root}\n"
            f"输出文件: {config.db_path}\n\n"
            f"此操作可能需要几分钟。是否继续？"
        )
        if reply != QDialog.DialogCode.Accepted:
            return

        progress = self._progress_dialog_cls("Database Update", "正在更新数据库...", self._parent)
        self._progress_dialog = progress
        progress.show()

        # 暂时禁用快捷键监听，避免数据库更新期间用户误触截图导致卡死
        if hasattr(self._parent, "_hotkeys"):
            try:
                self._parent._hotkeys.stop()
                self._log("[HOTKEY] 数据库更新期间已暂时禁用快捷键监听")
            except Exception as e:
                self._log(f"[HOTKEY] 暂时禁用快捷键监听失败: {e}")

        thread = self._thread_cls(self._config_path, config.db_path)
        self._thread = thread

        def on_progress(msg: str) -> None:
            progress.setLabelText(msg)
            self._log(f"[DB UPDATE] {msg}")

        def on_finished(success: bool, message: str) -> None:
            progress.close()
            if success:
                self._handle_success(message)
            else:
                self._dialog_cls.critical(self._parent, "失败", f"数据库更新失败：\n{message}")
                self._log(f"[DB UPDATE] 失败：{message}")
            
            # 恢复快捷键监听
            if hasattr(self._parent, "_hotkeys"):
                try:
                    self._parent._hotkeys.start()
                    self._log("[HOTKEY] 数据库更新已完成，已恢复快捷键监听")
                except Exception as e:
                    self._log(f"[HOTKEY] 恢复快捷键监听失败: {e}")

            self._thread = None
            self._progress_dialog = None

        thread.progress.connect(on_progress)
        thread.finished.connect(on_finished)
        thread.start()

    def _handle_success(self, message: str) -> None:
        try:
            refreshed = self._refresh_runtime()
        except Exception as exc:
            refreshed = False
            self._log(f"[DB UPDATE] 运行时资源刷新异常：{exc}")

        if refreshed:
            self._dialog_cls.information(self._parent, "成功", message)
            self._log(f"[DB UPDATE] 成功：{message}")
        else:
            self._dialog_cls.warning(
                self._parent,
                "警告",
                f"数据库更新成功，但运行时资源刷新失败。请重启应用以使用新数据库。\n\n{message}",
            )
            self._log(f"[DB UPDATE] 更新成功但刷新运行时资源失败：{message}")
