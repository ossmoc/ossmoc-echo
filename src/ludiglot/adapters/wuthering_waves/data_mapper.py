from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable


@dataclass
class WutheringDataPaths:
    en_text: Path
    zh_text: Path


@dataclass(frozen=True)
class WutheringTextLanguagePair:
    en: str
    zh: str


class WutheringDataMapper:
    """解析 WutheringData 的结构与字段。"""

    language_pairs = (
        WutheringTextLanguagePair("en", "zh-Hans"),
        WutheringTextLanguagePair("en", "zh-CN"),
    )
    language_dir_names = {
        "en",
        "zh-Hans",
        "zh-CN",
        "ja",
        "ko",
        "de",
        "fr",
        "es",
        "ru",
        "pt",
        "id",
        "vi",
        "th",
        "zh-Hant",
        "Misc",
    }
    root_blob_db_names = ("db_gacha.db",)

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def parse(self) -> WutheringDataPaths:
        en_text, zh_text = self.find_multitext_paths()
        return WutheringDataPaths(en_text=en_text, zh_text=zh_text)

    def find_multitext_paths(self) -> tuple[Path, Path]:
        candidates_en = _candidate_paths(
            self.data_root,
            [
                "ConfigDB/en/lang_text.db",
                "TextMap/en/MultiText.json",
                "TextMap/en/Multitext.json",
            ],
        )
        candidates_zh = _candidate_paths(
            self.data_root,
            [
                "ConfigDB/zh-Hans/lang_text.db",
                "TextMap/zh-Hans/MultiText.json",
                "TextMap/zh-CN/MultiText.json",
                "TextMap/zh-Hans/Multitext.json",
            ],
        )
        en_path = next((p for p in candidates_en if p.exists()), None)
        zh_path = next((p for p in candidates_zh if p.exists()), None)

        if en_path is None or zh_path is None:
            text_map_root = self.data_root / "TextMap"
            if text_map_root.exists():
                for path in text_map_root.rglob("MultiText*.json"):
                    rel = path.as_posix().lower()
                    if "textmap/en" in rel and en_path is None:
                        en_path = path
                    if "textmap/zh" in rel and zh_path is None:
                        zh_path = path
                    if en_path and zh_path:
                        break

        if en_path is None or zh_path is None:
            raise FileNotFoundError(
                f"无法在所选目录中找到游戏文本数据 ({self.data_root})\n\n"
                "诊断细节:\n"
                f"- 英文路径匹配: {candidates_en[0] if candidates_en else 'N/A'}\n"
                f"- 中文路径匹配: {candidates_zh[0] if candidates_zh else 'N/A'}\n\n"
                "建议解决方法:\n"
                "1. 运行 'ludiglot pak-update' 从游戏 Pak 解包并构建数据库\n"
                "2. 确保 config/settings.json 中配置了正确的 game_pak_root 或 game_install_root\n"
                "3. 详情请参阅 docs/usage/data-management.md"
            )
        return en_path, zh_path

    def text_source_roots(self) -> list[Path]:
        seed_roots = [
            self.data_root / "ConfigDB",
            self.data_root / "Client" / "Content" / "Aki" / "ConfigDB",
            self.data_root / "TextMap",
            self.data_root / "Client" / "Content" / "Aki" / "TextMap",
        ]
        roots: list[Path] = []
        seen: set[Path] = set()
        for seed_root in seed_roots:
            if not seed_root.exists():
                continue
            _append_unique(roots, seen, seed_root)
            try:
                for child in seed_root.iterdir():
                    if not child.is_dir() or child.name in self.language_dir_names:
                        continue
                    if self._has_language_pair(child):
                        _append_unique(roots, seen, child)
            except OSError:
                pass
        return roots

    def root_blob_db_paths(self) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for base in (
            self.data_root / "ConfigDB",
            self.data_root / "Client" / "Content" / "Aki" / "ConfigDB",
        ):
            if not base.exists():
                continue
            for name in self.root_blob_db_names:
                candidate = base / name
                if candidate.exists():
                    _append_unique(paths, seen, candidate)
        return paths

    def _has_language_pair(self, root: Path) -> bool:
        return any(
            (root / pair.en).is_dir() and (root / pair.zh).is_dir()
            for pair in self.language_pairs
        )

    def load_plot_audio_map(self) -> dict[str, str]:
        json_path = self.data_root / "ConfigDB" / "PlotAudio.json"
        db_path = self.data_root / "ConfigDB" / "db_plot_audio.db"
        mapping: dict[str, str] = {}

        if json_path.exists():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                for item in _iter_items(payload):
                    text_key = (
                        item.get("TextKey")
                        or item.get("TextMapId")
                        or item.get("TextId")
                        or item.get("Key")
                        or item.get("Content")
                    )
                    file_name = (
                        item.get("FileName")
                        or item.get("AudioEventName")
                        or item.get("AudioEvent")
                        or item.get("Voice")
                    )
                    if text_key and file_name:
                        mapping[str(text_key)] = str(file_name)
            except Exception:
                pass

        if db_path.exists():
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [r[0] for r in cursor.fetchall()]
                    audio_pat = re.compile(rb"(?:play_vo_|vo_)[a-zA-Z0-9_]{3,}")

                    for table_name in tables:
                        cursor.execute(f"PRAGMA table_info({table_name})")
                        cols_info = cursor.fetchall()
                        cols = {r[1].lower(): r[1] for r in cols_info}
                        text_key_col = (
                            cols.get("id")
                            or cols.get("textkey")
                            or cols.get("key")
                            or cols.get("textmapid")
                            or cols.get("textid")
                            or cols.get("content")
                        )
                        file_name_col = (
                            cols.get("filename")
                            or cols.get("audioeventname")
                            or cols.get("audioevent")
                            or cols.get("voice")
                        )
                        blob_col = next(
                            (
                                c[1]
                                for c in cols_info
                                if "blob" in (c[2] or "").lower() or c[1].lower() == "bindata"
                            ),
                            None,
                        )

                        if not text_key_col:
                            continue
                        if file_name_col:
                            cursor.execute(f"SELECT {text_key_col}, {file_name_col} FROM {table_name}")
                            for text_key, file_name in cursor.fetchall():
                                if text_key and file_name:
                                    mapping[str(text_key)] = str(file_name)
                        if blob_col and (not file_name_col or table_name.lower() == "plotaudio"):
                            cursor.execute(f"SELECT {text_key_col}, {blob_col} FROM {table_name}")
                            for text_key, blob in cursor.fetchall():
                                if not text_key or not isinstance(blob, (bytes, bytearray)):
                                    continue
                                matches = audio_pat.findall(blob)
                                if matches:
                                    event_name = matches[0].decode("ascii", errors="ignore").strip()
                                    if event_name:
                                        mapping[str(text_key)] = event_name
                finally:
                    conn.close()
            except Exception:
                pass

        return mapping


def _iter_items(payload: object) -> Iterable[dict]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(payload, dict):
        for key in ("Data", "data", "Items", "items", "List", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return


def _candidate_paths(root: Path, rel_paths: Iterable[str]) -> list[Path]:
    return [root / rel for rel in rel_paths]


def _append_unique(paths: list[Path], seen: set[Path], path: Path) -> None:
    if path in seen:
        return
    paths.append(path)
    seen.add(path)
