from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class AudioPlayer:
    """音频播放封装，支持非阻塞播放和状态控制。"""

    def __init__(self) -> None:
        self._player: Any = None
        self._audio: Any = None
        self._loop: Any = None
        self._is_playing = False

    def play(self, path: str, block: bool = False) -> None:
        """播放指定路径的音频。"""
        if not path or not os.path.exists(path):
            return

        try:
            from PyQt6.QtCore import QEventLoop, QUrl
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PyQt6.QtWidgets import QApplication
        except Exception:
            return self._play_fallback(path)

        app = QApplication.instance()
        # 如果没有 QApplication 且要求阻塞，则创建一个临时的
        if not app:
            if block:
                app = QApplication([])
            else:
                return self._play_fallback(path)

        if self._player is None:
            print("[DEBUG] AudioPlayer init QMediaPlayer", flush=True)
            self._player = QMediaPlayer()
            self._audio = QAudioOutput()
            self._player.setAudioOutput(self._audio)

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))
        self._is_playing = True
        print(f"[DEBUG] AudioPlayer source set: {path}", flush=True)

        if block:
            self._loop = QEventLoop()
            def _on_status(status: QMediaPlayer.MediaStatus):
                if status in (
                    QMediaPlayer.MediaStatus.EndOfMedia,
                    QMediaPlayer.MediaStatus.InvalidMedia,
                    QMediaPlayer.MediaStatus.NoMedia,
                ):
                    self._is_playing = False
                    if self._loop:
                        self._loop.quit()

            self._player.mediaStatusChanged.connect(_on_status)
            self._player.play()
            self._loop.exec()
            self._loop = None
        else:
            print("[DEBUG] AudioPlayer playing (async)...", flush=True)
            self._player.play()
            print("[DEBUG] AudioPlayer play() called.", flush=True)

    def stop(self) -> None:
        """停止播放。"""
        if self._player:
            self._player.stop()
        self._is_playing = False
        if self._loop:
            self._loop.quit()

    def pause(self) -> None:
        """暂停播放。"""
        if self._player and self._is_playing:
            self._player.pause()
            self._is_playing = False

    def resume(self) -> None:
        """恢复播放。"""
        if self._player and not self._is_playing:
            self._player.play()
            self._is_playing = True

    def seek(self, position: float) -> None:
        """跳转到指定位置（0-1之间的百分比）。"""
        if self._player:
            duration = self._player.duration()
            if duration > 0:
                target_pos = int(position * duration)
                self._player.setPosition(target_pos)
                if not self._is_playing:
                    # Qt6 FFmpeg 后端在暂停状态下调用 setPosition 不会立即刷新解码器和状态
                    # 通过极速的 play() + pause() 强行触发媒体引擎 flush，实现暂停态秒级 Seek！
                    self._player.play()
                    self._player.pause()

    def is_playing(self) -> bool:
        """返回当前是否正在播放。"""
        return self._is_playing

    def get_position(self) -> float:
        """获取当前播放位置（0-1之间的百分比）。"""
        if self._player:
            duration = self._player.duration()
            if duration > 0:
                return self._player.position() / duration
        return 0.0

    def get_duration(self) -> int:
        """获取音频总时长（毫秒）。"""
        if self._player:
            return self._player.duration()
        return 0

    def has_reached_end(self) -> bool:
        """返回 Qt 后端是否已经自然播放到结尾。"""
        if not self._player:
            return False
        try:
            from PyQt6.QtMultimedia import QMediaPlayer
            return self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
        except Exception:
            return False

    def current_source_name(self) -> str | None:
        """返回当前播放源的文件名。"""
        if not self._player:
            return None
        try:
            src_path = self._player.source().toLocalFile()
            return Path(src_path).name if src_path else None
        except Exception:
            return None

    def _play_fallback(self, path: str) -> None:
        try:
            from playsound import playsound
            playsound(path)
        except Exception:
            pass
