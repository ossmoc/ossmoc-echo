"""数据库更新线程 adapter。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from ludiglot.core.db_update_workflow import run_database_update


class DatabaseUpdateThread(QThread):
    """后台线程：从游戏 Pak 解包并重建数据库"""
    
    progress = pyqtSignal(str)  # 进度消息
    finished = pyqtSignal(bool, str)  # (成功?, 消息)
    
    def __init__(self, config_path: Path, output_path: Path, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self.output_path = output_path
    
    def run(self):
        try:
            result = run_database_update(self.config_path, self.output_path, progress=self.progress.emit)
            self.finished.emit(result.success, result.message)
        except Exception as e:
            self.finished.emit(False, f"更新失败: {e}")
