from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Tuple, Any

from ludiglot.core.voice_map import build_voice_map_from_configdb



def normalize_en(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


_TAG_RE = re.compile(r"</?[^>]+>")
_BRACE_RE = re.compile(r"\{[^}]+\}")
_PRINTABLE_ASCII_RE = re.compile(rb"[\x20-\x7E]{4,}")
_TEXT_KEY_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9.]+)+$")

# 性别占位符正则：匹配 {Male=xxx;Female=yyy} 或 {Female=xxx;Male=yyy} 格式
_GENDER_PLACEHOLDER_RE = re.compile(
    r"\{(?:Male=([^;]+);Female=([^}]+)|Female=([^;]+);Male=([^}]+))\}",
    re.IGNORECASE
)
_PLAYER_NAME_PLACEHOLDER_RE = re.compile(
    r"\{PlayerName\}|\{Cus:Var,\s*VarType=Global\s+Key=main_team_name\}",
    re.IGNORECASE,
)


def expand_player_name_placeholder(text: str, player_name: str) -> str:
    return _PLAYER_NAME_PLACEHOLDER_RE.sub(player_name, text)


def has_player_name_placeholder(text: str) -> bool:
    return bool(_PLAYER_NAME_PLACEHOLDER_RE.search(text))


def _strip_remaining_placeholders(text: str) -> str:
    return _BRACE_RE.sub("", _TAG_RE.sub("", text))


def _add_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)



def expand_gender_placeholder(text: str, gender: str) -> str:
    """
    将性别占位符展开为具体的性别变体。
    
    参数:
        text: 原始文本，可能包含 {Male=He;Female=She} 格式的占位符
        gender: "male" 或 "female"
    
    返回:
        展开后的文本
    """
    def replace_match(m):
        # 匹配格式：{Male=xxx;Female=yyy} 或 {Female=xxx;Male=yyy}
        male_first, female_first = m.group(1), m.group(2)
        female_second, male_second = m.group(3), m.group(4)
        
        if male_first is not None:
            # 格式: {Male=xxx;Female=yyy}
            return male_first if gender == "male" else female_first
        else:
            # 格式: {Female=xxx;Male=yyy}
            return male_second if gender == "male" else female_second
    
    return _GENDER_PLACEHOLDER_RE.sub(replace_match, text)


def has_gender_placeholder(text: str) -> bool:
    """检查文本是否包含性别占位符。"""
    return bool(_GENDER_PLACEHOLDER_RE.search(text))


def clean_en_text(text: str) -> str:
    text = _TAG_RE.sub("", text)
    text = _BRACE_RE.sub("", text)
    return text


def _iter_utf16le_ascii_runs(blob: bytes, min_len: int = 4) -> Iterable[str]:
    run: list[str] = []
    i = 0
    n = len(blob)
    while i + 1 < n:
        lo = blob[i]
        hi = blob[i + 1]
        if hi == 0 and 32 <= lo <= 126:
            run.append(chr(lo))
        else:
            if len(run) >= min_len:
                yield "".join(run)
            run.clear()
        i += 2
    if len(run) >= min_len:
        yield "".join(run)


def _extract_blob_text_candidates(blob: bytes) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for m in _PRINTABLE_ASCII_RE.finditer(blob):
        s = m.group(0).decode("ascii", errors="ignore").strip()
        if not s:
            continue
        if len(s) > 240:
            continue
        if all(ch in "0123456789abcdefABCDEF" for ch in s) and len(s) >= 8:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)

    for s in _iter_utf16le_ascii_runs(blob):
        s = s.strip()
        if not s or len(s) > 240:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)

    return out


def _is_probably_human_text(text: str) -> bool:
    if len(text) < 12:
        return False
    if any(sep in text for sep in ("/", "\\", "\t", "\n", "\r")):
        return False
    low = text.lower()
    if low.startswith("game/") or low.endswith((".uasset", ".png", ".jpg", ".atlas")):
        return False
    words = [w for w in text.split(" ") if w]
    if len(words) < 3:
        return False
    letters = sum(ch.isalpha() for ch in text)
    if letters < 8:
        return False
    return True


def _is_text_key_like(text: str) -> bool:
    if not text or " " in text:
        return False
    if any(sep in text for sep in ("/", "\\", "\t", "\n", "\r")):
        return False
    if len(text) < 8 or len(text) > 160:
        return False
    low = text.lower()
    if any(hint in low for hint in ("_text", "_title", "_name", "_desc", "summary")):
        return True
    return bool(_TEXT_KEY_TOKEN_RE.fullmatch(text))


def _pick_text_from_blob(blob: bytes) -> str | None:
    candidates = _extract_blob_text_candidates(blob)
    if not candidates:
        return None

    human_candidates = [c for c in candidates if _is_probably_human_text(c)]
    if human_candidates:
        human_candidates.sort(key=lambda s: (len(s.split()), len(s)), reverse=True)
        return human_candidates[0]

    key_candidates = [c for c in candidates if _is_text_key_like(c)]
    if key_candidates:
        def _key_score(s: str) -> tuple[int, int]:
            low = s.lower()
            if "title" in low:
                pri = 4
            elif "name" in low:
                pri = 3
            elif "summary" in low:
                pri = 2
            elif "describe" in low or "desc" in low:
                pri = 1
            elif "_text" in low:
                pri = 0
            else:
                pri = -1
            return pri, len(s)

        key_candidates.sort(
            key=_key_score,
            reverse=True,
        )
        return key_candidates[0]

    return None


def _extract_map(obj: object) -> Dict[str, str]:
    """从多种可能的 JSON 结构抽取 {text_key: text} 映射。"""
    if isinstance(obj, dict):
        if all(isinstance(v, str) for v in obj.values()):
            return {str(k): v for k, v in obj.items()}
        extracted: Dict[str, str] = {}
        for k, v in obj.items():
            if isinstance(v, dict):
                text = (
                    v.get("Text")
                    or v.get("Content")
                    or v.get("Value")
                    or v.get("TextValue")
                )
                if isinstance(text, str):
                    extracted[str(k)] = text
        return extracted
    if isinstance(obj, list):
        extracted: Dict[str, str] = {}
        for item in obj:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("Key")
                or item.get("TextKey")
                or item.get("TextMapId")
                or item.get("ID")
            )
            text = (
                item.get("Text")
                or item.get("Content")
                or item.get("Value")
                or item.get("TextValue")
            )
            if isinstance(key, str) and isinstance(text, str):
                extracted[key] = text
        return extracted
    return {}


def _load_map(path: Path) -> Dict[str, str]:
    if path.suffix.lower() == ".db":
        return _load_sqlite_map(path)
    obj = json.loads(path.read_text(encoding="utf-8"))
    return _extract_map(obj)


def _load_sqlite_map(path: Path) -> Dict[str, str]:
    """从 SQLite 数据库提取文本。常见表名为 'Table' 或 'data'，列名为 'TextMapId'/'Key' 和 'Text'/'Content'。"""
    try:
        # 预检：检查是否截断
        with open(path, "rb") as f:
            header = f.read(100)
            if len(header) < 100 or header[:15] != b"SQLite format 3":
                return {}
            # 第 28-31 字节是文件大小（以页为单位，大端序）
            page_count = int.from_bytes(header[28:32], "big")
            # 第 16-17 字节是页大小
            page_size = int.from_bytes(header[16:18], "big")
            if page_size == 1: page_size = 65536
            
            expected_size = page_count * page_size
            actual_size = path.stat().st_size
            if actual_size < expected_size:
                print(f"⚠️ 警告: 数据库 {path.name} 似乎已截断 (预期 {expected_size} 字节, 实际 {actual_size} 字节)。这通常是由于解包不完整导致的。")
                # 即使截断，sqlite3 有时也能读到前面的数据，继续尝试

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()

            # 探测表名
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            if not tables:
                return {}

            # 优先语言常见表，但会遍历所有表
            preferred = ["Table", "data", "lang_text", "text", "MultiText"]
            table_order = [t for t in preferred if t in tables] + [t for t in tables if t not in preferred]

            mapping: Dict[str, str] = {}

            for table_name in table_order:
                try:
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    cols_info = cursor.fetchall()
                except Exception:
                    continue

                if not cols_info:
                    continue

                cols = {r[1].lower(): r[1] for r in cols_info}
                decl_types = {r[1].lower(): (r[2] or "") for r in cols_info}

                id_col = cols.get("textid") or cols.get("textmapid") or cols.get("key") or cols.get("id")
                text_col = cols.get("text") or cols.get("content") or cols.get("value")
                blob_col = next(
                    (r[1] for r in cols_info if "blob" in (r[2] or "").lower() or r[1].lower() == "bindata"),
                    None,
                )

                # 1) 标准文本表（优先）
                if id_col and text_col and "blob" not in decl_types.get(text_col.lower(), "").lower():
                    try:
                        cursor.execute(f"SELECT {id_col}, {text_col} FROM {table_name}")
                        for k, v in cursor.fetchall():
                            if k is None or v is None or isinstance(v, (bytes, bytearray)):
                                continue
                            text = str(v).strip()
                            if not text:
                                continue
                            mapping.setdefault(str(k), text)
                    except Exception:
                        pass
                    continue

                # 2) BinData/FlatBuffers 启发式提取
                if id_col and blob_col:
                    try:
                        cursor.execute(f"SELECT {id_col}, {blob_col} FROM {table_name}")
                        for k, blob in cursor.fetchall():
                            if k is None or not isinstance(blob, (bytes, bytearray)):
                                continue
                            picked = _pick_text_from_blob(blob)
                            if picked:
                                mapping.setdefault(str(k), picked)
                    except Exception:
                        pass
                    continue

                # 3) 极端兜底：猜测前两列是 key/value
                if len(cols_info) >= 2:
                    c0 = cols_info[0][1]
                    c1 = cols_info[1][1]
                    c1_type = (cols_info[1][2] or "").lower()
                    if "blob" in c1_type:
                        continue
                    try:
                        cursor.execute(f"SELECT {c0}, {c1} FROM {table_name}")
                        for k, v in cursor.fetchall():
                            if k is None or v is None or isinstance(v, (bytes, bytearray)):
                                continue
                            text = str(v).strip()
                            if not text:
                                continue
                            mapping.setdefault(str(k), text)
                    except Exception:
                        pass

            return mapping
        finally:
            conn.close()
    except Exception as e:
        print(f"警告: 无法读取 SQLite 数据库 {path.name}: {e}")
        return {}



def load_plot_audio_map(data_root: Path) -> Dict[str, str]:
    from ludiglot.adapters.wuthering_waves.data_mapper import WutheringDataMapper

    return WutheringDataMapper(data_root).load_plot_audio_map()

def _resolve_audio_hash(
    text_key: str,
    plot_audio: Dict[str, str] | None,
    voice_map: Dict[str, list[str]] | None,
) -> tuple[str | None, str | None]:
    from ludiglot.adapters.wuthering_waves.audio_strategy import WutheringAudioStrategy

    strategy = WutheringAudioStrategy()
    event_name: str | None = None
    if plot_audio and text_key in plot_audio:
        event_name = plot_audio[text_key]
    elif voice_map and text_key in voice_map and voice_map[text_key]:
        event_name = voice_map[text_key][0]
    if not event_name:
        return None, None
    event_name = strategy.parse_event_name(event_name)
    if not event_name:
        return None, None
    audio_hash = str(strategy.hash_name(event_name))
    return audio_hash, event_name


def find_multitext_paths(data_root: Path) -> Tuple[Path, Path]:
    from ludiglot.adapters.wuthering_waves.data_mapper import WutheringDataMapper

    paths = WutheringDataMapper(data_root).parse()
    return paths.en_text, paths.zh_text

def build_text_db_from_root(data_root: Path) -> Dict[str, dict]:
    en_json, zh_json = find_multitext_paths(data_root)
    plot_audio = load_plot_audio_map(data_root)
    voice_map = build_voice_map_from_configdb(data_root)
    return build_text_db(en_json, zh_json, plot_audio=plot_audio, voice_map=voice_map)


def build_text_db_from_root_all(data_root: Path) -> Dict[str, dict]:
    from ludiglot.adapters.wuthering_waves.data_mapper import WutheringDataMapper

    mapper = WutheringDataMapper(data_root)
    roots = mapper.text_source_roots()

    plot_audio = load_plot_audio_map(data_root)
    # 启用缓存，确保 voice_map 可读
    cache_path = data_root.parent / "cache" / "voice_map_v6.json"
    voice_map = build_voice_map_from_configdb(data_root, cache_path=cache_path)
    db: Dict[str, dict] = {}

    def merge_partial(partial: Dict[str, dict]) -> None:
        for key, payload in partial.items():
            if key not in db:
                db[key] = payload
            else:
                db[key]["matches"].extend(payload.get("matches", []))

    def scan_pair(en_dir: Path, zh_dir: Path):
        if not en_dir.exists() or not zh_dir.exists():
            return
        for ext in ("*.json", "*.db"):
            for en_path in en_dir.rglob(ext):
                rel = en_path.relative_to(en_dir)
                zh_path = zh_dir / rel
                if not zh_path.exists():
                    continue
                try:
                    partial = build_text_db(en_path, zh_path, plot_audio=plot_audio, voice_map=voice_map)
                    merge_partial(partial)
                except Exception:
                    continue

    for r in roots:
        if not r.exists(): continue
        for pair in mapper.language_pairs:
            scan_pair(r / pair.en, r / pair.zh)

    for db_path in mapper.root_blob_db_paths():
        try:
            en_map = _load_sqlite_map(db_path)
            if not en_map:
                continue
            partial = build_text_db_from_maps(
                en_map,
                {},
                db_path.name,
                plot_audio=plot_audio,
                voice_map=voice_map,
            )
            merge_partial(partial)
        except Exception:
            continue

    # 兜底：如果完全没有扫到，或者扫出的是空的，尝试使用 MultiText 逻辑兜底
    if not db:
        try:
            return build_text_db_from_root(data_root)
        except Exception:
            pass
            
    return db



def build_text_db_from_maps(
    en_map: Dict[str, str],
    zh_map: Dict[str, str],
    source_json: str,
    plot_audio: Dict[str, str] | None = None,
    voice_map: Dict[str, list[str]] | None = None,
) -> Dict[str, dict]:
    db: Dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()

    def add_match(norm_key: str, text_key: str, match: dict) -> None:
        if not norm_key:
            return
        sig = (norm_key, text_key)
        if sig in seen:
            return
        seen.add(sig)
        if norm_key not in db:
            db[norm_key] = {"key": norm_key, "matches": [match]}
        else:
            db[norm_key]["matches"].append(match)

    all_keys = set(en_map.keys()) | set(zh_map.keys())
    for text_key in all_keys:
        en_text = en_map.get(text_key, "")
        zh_text = zh_map.get(text_key, "")
        if not (isinstance(en_text, str) or isinstance(zh_text, str)):
            continue

        if not isinstance(en_text, str):
            en_text = ""
        if not isinstance(zh_text, str):
            zh_text = ""

        audio_hash, audio_event = _resolve_audio_hash(text_key, plot_audio, voice_map)
        match = {
            "text_key": text_key,
            "official_en": en_text,  # 存储原始英文（包含HTML标记）
            "official_cn": zh_text,
            "source_json": source_json,
            "audio_rule": "vo_{text_key}",
            "audio_hash": audio_hash,
            "audio_event": audio_event,
            "terms": [],
        }

        # 生成规范化键列表
        keys_to_add: list[str] = []
        
        # 原有逻辑：清理HTML标签和所有花括号占位符
        cleaned_en = clean_en_text(en_text) if en_text else ""
        cleaned_zh = clean_en_text(zh_text) if zh_text else ""
        
        key_en = normalize_en(cleaned_en) if cleaned_en else ""
        key_zh = normalize_en(cleaned_zh) if cleaned_zh else ""
        
        if key_en:
            _add_unique(keys_to_add, key_en)
        if key_zh:
            _add_unique(keys_to_add, key_zh)

        if en_text and has_player_name_placeholder(en_text):
            for player_name in ("Rover", ""):
                expanded = expand_player_name_placeholder(en_text, player_name)
                expanded_key = normalize_en(_strip_remaining_placeholders(expanded))
                _add_unique(keys_to_add, expanded_key)
        if zh_text and has_player_name_placeholder(zh_text):
            for player_name in ("漂泊者", ""):
                expanded = expand_player_name_placeholder(zh_text, player_name)
                expanded_key = normalize_en(_strip_remaining_placeholders(expanded))
                _add_unique(keys_to_add, expanded_key)

        # 新增：为包含性别占位符的文本生成 male 和 female 变体的键
        if en_text and has_gender_placeholder(en_text):
            for gender in ("male", "female"):
                expanded = expand_gender_placeholder(en_text, gender)
                expanded_key = normalize_en(_strip_remaining_placeholders(expanded))
                _add_unique(keys_to_add, expanded_key)
        if zh_text and has_gender_placeholder(zh_text):
            for gender in ("male", "female"):
                expanded = expand_gender_placeholder(zh_text, gender)
                expanded_key = normalize_en(_strip_remaining_placeholders(expanded))
                _add_unique(keys_to_add, expanded_key)
        
        if not keys_to_add:
            continue

        for k in keys_to_add:
            add_match(k, text_key, match)
    return db


def build_text_db(
    en_json: Path,
    zh_json: Path,
    plot_audio: Dict[str, str] | None = None,
    voice_map: Dict[str, list[str]] | None = None,
) -> Dict[str, dict]:
    """从 MultiText 构建数据库（兼容多种 JSON 结构）。"""
    en_map = _load_map(en_json)
    zh_map = _load_map(zh_json)
    return build_text_db_from_maps(en_map, zh_map, en_json.name, plot_audio, voice_map)


def save_text_db(db: Dict[str, dict], output: Path) -> None:
    output.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
