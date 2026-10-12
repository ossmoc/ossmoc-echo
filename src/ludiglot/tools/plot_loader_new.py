
import json
import sqlite3
import re
from pathlib import Path
from typing import Dict, Iterable, Any

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

def load_plot_audio_map(data_root: Path) -> Dict[str, str]:
    json_path = data_root / "ConfigDB" / "PlotAudio.json"
    db_path = data_root / "ConfigDB" / "db_plot_audio.db"
    
    mapping: Dict[str, str] = {}
    
    # 1. 尝试 JSON
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            for item in _iter_items(payload):
                text_key = item.get("TextKey") or item.get("TextMapId") or item.get("TextId") or item.get("Key")
                file_name = item.get("FileName") or item.get("AudioEventName") or item.get("AudioEvent") or item.get("Voice")
                if text_key and file_name:
                    mapping[str(text_key)] = str(file_name)
        except Exception: pass
            
    # 2. 尝试 SQLite
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            
            # 预编译正则以提高效率
            audio_pat = re.compile(rb'(?:play_vo_|vo_)[a-zA-Z0-9_]{3,}')
            
            for tbl in tables:
                cursor.execute(f"PRAGMA table_info({tbl})")
                cols_info = cursor.fetchall()
                col_names = [c[1].lower() for c in cols_info]
                
                # 寻找 ID 列
                id_idx = -1
                for idx, name in enumerate(col_names):
                    if name in ("id", "textkey", "key", "textmapid"):
                        id_idx = idx
                        break
                
                if id_idx == -1: continue
                
                # 寻找音频列或 BLOB 列
                audio_idx = -1
                blob_idx = -1
                for idx, name in enumerate(col_names):
                    if name in ("filename", "audioeventname", "audioevent", "voice"):
                        audio_idx = idx
                    if "blob" in (cols_info[idx][2] or "").lower() or name == "bindata":
                        blob_idx = idx
                
                cursor.execute(f"SELECT * FROM {tbl}")
                while True:
                    rows = cursor.fetchmany(1000)
                    if not rows: break
                    for row in rows:
                        tid = row[id_idx]
                        if not tid: continue
                        
                        # 首先尝试列映射
                        event = None
                        if audio_idx != -1 and row[audio_idx]:
                            event = str(row[audio_idx])
                        
                        # 如果没有列映射，尝试 BLOB 扫描
                        if not event and blob_idx != -1 and isinstance(row[blob_idx], (bytes, bytearray)):
                            m = audio_pat.findall(row[blob_idx])
                            if m:
                                event = m[0].decode('ascii', errors='ignore')
                        
                        if event:
                            mapping[str(tid)] = event
            conn.close()
        except Exception: pass
            
    return mapping
