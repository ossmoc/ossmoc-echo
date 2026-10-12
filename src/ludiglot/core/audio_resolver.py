from __future__ import annotations

import json
import time
from pathlib import Path
from typing import NamedTuple, Any, List

from ludiglot.core.config import AppConfig
from ludiglot.core.voice_map import (
    _resolve_events_for_text_key,
    build_voice_map_from_configdb,
    collect_all_voice_event_names,
)
from ludiglot.core.voice_event_index import VoiceEventIndex
from ludiglot.core.audio_mapper import AudioCacheIndex
from ludiglot.adapters.wuthering_waves.audio_strategy import WutheringAudioStrategy
from ludiglot.core.audio_extract import (
    find_wem_by_hash, find_bnk_for_event, find_wem_by_event_name, 
    default_wwiser_path, find_txtp_for_event, 
    convert_single_wem_to_wav, generate_txtp_for_bnk, convert_txtp_to_wav
)
import shutil

class AudioResolution(NamedTuple):
    hash_value: int
    event_name: str
    source_type: str  # 'cache', 'wem', 'bnk', 'unknown'


_EVENT_INDEX_CACHE: dict[str, VoiceEventIndex] = {}


def get_voice_event_index(config: AppConfig) -> VoiceEventIndex | None:
    if not config.audio_bnk_root and not config.audio_txtp_cache:
        return None

    cache_path = config.audio_cache_path / "voice_event_index.json" if config.audio_cache_path else None
    key = f"{config.audio_bnk_root}|{config.audio_txtp_cache}|{config.data_root}|{cache_path}"
    cached = _EVENT_INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    voice_map = build_voice_map_from_configdb(config.data_root) if config.data_root else {}
    extra_names = collect_all_voice_event_names(config.data_root, voice_map)
    index = VoiceEventIndex(
        bnk_root=config.audio_bnk_root,
        txtp_root=config.audio_txtp_cache,
        cache_path=cache_path,
        extra_names=extra_names,
    )
    index.load_or_build()
    _EVENT_INDEX_CACHE.clear()
    _EVENT_INDEX_CACHE[key] = index
    return index


class AudioResolver:
    def __init__(
        self,
        config: AppConfig,
        voice_event_index: VoiceEventIndex = None,
        audio_index: AudioCacheIndex | None = None,
    ):
        self.config = config
        self.strategy = WutheringAudioStrategy()
        self._audio_index: AudioCacheIndex | None = audio_index
        self._voice_event_index = voice_event_index
        self._cache_meta_loaded = False
        self._cache_meta: dict[str, dict[str, Any]] = {}
        self._cache_meta_path: Path | None = (
            self.config.audio_cache_path / "audio_resolver_cache_meta.json"
            if self.config.audio_cache_path
            else None
        )
        
    @property
    def audio_index(self) -> AudioCacheIndex | None:
        if not self.config.audio_cache_path:
            return None
        if self._audio_index is None:
            self._audio_index = AudioCacheIndex(
                self.config.audio_cache_path,
                index_path=self.config.audio_cache_index_path,
                max_mb=self.config.audio_cache_max_mb
            )
            self._audio_index.load()
            self._audio_index.scan()
        return self._audio_index

    def get_candidates(self, text_key: str | None, db_event: str | None = None) -> List[str]:
        """依据 TextKey 和数据库已知 Event，生成经过性别排序的候选列表。"""
        events = []
        if text_key:
            events = _resolve_events_for_text_key(text_key, self.config)
        
        if db_event:
            clean_db_event = self.strategy.parse_event_name(db_event)
            if clean_db_event and clean_db_event not in events:
                # 如果是通过 text_key 解析出来的，保持其优先级。
                # 数据库自带的 Event 放在 Stage 0 的末尾，作为参考而非绝对权威。
                events.append(clean_db_event)

        # 0. 准备阶段容器
        stages: list[list[str]] = [
            events,  # Stage 0: DB/TextMap (Highest Confidence)
            [],      # Stage 1: Strategy Heuristics (High Confidence)
            []       # Stage 2: Fuzzy/Index Search (Low Confidence)
        ]

        # 1. 填充 Stage 1 (Strategy) 和 Stage 2 (Fuzzy)
        if text_key:
            stages[1].extend(self.strategy.build_names(text_key, None))

            if events and self._voice_event_index:
                 seed = events[0]
                 stages[2].extend(self._voice_event_index.find_candidates(text_key, seed, limit=8, min_score=0.9))

        elif db_event:
            stages[1].extend(self.strategy.build_names(None, db_event))

        # 2. 定义性别排序优先级
        pref = (self.config.gender_preference or "female").lower()
        f_pats = ["_f", "nvzhu", "roverf", "_female"]
        m_pats = ["_m", "nanzhu", "roverm", "_male"]
        target_pats = f_pats if pref == "female" else m_pats
        other_pats = m_pats if pref == "female" else f_pats

        def get_priority(n: str) -> int:
            nl = n.lower()
            if any(w in nl for w in target_pats): return 0  # 命中偏好性别
            if any(w in nl for w in other_pats): return 2   # 命中相反性别
            return 1                                        # 中性/未知

        # 3. 分阶段处理：生成性别变体 -> 组内排序 -> 合并
        final_names: list[str] = []
        seen = set()

        for stage_names in stages:
            # A. 为当前阶段的每个候选项生成性别变体 (扩充)
            # 这样做是为了保证：Stage 1 的 "Name_F" 仍然属于 Stage 1，优于 Stage 2 的任何东西
            expanded_stage = []
            for name in stage_names:
                if not name: continue
                expanded_stage.append(name)
                
                nl = name.lower()
                # 如果本身是中性的，尝试生成性别变体
                if not any(w in nl for w in ["_f", "_m", "nanzhu", "nvzhu", "roverf", "roverm"]):
                    expanded_stage.append(f"{name}_f")
                    expanded_stage.append(f"{name}_m")
            
            # B. 组内排序
            stage_sorted = sorted(expanded_stage, key=get_priority)
            
            # C. 添加到最终列表 (去重)
            for name in stage_sorted:
                if name not in seen:
                    seen.add(name)
                    final_names.append(name)
            
        return final_names

    def _load_cache_meta(self) -> None:
        if self._cache_meta_loaded:
            return
        self._cache_meta_loaded = True
        self._cache_meta = {}
        if not self._cache_meta_path or not self._cache_meta_path.exists():
            return
        try:
            raw = json.loads(self._cache_meta_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return
        for key, val in entries.items():
            try:
                hash_key = str(int(key))
            except Exception:
                continue
            if not isinstance(val, dict):
                continue
            event_name = str(val.get("event_name", "")).strip()
            source_type = str(val.get("source_type", "")).strip()
            try:
                updated_at = float(val.get("updated_at", 0.0) or 0.0)
            except Exception:
                updated_at = 0.0
            self._cache_meta[hash_key] = {
                "event_name": event_name,
                "source_type": source_type,
                "updated_at": updated_at,
            }

    def _save_cache_meta(self) -> None:
        if not self._cache_meta_path:
            return
        payload = {
            "generated_at": time.time(),
            "entries": self._cache_meta,
        }
        self._cache_meta_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_meta_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize_event_name(self, event_name: str | None) -> str:
        if not event_name:
            return ""
        parsed = self.strategy.parse_event_name(event_name)
        if parsed:
            return parsed.strip().lower()
        return str(event_name).strip().lower()

    def _is_cache_trusted(self, hash_value: int, event_name: str | None = None) -> bool:
        self._load_cache_meta()
        entry = self._cache_meta.get(str(int(hash_value)))
        if not entry:
            return False
        if not event_name:
            return True
        expected = self._normalize_event_name(event_name)
        if not expected:
            return True
        cached_event = self._normalize_event_name(str(entry.get("event_name", "")))
        return cached_event == expected

    def _mark_cache_trusted(self, hash_value: int, event_name: str | None, source_type: str) -> None:
        normalized_event = self._normalize_event_name(event_name)
        if not normalized_event:
            return
        self._load_cache_meta()
        self._cache_meta[str(int(hash_value))] = {
            "event_name": normalized_event,
            "source_type": source_type,
            "updated_at": time.time(),
        }
        self._save_cache_meta()

    def get_cached_path(
        self,
        hash_value: int,
        event_name: str | None = None,
        *,
        trusted_only: bool = True,
    ) -> Path | None:
        """返回缓存文件路径；默认仅返回与事件名一致的可信缓存。"""
        index = self.audio_index
        if not index:
            return None
        cached = index.find(hash_value)
        if not cached:
            return None
        if trusted_only and event_name and not self._is_cache_trusted(hash_value, event_name):
            return None
        return cached

    def resolve(self, text_key: str | None, db_event: str | None = None, db_hash: int | None = None) -> AudioResolution | None:
        """全流程解析音频。

        优化：避免慢速文件系统扫描，优先使用缓存和直接路径。
        """
        if text_key and not db_event and db_hash is None and not _resolve_events_for_text_key(text_key, self.config):
            return None

        candidates = self.get_candidates(text_key, db_event)
        
        final_candidates: list[tuple[str, int]] = []
        for name in candidates:
            final_candidates.append((name, self.strategy.hash_name(name)))

        index = self.audio_index
        wem_root = self.config.audio_wem_root

        def _gender_tag(name: str | None) -> str | None:
            if not name:
                return None
            nl = name.lower()
            if any(tok in nl for tok in ("nvzhu", "roverf", "_female")):
                return "female"
            if any(tok in nl for tok in ("nanzhu", "roverm", "_male")):
                return "male"
            return None

        # === 第一优先级：数据库显式 hash/event（最高置信） ===
        if db_hash is not None:
            try:
                db_hash_int = int(db_hash)
            except (TypeError, ValueError):
                db_hash_int = None

            fallback_event = self.strategy.parse_event_name(db_event) if db_event else None
            if not fallback_event:
                fallback_event = final_candidates[0][0] if final_candidates else "unknown_from_db"

            # 对数据库明确给出的事件：仅信任有来源标记的缓存，避免旧缓存错配。
            if db_hash_int is not None and index and self._is_cache_trusted(db_hash_int, fallback_event):
                cached = index.find(db_hash_int)
                if cached:
                    return AudioResolution(db_hash_int, fallback_event, 'cache')

            # 优先检查直接 WEM（即使有旧缓存，也优先资源直连）
            if db_hash_int is not None and wem_root:
                direct = wem_root / f"{db_hash_int}.wem"
                if direct.exists():
                    return AudioResolution(db_hash_int, fallback_event, 'wem')

            # 主角语音：若数据库事件与性别偏好冲突，优先使用已排序的候选事件。
            pref_gender = (self.config.gender_preference or "female").lower()
            preferred_name, preferred_hash = final_candidates[0] if final_candidates else (None, None)
            preferred_tag = _gender_tag(preferred_name)
            db_tag = _gender_tag(fallback_event)
            if (
                preferred_name
                and preferred_hash is not None
                and preferred_tag
                and db_tag
                and preferred_tag == pref_gender
                and db_tag != preferred_tag
            ):
                if index and self._is_cache_trusted(preferred_hash, preferred_name):
                    cached = index.find(preferred_hash)
                    if cached:
                        return AudioResolution(preferred_hash, preferred_name, "cache")
                if wem_root:
                    direct = wem_root / f"{preferred_hash}.wem"
                    if direct.exists():
                        return AudioResolution(preferred_hash, preferred_name, "wem")
                return AudioResolution(preferred_hash, preferred_name, "computed")

            # 返回db_hash作为后备，让播放器尝试
            if db_hash_int is not None:
                return AudioResolution(db_hash_int, fallback_event, "db_fallback")

        if not final_candidates:
            return None

        # === 第二优先级：缓存查找（仅信任有来源标记的条目） ===
        for name, h in final_candidates:
            if index and self._is_cache_trusted(h, name):
                cached = index.find(h)
                if cached:
                    return AudioResolution(h, name, 'cache')

        # === 第三优先级：直接路径查找（O(1)） ===
        if wem_root:
            for name, h in final_candidates:
                direct = wem_root / f"{h}.wem"
                if direct.exists():
                    return AudioResolution(h, name, 'wem')

        if db_event:
            return AudioResolution(final_candidates[0][1], final_candidates[0][0], 'computed')
        return None

    def ensure_playable_audio(
        self, 
        hash_value: int, 
        text_key: str | None, 
        event_name: str | None,
        log_callback: Any = None,
        skip_cache: bool = False,
    ) -> Path | None:
        """确保音频可播放（提取WEM/生成TXTP/转码WAV）。"""
        def log(msg):
            if log_callback: log_callback(msg)
        
        index = self.audio_index
        # 1. 再次检查缓存 (可能刚刚被另一个线程生成了)
        if index and not skip_cache:
            cached = index.find(hash_value)
            if cached and (not event_name or self._is_cache_trusted(hash_value, event_name)):
                return cached
        
        wem_root = self.config.audio_wem_root
        external_root = self.config.audio_external_root
        bnk_root = self.config.audio_bnk_root
        
        # 2. 检查 WEM 物理文件 (多目录搜寻)
        search_roots = []
        if wem_root: search_roots.append(wem_root)
        if external_root: search_roots.append(external_root)
        
        wem_file = None
        for root in search_roots:
            wem_file = find_wem_by_hash(root, hash_value)
            if not wem_file and event_name:
                wem_file = find_wem_by_event_name(root, event_name)
            if wem_file:
                break
        
        if wem_file:
            log(f"[AUDIO] 发现 WEM 文件: {wem_file.name}")
            try:
                wav_path = convert_single_wem_to_wav(
                    wem_file, 
                    self.config.vgmstream_path,
                    self.config.audio_cache_path,
                    output_name=str(hash_value),
                    skip_existing=not skip_cache,
                )
                if wav_path:
                    # 为了兼容 AudioCacheIndex (只识别数字文件名)，重命名为 hash.wav
                    final_path = wav_path.parent / f"{hash_value}{wav_path.suffix}"
                    if final_path != wav_path:
                         # 如果目标存在且有效，直接使用
                        if final_path.exists() and final_path.stat().st_size > 0 and not skip_cache:
                            wav_path = final_path
                        else:
                            if final_path.exists() and skip_cache:
                                final_path.unlink(missing_ok=True)
                            shutil.move(wav_path, final_path)
                            wav_path = final_path
                    
                    if index:
                        index.add_file(wav_path)
                    self._mark_cache_trusted(hash_value, event_name, "wem")
                return wav_path
            except Exception as e:
                log(f"[ERROR] WEM 转码失败: {e}")
                return None

        # 3. 检查 BNK 并生成 TXTP
        if bnk_root and (event_name or hash_value):
            log(f"[AUDIO] 尝试从 BNK 提取: {event_name or hash_value}")
            txtp_cache = self.config.audio_txtp_cache
            if not txtp_cache and self.config.audio_cache_path:
                txtp_cache = self.config.audio_cache_path / "txtp_cache"
                txtp_cache.mkdir(exist_ok=True)
            
            # 使用 wwiser 生成 txtp
            active_event = event_name
            bnk_file = None
            if active_event:
                bnk_file = find_bnk_for_event(bnk_root, active_event)
            
            # 如果没找到 BNK 但有 event_name，或许可以直接搜现有的 txtp?
            txtp_file: Path | None = None
            if active_event and txtp_cache:
                txtp_file = find_txtp_for_event(txtp_cache, active_event, hash_value)

            if not txtp_file and bnk_file and txtp_cache and wem_root:
                try:
                    # 调用 wwiser
                    wwiser_path = default_wwiser_path() 
                    generated_txtp = generate_txtp_for_bnk(
                        bnk_file,
                        wem_root,
                        txtp_cache,
                        wwiser_path,
                        log_callback=log,
                    )
                    if generated_txtp:
                        # 优先按 event/hash 精确匹配，否则取第一个可用 TXTP。
                        if active_event:
                            txtp_file = find_txtp_for_event(txtp_cache, active_event, hash_value)
                        if not txtp_file:
                            txtp_file = generated_txtp[0]
                except Exception as e:
                    log(f"[ERROR] BNK 处理失败: {e}")
            
            if txtp_file:
                log(f"[AUDIO] 发现/生成 TXTP: {txtp_file.name}")
                try:
                    wav_path = convert_txtp_to_wav(
                        txtp_file,
                        self.config.vgmstream_path,
                        self.config.audio_cache_path / f"{hash_value}.wav"
                    )
                    if wav_path:
                        # 重命名为 hash.wav
                        final_path = wav_path.parent / f"{hash_value}{wav_path.suffix}"
                        if final_path != wav_path:
                            if final_path.exists() and final_path.stat().st_size > 0 and not skip_cache:
                                wav_path = final_path
                            else:
                                if final_path.exists() and skip_cache:
                                    final_path.unlink(missing_ok=True)
                                shutil.move(wav_path, final_path)
                                wav_path = final_path
                        
                        if index:
                            index.add_file(wav_path)
                        self._mark_cache_trusted(hash_value, active_event or event_name, "bnk")
                    return wav_path
                except Exception as e:
                    log(f"[ERROR] TXTP 转码失败: {e}")
        
        
        return None


def resolve_external_wem_root(config: AppConfig) -> Path | None:
    """尝试解析 WwiseExternalSource 目录位置。"""
    if not config.audio_wem_root:
        return None
    try:
        # e.g. Client/Saved/WwiseAudio/Media/zh -> Client/Saved/WwiseAudio/WwiseExternalSource
        # config.audio_wem_root usually points to .../Media/zh or similar
        # parent=Media, parent.parent=WwiseAudio/Platform
        
        # Let's handle generic traversal upwards till we find WwiseExternalSource?
        # Or stick to the specific structure logic from overlay_window.
        # Original: base = self.config.audio_wem_root.parents[1]
        
        base = config.audio_wem_root.parents[1]
        candidate = base / "WwiseExternalSource"
        if candidate.exists():
            return candidate
        candidate = base / "WwiseExternalSource" / "zh" 
        if candidate.exists():
            return candidate
            
        # Try parents[2] just in case structure differs slightly
        if len(config.audio_wem_root.parts) > 3:
             base2 = config.audio_wem_root.parents[2]
             candidate = base2 / "WwiseExternalSource"
             if candidate.exists(): return candidate
             
    except Exception:
        return None
    return None
