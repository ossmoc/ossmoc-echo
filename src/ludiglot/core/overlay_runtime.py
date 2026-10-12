from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ludiglot.core.audio_mapper import AudioCacheIndex
from ludiglot.core.audio_resolver import AudioResolver, resolve_external_wem_root
from ludiglot.core.config import AppConfig
from ludiglot.core.matcher import TextMatcher
from ludiglot.core.ocr import OCREngine
from ludiglot.core.skill_param_resolver import SkillParamResolver
from ludiglot.core.text_builder import build_text_db, build_text_db_from_root_all, save_text_db
from ludiglot.adapters.wuthering_waves.wahu import apply_configured_wahu_provider
from ludiglot.core.voice_event_index import VoiceEventIndex
from ludiglot.core.voice_map import build_voice_map_from_configdb, collect_all_voice_event_names


@dataclass(frozen=True)
class OverlayRuntimeCallbacks:
    status: Callable[[str], None] | None = None
    log: Callable[[str], None] | None = None
    error: Callable[[str], None] | None = None


@dataclass(frozen=True)
class OverlayRuntimeResources:
    db: dict[str, Any]
    matcher: TextMatcher | None
    audio_resolver: AudioResolver | None
    skill_param_resolver: SkillParamResolver | None
    voice_map: dict[str, list[str]]
    voice_event_index: VoiceEventIndex | None
    audio_index: AudioCacheIndex | None
    external_wem_root: Path | None


@dataclass(frozen=True)
class OverlayRuntimeInitResult:
    success: bool
    resources: OverlayRuntimeResources | None = None
    error_message: str | None = None


def create_overlay_ocr_engine(config: AppConfig, callbacks: OverlayRuntimeCallbacks | None = None) -> OCREngine:
    callbacks = callbacks or OverlayRuntimeCallbacks()
    engine = OCREngine(
        lang=config.ocr_lang,
        use_gpu=config.ocr_gpu,
        mode=config.ocr_mode,
    )
    engine.set_logger(callbacks.log, callbacks.status)
    _set_optional_ocr_flag(engine, "win_ocr_line_refine", getattr(config, "ocr_line_refine", False))
    _set_optional_ocr_flag(engine, "win_ocr_preprocess", getattr(config, "ocr_preprocess", False))
    _set_optional_ocr_flag(engine, "win_ocr_segment", getattr(config, "ocr_word_segment", False))
    _set_optional_ocr_flag(engine, "win_ocr_multiscale", getattr(config, "ocr_multiscale", False))
    _set_optional_ocr_flag(engine, "win_ocr_adaptive", getattr(config, "ocr_adaptive", True))
    setattr(engine, "paddle_vl_url", getattr(config, "ocr_paddle_vl_url", "http://localhost:8000/v1"))
    setattr(engine, "paddle_vl_model", getattr(config, "ocr_paddle_vl_model", "PaddlePaddle/PaddleOCR-VL"))
    return engine


def initialize_overlay_runtime(
    config: AppConfig,
    engine: OCREngine,
    callbacks: OverlayRuntimeCallbacks | None = None,
) -> OverlayRuntimeInitResult:
    callbacks = callbacks or OverlayRuntimeCallbacks()
    try:
        db = _load_or_build_text_db(config, callbacks)
    except Exception as exc:
        message = f"DB 初始化失败: {exc}"
        _emit(callbacks.error, message)
        return OverlayRuntimeInitResult(False, error_message=message)

    skill_param_resolver = _init_skill_param_resolver(config, callbacks)
    voice_map = _build_voice_map(config, callbacks)
    voice_event_index = _build_voice_event_index(config, voice_map, callbacks)
    _preload_ocr_engine(config, engine, callbacks)
    audio_index = _init_audio_index(config, callbacks)
    external_wem_root = resolve_external_wem_root(config)
    matcher, audio_resolver = _init_matcher_and_audio_resolver(config, db, voice_map, voice_event_index, audio_index, callbacks)

    return OverlayRuntimeInitResult(
        True,
        OverlayRuntimeResources(
            db=db,
            matcher=matcher,
            audio_resolver=audio_resolver,
            skill_param_resolver=skill_param_resolver,
            voice_map=voice_map,
            voice_event_index=voice_event_index,
            audio_index=audio_index,
            external_wem_root=external_wem_root,
        ),
    )


def _load_or_build_text_db(config: AppConfig, callbacks: OverlayRuntimeCallbacks) -> dict[str, Any]:
    should_rebuild = config.auto_rebuild_db or not config.db_path.exists()

    if should_rebuild:
        if not (config.data_root or (config.en_json and config.zh_json)):
            if not config.db_path.exists():
                raise FileNotFoundError("找不到数据库文件，且没有指定源数据路径 (data_root) 来生成它。")
            should_rebuild = False

    if should_rebuild:
        _emit(callbacks.status, "构建文本数据库…")
        if config.data_root and config.data_root.exists():
            db = build_text_db_from_root_all(config.data_root)
        elif config.en_json and Path(config.en_json).exists():
            db = build_text_db(Path(config.en_json), Path(config.zh_json))
        elif config.db_path.exists():
            _emit(callbacks.log, "[DB] 缺少源数据，跳过重建，使用现有数据库")
            should_rebuild = False
        else:
            raise FileNotFoundError("找不到数据库文件，且没有有效的源数据路径来生成它。")

    if should_rebuild:
        stats = apply_configured_wahu_provider(db, config)
        if stats:
            _emit(
                callbacks.log,
                "[WAHU] "
                f"official={stats.official_entries}, exact_id={stats.exact_id_matches}, "
                f"vietnamese={stats.vietnamese_matches}, english_fallback={stats.english_fallbacks}, "
                f"missing={stats.missing_entries}",
            )
        save_text_db(db, config.db_path)

    if config.db_path.exists():
        return json.loads(config.db_path.read_text(encoding="utf-8"))
    return {}


def _init_skill_param_resolver(config: AppConfig, callbacks: OverlayRuntimeCallbacks) -> SkillParamResolver | None:
    if not config.data_root:
        return None
    db_path = config.data_root / "ConfigDB" / "db_skill.db"
    resolver = SkillParamResolver(db_path, logger=callbacks.log)
    if resolver.available:
        _emit(callbacks.log, f"[PARAM] 技能参数解析器已启用: {db_path}")
        return resolver
    return None


def _build_voice_map(config: AppConfig, callbacks: OverlayRuntimeCallbacks) -> dict[str, list[str]]:
    if not config.data_root:
        return {}
    try:
        cache_path = Path(__file__).resolve().parents[3] / "cache" / "voice_map.json"
        voice_map = build_voice_map_from_configdb(config.data_root, cache_path=cache_path)
        if voice_map:
            _emit(callbacks.log, f"[VOICE] 映射加载: {len(voice_map)} 项")
        return voice_map
    except Exception as exc:
        _emit(callbacks.log, f"[VOICE] 映射加载失败: {exc}")
        return {}


def _build_voice_event_index(
    config: AppConfig,
    voice_map: dict[str, list[str]],
    callbacks: OverlayRuntimeCallbacks,
) -> VoiceEventIndex | None:
    if not config.audio_bnk_root and not config.audio_txtp_cache:
        return None
    try:
        cache_path = config.audio_cache_path / "voice_event_index.json" if config.audio_cache_path else None
        extra_names = collect_all_voice_event_names(config.data_root, voice_map)
        index = VoiceEventIndex(
            bnk_root=config.audio_bnk_root,
            txtp_root=config.audio_txtp_cache,
            cache_path=cache_path,
            extra_names=extra_names,
        )
        index.load_or_build()
        if index.names:
            _emit(callbacks.log, f"[VOICE] 事件索引: {len(index.names)} 项")
        return index
    except Exception as exc:
        _emit(callbacks.log, f"[VOICE] 事件索引加载失败: {exc}")
        return None


def _preload_ocr_engine(config: AppConfig, engine: OCREngine, callbacks: OverlayRuntimeCallbacks) -> None:
    try:
        _emit(callbacks.status, "预加载 OCR 模型…")
        engine.initialize()
        engine.prewarm(config.ocr_backend, async_=True)
        _emit(callbacks.log, "[OCR] 模型已预加载")
    except Exception as exc:
        _emit(callbacks.log, f"[OCR] 预加载失败: {exc}")


def _init_audio_index(config: AppConfig, callbacks: OverlayRuntimeCallbacks) -> AudioCacheIndex | None:
    if not (config.audio_cache_path and config.scan_audio_on_start):
        return None
    try:
        _emit(callbacks.status, "扫描音频缓存…")
        audio_index = AudioCacheIndex(
            config.audio_cache_path,
            index_path=config.audio_cache_index_path,
            max_mb=config.audio_cache_max_mb,
        )
        audio_index.load()
        audio_index.scan()
        _emit(callbacks.log, f"[AUDIO] 缓存条目: {len(audio_index.entries)}")
        return audio_index
    except Exception as exc:
        _emit(callbacks.error, f"音频缓存扫描失败: {exc}")
        return None


def _init_matcher_and_audio_resolver(
    config: AppConfig,
    db: dict[str, Any],
    voice_map: dict[str, list[str]],
    voice_event_index: VoiceEventIndex | None,
    audio_index: AudioCacheIndex | None,
    callbacks: OverlayRuntimeCallbacks,
) -> tuple[TextMatcher | None, AudioResolver | None]:
    if not db:
        return None, None
    matcher = TextMatcher(
        db,
        voice_map,
        voice_event_index,
        gender_preference=config.gender_preference,
    )
    matcher.set_logger(callbacks.log)
    _emit(callbacks.log, "[MATCHER] 匹配服务已初始化")

    audio_resolver = AudioResolver(config, voice_event_index, audio_index=audio_index)
    _emit(callbacks.log, "[AUDIO] 解析服务已初始化")
    return matcher, audio_resolver


def _set_optional_ocr_flag(engine: OCREngine, name: str, value: Any) -> None:
    try:
        setattr(engine, name, bool(value))
    except Exception:
        pass


def _emit(callback: Callable[[Any], None] | None, value: Any) -> None:
    if callback:
        callback(value)
