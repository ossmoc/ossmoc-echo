from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from ludiglot.adapters.wuthering_waves.data_mapper import WutheringDataMapper


@dataclass
class AppConfig:
    data_root: Path | None
    en_json: Path
    zh_json: Path
    db_path: Path
    image_path: Path
    fonts_root: Path | None = None
    use_game_paks: bool = False
    game_install_root: Path | None = None
    game_pak_root: Path | None = None
    game_data_root: Path | None = None
    game_audio_root: Path | None = None
    game_platform: str | None = None
    game_server: str | None = None
    game_version: str | None = None
    game_languages: list[str] | None = None
    game_audio_languages: list[str] | None = None
    aes_archive_url: str | None = None
    extract_audio: bool | None = None
    auto_rebuild_db: bool = True
    min_db_entries: int = 1000
    translation_provider: str | None = None
    wahu_db_path: Path | None = None
    ocr_lang: str = "en"
    ocr_mode: str = "auto"  # auto | gpu | cpu
    ocr_gpu: bool = False  # legacy field
    ocr_backend: str = "auto"  # auto | windows | paddle_vl
    ocr_paddle_vl_url: str = "http://localhost:8000/v1"
    ocr_paddle_vl_model: str = "PaddlePaddle/PaddleOCR-VL"
    ocr_paddle_vl_mode: str = "native"  # native | gguf
    ocr_gguf_model_path: str = "tools/llama/PaddleOCR-VL-1.6-GGUF.gguf"
    ocr_gguf_mmproj_path: str = "tools/llama/PaddleOCR-VL-1.6-GGUF-mmproj.gguf"
    ocr_debug_dump_input: bool = False
    ocr_raw_capture: bool = False
    ocr_windows_input: str = "auto"  # auto | raw | png
    ocr_line_refine: bool = False
    ocr_preprocess: bool = False
    ocr_word_segment: bool = False
    ocr_multiscale: bool = False
    ocr_adaptive: bool = True
    audio_cache_path: Path | None = None
    audio_cache_index_path: Path | None = None
    audio_wem_root: Path | None = None
    audio_bnk_root: Path | None = None
    audio_external_root: Path | None = None
    audio_txtp_cache: Path | None = None
    vgmstream_path: Path | None = None
    wwiser_path: Path | None = None
    fmodel_root: Path | None = None
    audio_cache_max_mb: int = 2048
    scan_audio_on_start: bool = True
    play_audio: bool = True
    gender_preference: str = "female"  # "male" or "female"
    capture_mode: str = "select"  # "window", "region", "fullscreen", "select"
    capture_backend: str = "mss"  # "mss" | "winrt"
    window_title: str | None = None
    capture_region: dict | None = None
    exception_capture_region: dict | None = None
    realtime_enabled: bool = False
    realtime_interval_ms: int = 350
    realtime_change_threshold: float = 0.005
    hotkey_capture: str = "ctrl+shift+o"
    hotkey_toggle: str | None = None
    hotkey_exception: str | None = "alt+e"
    hotkey_exception_select: str | None = "alt+shift+e"
    window_pos: tuple[int, int] | None = None
    font_en: str = "Source Han Serif SC, 思源宋体, serif"
    font_cn: str = "Source Han Serif SC, 思源宋体, serif"
    capture_force_dpr: float | None = None


def load_config(path: Path, *, validate_data: bool = True, base_root: Path | None = None) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {path}\n"
            "   请将 config/settings.example.json 重命名为 settings.json 并配置数据路径。"
        )
    raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8-sig"))
    
    project_root = Path(base_root).resolve() if base_root else Path(__file__).resolve().parents[3]
    
    def resolve_path(p: str | None) -> Path | None:
        if not p: return None
        pp = Path(p)
        if pp.is_absolute(): return pp
        # 相对路径解析：相对于项目根目录
        return (project_root / pp).resolve()

    data_root = resolve_path(raw.get("data_root"))
    db_path = resolve_path(raw.get("db_path"))

    use_game_paks = bool(raw.get("use_game_paks", False))
    game_install_root = resolve_path(raw.get("game_install_root"))
    game_pak_root = resolve_path(raw.get("game_pak_root"))
    game_data_root = resolve_path(raw.get("game_data_root"))
    game_audio_root = resolve_path(raw.get("game_audio_root"))
    game_platform = raw.get("game_platform")
    game_server = raw.get("game_server")
    game_version = raw.get("game_version")
    game_languages = raw.get("game_languages")
    game_audio_languages = raw.get("game_audio_languages")
    aes_archive_url = raw.get("aes_archive_url")
    extract_audio = raw.get("extract_audio")
    if extract_audio is None:
        extract_audio = raw.get("extract_game_audio")

    
    # 智能探测数据库文件（仅在未显式配置 db_path 时启用）
    if not raw.get("db_path"):
        shared_db_candidates = [
            project_root / "game_text_db.json",
            project_root / "data" / "game_text_db.json",
            project_root / "cache" / "game_text_db.json"
        ]
        for candidate in shared_db_candidates:
            if candidate.exists():
                db_path = candidate
                break

    auto_rebuild = raw.get("auto_rebuild_db", True)
    has_db = db_path and db_path.exists()
    
    # 修改逻辑：只有在真正需要 data_root 的时候才报错
    if validate_data and data_root and not data_root.exists() and not use_game_paks:
        # 需要 data_root 的两种情况：1. 明确要求重建 2. 没有 DB 需要生成
        if auto_rebuild or not has_db:
             # 如果用户已经设置了 false 但还是进来了，说明是 has_db 为 false
             if not auto_rebuild and not has_db:
                 raise FileNotFoundError(
                     f"未找到数据库: {raw.get('db_path')}\n"
                     f"且 data_root ({data_root}) 不存在，无法自动生成。\n"
                     "请在 settings.json 中设置正确的 'data_root' 或 'db_path'。"
                 )
             else:
                 raise FileNotFoundError(
                     f"数据目录不存在: {data_root}\n"
                     "建议配置 game_pak_root 或 game_install_root 使用 Pak 解包流程。"
                 )

    en_json = raw.get("en_json")
    zh_json = raw.get("zh_json")
    
    # 只有在需要重建或者没有 DB 的时候，才强制要求 MultiText 路径
    if validate_data and (not has_db or auto_rebuild) and (not en_json or not zh_json) and data_root and data_root.exists():
        try:
            data_paths = WutheringDataMapper(data_root).parse()
            en_json = en_json or str(data_paths.en_text)
            zh_json = zh_json or str(data_paths.zh_text)
        except FileNotFoundError:
            if not has_db: raise
    
    # 解析路径
    en_json_path = resolve_path(en_json)
    zh_json_path = resolve_path(zh_json)
    
    if validate_data and not has_db and (not en_json_path or not zh_json_path) and not use_game_paks:
         raise ValueError("配置中缺少数据库文件 (db_path) 且无法通过 data_root 构建。请至少提供其中之一。")

    fonts_root = resolve_path(raw.get("fonts_root"))
    if fonts_root is None:
        fonts_root = (data_root or (project_root / "data")) / "Fonts"

    ocr_mode = raw.get("ocr_mode")
    if not ocr_mode:
        ocr_mode = "gpu" if raw.get("ocr_gpu") else "auto"
    ocr_windows_input = str(raw.get("ocr_windows_input", "auto")).lower()
    if ocr_windows_input not in {"auto", "raw", "png"}:
        ocr_windows_input = "auto"
    
    ocr_paddle_vl_url = str(raw.get("ocr_paddle_vl_url", "http://localhost:8000/v1"))
    ocr_paddle_vl_model = str(raw.get("ocr_paddle_vl_model", "PaddlePaddle/PaddleOCR-VL"))
    ocr_paddle_vl_mode = str(raw.get("ocr_paddle_vl_mode", "native")).lower()
    ocr_gguf_model_path = str(raw.get("ocr_gguf_model_path", "tools/llama/PaddleOCR-VL-1.6-GGUF.gguf"))
    ocr_gguf_mmproj_path = str(raw.get("ocr_gguf_mmproj_path", "tools/llama/PaddleOCR-VL-1.6-GGUF-mmproj.gguf"))
    # Removed Ollama/GLM backend fields
    capture_force_dpr = raw.get("capture_force_dpr")
    try:
        capture_force_dpr = float(capture_force_dpr) if capture_force_dpr is not None else None
    except Exception:
        capture_force_dpr = None
        
    audio_cache_path = resolve_path(raw.get("audio_cache_path")) or project_root / "cache" / "audio"
    audio_cache_index_path = resolve_path(raw.get("audio_cache_index_path"))
    audio_wem_root = resolve_path(raw.get("audio_wem_root"))
    audio_bnk_root = resolve_path(raw.get("audio_bnk_root"))
    audio_external_root = resolve_path(raw.get("audio_external_root"))
    audio_txtp_cache = resolve_path(raw.get("audio_txtp_cache"))
    
    vgmstream_path = resolve_path(raw.get("vgmstream_path"))
    wwiser_path = resolve_path(raw.get("wwiser_path"))
    fmodel_root = resolve_path(raw.get("fmodel_root"))
    
    # 智能探测 vgmstream_path
    if vgmstream_path is None or not vgmstream_path.exists():
        # 1. 尝试从项目内置 tools 找
        # 1. 优先尝试 FModelCLI 自动维护的 .data 目录
        candidate = project_root / "tools" / ".data" / "vgmstream-cli.exe"
        if candidate.exists():
            vgmstream_path = candidate
        else:
            # 2. 兼容旧的独立目录 (如果尚未删除)
            legacy_candidate = project_root / "tools" / "vgmstream" / "vgmstream-cli.exe"
            if legacy_candidate.exists():
                vgmstream_path = legacy_candidate
            
        # 2. 尝试从 fmodel_root 探测 (FModel/Output/.data/vgmstream/vgmstream-cli.exe)
        if (vgmstream_path is None or not vgmstream_path.exists()) and fmodel_root:
            fmodel_vgm = fmodel_root / ".data" / "vgmstream" / "vgmstream-cli.exe"
            if fmodel_vgm.exists():
                vgmstream_path = fmodel_vgm
            else:
                # 兼容不同层级
                fmodel_vgm = fmodel_root / "Output" / ".data" / "vgmstream" / "vgmstream-cli.exe"
                if fmodel_vgm.exists():
                    vgmstream_path = fmodel_vgm

    if audio_cache_path and audio_cache_index_path is None:
        audio_cache_index_path = audio_cache_path / "audio_index.json"
    if audio_cache_path and audio_txtp_cache is None:
        audio_txtp_cache = audio_cache_path / "txtp"
        
    if audio_wem_root and audio_bnk_root is None:
        # 尝试从 Media/zh 向上两级找 Event/zh
        candidate = audio_wem_root.parents[1] / "Event" / "zh"
        if candidate.exists():
            audio_bnk_root = candidate
            
    if audio_wem_root and audio_external_root is None:
        # 尝试从 Media 目录向上看是否有 WwiseExternalSource
        # Media/zh -> parents[1] is WwiseAudio_Generated
        candidate = audio_wem_root.parents[1] / "WwiseExternalSource"
        if candidate.exists():
            audio_external_root = candidate
            
    if wwiser_path is None:
        candidate = project_root / "tools/wwiser.pyz"
        if candidate.exists():
            wwiser_path = candidate
            
    font_en = raw.get("font_en", "Source Han Serif SC, 思源宋体, serif")
    font_cn = raw.get("font_cn", "Source Han Serif SC, 思源宋体, serif")

    if game_data_root is None:
        game_data_root = project_root / "data" / "GameData"
    if game_audio_root is None:
        game_audio_root = project_root / "data" / "WwiseAudio_Generated"

    if isinstance(game_languages, str):
        game_languages = [item.strip() for item in game_languages.split(",") if item.strip()]
    if isinstance(game_audio_languages, str):
        game_audio_languages = [item.strip() for item in game_audio_languages.split(",") if item.strip()]
    if isinstance(unrealpak_extra_args := raw.get("unrealpak_extra_args"), str):
        unrealpak_extra_args = [item.strip() for item in unrealpak_extra_args.split(" ") if item.strip()]
    
    capture_backend = str(raw.get("capture_backend", "mss")).lower()
    if capture_backend not in {"mss", "winrt"}:
        capture_backend = "mss"
    
    gender_preference = str(raw.get("gender_preference", "female")).strip().lower()
    if gender_preference not in {"female", "male"}:
        gender_preference = "female"

    return AppConfig(
        data_root=data_root,
        en_json=en_json_path,
        zh_json=zh_json_path,
        db_path=resolve_path(raw.get("db_path", "game_text_db.json")) or project_root / "game_text_db.json",
        image_path=resolve_path(raw.get("image_path")) or project_root / "cache/capture.png",
        fonts_root=fonts_root,
        use_game_paks=use_game_paks,
        game_install_root=game_install_root,
        game_pak_root=game_pak_root,
        game_data_root=game_data_root,
        game_audio_root=game_audio_root,
        game_platform=game_platform,
        game_server=game_server,
        game_version=game_version,
        game_languages=game_languages,
        game_audio_languages=game_audio_languages,
        aes_archive_url=aes_archive_url,
        extract_audio=bool(extract_audio) if extract_audio is not None else None,
        auto_rebuild_db=bool(raw.get("auto_rebuild_db", True)),
        min_db_entries=int(raw.get("min_db_entries", 1000)),
        translation_provider=raw.get("translation_provider"),
        wahu_db_path=resolve_path(raw.get("wahu_db_path")),
        ocr_lang=raw.get("ocr_lang", "en"),
        ocr_mode=str(ocr_mode).lower(),
        ocr_gpu=bool(raw.get("ocr_gpu", False)),
        ocr_backend=str(raw.get("ocr_backend", "auto")).lower(),
        ocr_paddle_vl_url=ocr_paddle_vl_url,
        ocr_paddle_vl_model=ocr_paddle_vl_model,
        ocr_paddle_vl_mode=ocr_paddle_vl_mode,
        ocr_gguf_model_path=ocr_gguf_model_path,
        ocr_gguf_mmproj_path=ocr_gguf_mmproj_path,
        ocr_debug_dump_input=bool(raw.get("ocr_debug_dump_input", False)),
        ocr_raw_capture=bool(raw.get("ocr_raw_capture", False)),
        ocr_windows_input=ocr_windows_input,
        ocr_line_refine=bool(raw.get("ocr_line_refine", False)),
        ocr_preprocess=bool(raw.get("ocr_preprocess", False)),
        ocr_word_segment=bool(raw.get("ocr_word_segment", False)),
        ocr_multiscale=bool(raw.get("ocr_multiscale", False)),
        ocr_adaptive=bool(raw.get("ocr_adaptive", True)),
        audio_cache_path=audio_cache_path,
        audio_cache_index_path=audio_cache_index_path,
        audio_wem_root=audio_wem_root,
        audio_bnk_root=audio_bnk_root,
        audio_external_root=audio_external_root,
        audio_txtp_cache=audio_txtp_cache,
        vgmstream_path=vgmstream_path,
        wwiser_path=wwiser_path,
        fmodel_root=fmodel_root,
        audio_cache_max_mb=int(raw.get("audio_cache_max_mb", 2048)),
        scan_audio_on_start=bool(raw.get("scan_audio_on_start", True)),
        play_audio=bool(raw.get("play_audio", False)),
        gender_preference=gender_preference,
        capture_mode=raw.get("capture_mode", "image"),
        capture_backend=capture_backend,
        window_title=raw.get("window_title"),
        capture_region=raw.get("capture_region"),
        exception_capture_region=raw.get("exception_capture_region"),
        realtime_enabled=bool(raw.get("realtime_enabled", False)),
        realtime_interval_ms=max(100, int(raw.get("realtime_interval_ms", 350))),
        realtime_change_threshold=max(0.0, min(1.0, float(raw.get("realtime_change_threshold", 0.005)))),
        hotkey_capture=raw.get("hotkey_capture", "ctrl+shift+o"),
        hotkey_toggle=raw.get("hotkey_toggle"),
        hotkey_exception=raw.get("hotkey_exception", "alt+e"),
        hotkey_exception_select=raw.get("hotkey_exception_select", "alt+shift+e"),
        window_pos=_load_window_pos(raw.get("window_pos")),
        font_en=font_en,
        font_cn=font_cn,
        capture_force_dpr=capture_force_dpr,
    )


def _load_window_pos(raw: Any) -> tuple[int, int] | None:
    if not isinstance(raw, dict):
        return None
    try:
        x = int(raw.get("x"))
        y = int(raw.get("y"))
        return (x, y)
    except Exception:
        return None
