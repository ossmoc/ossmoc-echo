from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ludiglot.core.config import AppConfig


def _iter_items(payload: Any) -> Iterable[dict]:
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


def _latest_mtime(paths: Iterable[Path]) -> float:
    latest = 0.0
    for path in paths:
        try:
            latest = max(latest, path.stat().st_mtime)
        except Exception:
            continue
    return latest


def _normalize_voice_event(path: str) -> str | None:
    if not path or not isinstance(path, str):
        return None
    # 移除路径和扩展名 (例如 "Aki/Audio/Event/play_vo_test.bnk" -> "play_vo_test")
    segment = path.rsplit("/", 1)[-1]
    if "." in segment:
        segment = segment.split(".")[0]
    return segment


# Heuristic patterns for blob scanning
_TEXT_KEY_PATTERN = re.compile(rb'[A-Z][a-zA-Z0-9_]{3,}_[0-9]{3,}(?:_[a-zA-Z0-9_]+)?')
_AUDIO_PATTERN = re.compile(rb'play_[a-zA-Z0-9_]+')


def build_voice_map_from_configdb(data_root: Path, cache_path: Path | None = None) -> Dict[str, list[str]]:
    configdb = data_root / "ConfigDB"
    config_dir = data_root / "Config"
    
    scan_dirs = []
    if configdb.exists(): scan_dirs.append(configdb)
    if config_dir.exists(): scan_dirs.append(config_dir)
    
    if not scan_dirs:
        return {}

    json_paths = []
    db_paths = []
    for d in scan_dirs:
        json_paths.extend(d.rglob("*.json"))
        db_paths.extend(d.rglob("*.db"))

    all_paths = json_paths + db_paths
    latest = _latest_mtime(all_paths)
    
    # 增加版本号以强制重建 (v6: 增加 Blob 启发式扫描)
    cache_version = "v6"
    
    if cache_path and cache_path.exists():
        try:
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            if cache_data.get("mtime") == latest and cache_data.get("version") == cache_version:
                return cache_data.get("mapping", {})
        except Exception:
            pass

    voice_map: Dict[str, list[str]] = {}
    
    audio_keys = {"Voice", "FileName", "Audio", "AudioEventName", "AudioEvent", "WwiseEvent", "EventName", "Sound"}
    text_keys = {"Id", "Key", "TextKey", "TextId", "TextMapId", "DialogId", "EntryId"}

    for path in all_paths:
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in _iter_items(data):
                    _process_item(item, voice_map, text_keys, audio_keys, path.name)
            except Exception:
                continue
        elif path.suffix.lower() == ".db":
            try:
                _scan_db_file(path, voice_map, text_keys, audio_keys)
            except Exception:
                continue
    
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "mtime": latest,
            "version": cache_version,
            "mapping": voice_map
        }, ensure_ascii=False), encoding="utf-8")
        
    return voice_map


def _process_item(item: dict, voice_map: Dict[str, list[str]], text_keys: set, audio_keys: set, filename: str) -> None:
    found_events = []
    for ak in audio_keys:
        val = item.get(ak)
        if val and isinstance(val, str) and val.strip():
            normalized_event = _normalize_voice_event(val)
            if normalized_event:
                found_events.append(normalized_event)
        elif val and isinstance(val, list):
            for v in val:
                if isinstance(v, str):
                    normalized_event = _normalize_voice_event(v)
                    if normalized_event:
                        found_events.append(normalized_event)
        elif val and isinstance(val, dict):
            for v_val in val.values():
                if isinstance(v_val, str):
                    normalized_event = _normalize_voice_event(v_val)
                    if normalized_event:
                        found_events.append(normalized_event)

    if not found_events:
        return
        
    found_text_keys = []
    for tk in text_keys:
        val = item.get(tk)
        if isinstance(val, str) and val.strip():
            found_text_keys.append(val.strip())
        elif isinstance(val, (int, float)):
            found_text_keys.append(str(val))

    for t_key in found_text_keys:
        if t_key.isdigit() and len(t_key) < 5:
            continue
        for ev in found_events:
            if ev not in voice_map.get(t_key, []):
                voice_map.setdefault(t_key, []).append(ev)


def _scan_db_file(path: Path, voice_map: Dict[str, list[str]], text_keys: set, audio_keys: set) -> None:
    if path.stat().st_size < 100:
        return

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            cols_info = cursor.fetchall()
            cols = [c[1] for c in cols_info]
            cols_map = {c.lower(): c for c in cols}
            
            target_text_cols = [cols_map[k.lower()] for k in text_keys if k.lower() in cols_map]
            target_audio_cols = [cols_map[k.lower()] for k in audio_keys if k.lower() in cols_map]
            
            if target_text_cols and target_audio_cols:
                select_cols = target_text_cols + target_audio_cols
                cursor.execute(f"SELECT {', '.join(select_cols)} FROM {table}")
                while True:
                    rows = cursor.fetchmany(1000)
                    if not rows: break
                    for row in rows:
                        t_keys = [str(val).strip() for val in row[:len(target_text_cols)] if val is not None]
                        v_events = [_normalize_voice_event(str(val)) for val in row[len(target_text_cols):] if val is not None]
                        for tk in t_keys:
                            if tk.isdigit() and len(tk) < 5: continue
                            for ev in v_events:
                                if ev and ev not in voice_map.get(tk, []):
                                    voice_map.setdefault(tk, []).append(ev)
            
            blob_cols = [c[1] for c in cols_info if "blob" in (c[2] or "").lower() or "bindata" in c[1].lower() or "data" in c[1].lower()]
            if blob_cols:
                cursor.execute(f"SELECT {', '.join(blob_cols)} FROM {table}")
                while True:
                    rows = cursor.fetchmany(500)
                    if not rows: break
                    for row in rows:
                        for val in row:
                            if isinstance(val, (bytes, bytearray)):
                                k_list = _TEXT_KEY_PATTERN.findall(val)
                                a_list = _AUDIO_PATTERN.findall(val)
                                if k_list and a_list:
                                    for kb in k_list:
                                        tk = kb.decode('utf-8', errors='ignore')
                                        if tk.isdigit() and len(tk) < 5: continue 
                                        for ab in a_list:
                                            ev = _normalize_voice_event(ab.decode('utf-8', errors='ignore'))
                                            if ev and ev not in voice_map.get(tk, []):
                                                voice_map.setdefault(tk, []).append(ev)
    finally:
        conn.close()


def _resolve_events_for_text_key(text_key: str, cfg: AppConfig | None) -> list[str]:
    """返回所有可能的音频事件名列表，按优先级排序。"""
    if not cfg or not cfg.data_root:
        return []
    
    candidates = []
    
    from ludiglot.core.text_builder import load_plot_audio_map
    
    # 1. 尝试从 PlotAudio 获取官方指定的 Event (最高优先级)
    plot_audio = load_plot_audio_map(cfg.data_root)
    voice_event = plot_audio.get(text_key)
    if voice_event:
        candidates.append(voice_event)
        
    # 2. 尝试从 voice_map 获取候选
    cache_path = cfg.data_root.parent / "cache" / "voice_map_v6.json"
    voice_map = build_voice_map_from_configdb(cfg.data_root, cache_path=cache_path)
    extra = voice_map.get(text_key) or []
    for ev in extra:
        if ev not in candidates:
            candidates.append(ev)
            
    # --- 增强：性别版本自动补充 ---
    pref = (cfg.gender_preference or "female").lower()
    auto_cands = []
    for c in candidates:
        if "nanzhu" in c.lower() and pref == "female":
            auto_cands.append(c.lower().replace("nanzhu", "nvzhu"))
        elif "nvzhu" in c.lower() and pref == "male":
            auto_cands.append(c.lower().replace("nvzhu", "nanzhu"))
    
    for ac in auto_cands:
        if ac not in candidates:
            candidates.append(ac)
            
    # --- 全局重排优先性能 ---
    f_pats = ["_f_", "nvzhu", "roverf", "_female"]
    m_pats = ["_m_", "nanzhu", "roverm", "_male"]
    target_pats = f_pats if pref == "female" else m_pats
    other_pats = m_pats if pref == "female" else f_pats

    def cand_priority(n):
        nl = n.lower()
        if any(w in nl for w in target_pats): return 0
        if any(w in nl for w in other_pats): return 2
        return 1

    candidates.sort(key=cand_priority)
    return candidates


def collect_all_voice_event_names(data_root: Path | None, voice_map: Dict[str, list[str]]) -> list[str]:
    """收集所有已知的音频事件名（用于构建索引）。"""
    events: list[str] = []
    if data_root:
        try:
             from ludiglot.core.text_builder import load_plot_audio_map
             plot_audio = load_plot_audio_map(data_root)
             events.extend([str(v) for v in plot_audio.values() if v])
        except Exception:
             # Plot audio map is optional; if it fails to load, continue without it
             pass
    
    for items in voice_map.values():
        if isinstance(items, list):
             events.extend([str(v) for v in items if v])
             
    # Dedup and sort for stability
    return sorted(list(set(events)))
