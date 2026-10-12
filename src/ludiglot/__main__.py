from __future__ import annotations

from ludiglot.infrastructure.proxy_setup import setup_system_proxy
setup_system_proxy()


import argparse
import json
import sys
from pathlib import Path

from ludiglot.core.audio_extract import (
    build_audio_index,
    collect_wem_files,
    convert_wem_to_wav,
    default_vgmstream_path,
)
from ludiglot.core.audio_mapper import AudioCacheIndex
from ludiglot.infrastructure.qt_audio_player import AudioPlayer
from ludiglot.core.audio_resolver import AudioResolver, get_voice_event_index
from ludiglot.core.capture_input import CaptureInputAdapters, capture_input_to_memory, capture_options_from_config
from ludiglot.core.config import load_config
from ludiglot.core.game_pak_update import GamePakUpdateError, update_from_game_paks
from ludiglot.core.matcher import TextMatcher
from ludiglot.core.ocr import OCREngine
from ludiglot.core.search import FuzzySearcher
from ludiglot.core.text_builder import (
    build_text_db,
    build_text_db_from_maps,
    build_text_db_from_root_all,
    normalize_en,
    save_text_db,
)
from ludiglot.core.wwise_hash import WwiseHash


# 旧的 WutheringData 克隆逻辑已移除
# 现在统一使用 FModelCLI 从游戏 Pak 构建数据库


def _check_and_setup_game_data(config_path: Path) -> bool:
    """在终端中检测游戏数据，如不存在则交互式构建。"""
    if not config_path.exists():
        return True
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as e:
        # load_config 抛出的 FileNotFoundError 说明数据缺失
        # 需要交互式处理
        pass
    except Exception:
        return True
    else:
        # 成功加载配置，检查数据是否存在
        data_root = cfg.data_root
        if data_root:
            data_root = Path(data_root).resolve()
            configdb = data_root / "ConfigDB"
            if configdb.exists() and any(configdb.iterdir()):
                # ConfigDB 存在且非空，数据就绪
                return True

    # 数据缺失，尝试重新解析原始配置以获取路径信息
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return True

    game_pak_root = raw.get("game_pak_root") or raw.get("game_install_root")
    
    # 如果没有配置游戏路径，直接返回让后续流程报错
    if not game_pak_root:
        return True

    if not sys.stdin.isatty():
        print("\n⚠️  游戏数据未就绪。请运行 ludiglot pak-update 构建数据库。")
        return False

    print("\n" + "=" * 70)
    print("📦 游戏数据未就绪")
    print("=" * 70)
    print(f"\n检测到游戏路径: {game_pak_root}")
    print("将使用 FModelCLI 从游戏 Pak 解包文本和音频资源。")
    print("\n选项：")
    print("  [Y] 立即解包并构建数据库 (推荐，首次运行必选)")
    print("  [N] 跳过 (稍后手动执行 ludiglot pak-update)")
    print("  [C] 取消启动")

    while True:
        choice = input("请选择 [Y/N/C]: ").strip().upper()
        if choice == "C":
            return False
        if choice == "N":
            return True
        if choice == "Y":
            break
        print("❌ 无效输入，请输入 Y、N 或 C")

    try:
        # 直接从 raw 配置构建最小配置，绕过 load_config 的数据验证
        # 因为此时数据还未构建，load_config 会因为 find_multitext_paths 失败
        project_root = Path(__file__).resolve().parents[2]
        
        def resolve_path(p: str | None) -> Path | None:
            if not p: return None
            pp = Path(p)
            if pp.is_absolute(): return pp
            return (project_root / pp).resolve()
        
        data_root = resolve_path(raw.get("data_root", "data"))
        db_path = resolve_path(raw.get("db_path", "game_text_db.json")) or project_root / "game_text_db.json"
        
        # 确保数据目录存在
        if data_root:
            data_root.mkdir(parents=True, exist_ok=True)
        
        # 创建最小配置对象
        from ludiglot.core.config import AppConfig
        minimal_cfg = AppConfig(
            data_root=data_root,
            en_json=None,  # 构建前不需要
            zh_json=None,  # 构建前不需要
            db_path=db_path,
            image_path=project_root / "cache" / "capture.png",
            use_game_paks=bool(raw.get("use_game_paks")),
            game_install_root=resolve_path(raw.get("game_install_root")),
            game_pak_root=resolve_path(raw.get("game_pak_root")),
            game_data_root=resolve_path(raw.get("game_data_root")) or (data_root / "GameData" if data_root else None),
            game_audio_root=resolve_path(raw.get("game_audio_root")) or (data_root / "WwiseAudio_Generated" if data_root else None),
            game_platform=raw.get("game_platform"),
            game_server=raw.get("game_server"),
            game_version=raw.get("game_version"),
            game_languages=raw.get("game_languages"),
            game_audio_languages=raw.get("game_audio_languages"),
            aes_archive_url=raw.get("aes_archive_url"),
            extract_audio=raw.get("extract_audio") if raw.get("extract_audio") is not None else raw.get("extract_game_audio"),
        )
            
        update_from_game_paks(minimal_cfg, config_path, db_path, progress=lambda m: print(m))
        print("\n✅ 数据库构建成功！")
        return True
    except GamePakUpdateError as exc:
        print(f"\n❌ Pak 更新失败: {exc}")
        return False
    except Exception as exc:
        print(f"\n❌ 构建失败: {exc}")
        import traceback
        traceback.print_exc()
        return False


def _load_db(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_demo(args: argparse.Namespace) -> None:
    en_map = {
        "Main_LahaiRoi_3_1_1_2": "Stop right there!",
        "Main_LahaiRoi_3_1_1_3": "Who are you?",
    }
    zh_map = {
        "Main_LahaiRoi_3_1_1_2": "站住！",
        "Main_LahaiRoi_3_1_1_3": "你是谁？",
    }
    db = build_text_db_from_maps(en_map, zh_map, "demo.json")
    output = Path(args.output)
    save_text_db(db, output)

    query = "stop right ther"
    key = normalize_en(query)
    if key in db:
        print("Exact hit:")
        print(json.dumps(db[key], ensure_ascii=False, indent=2))
        return

    searcher = FuzzySearcher()
    best, score = searcher.search(key, db.keys())
    print(f"Best match: {best}  score={score:.3f}")
    print(json.dumps(db.get(best, {}), ensure_ascii=False, indent=2))


def cmd_build(args: argparse.Namespace) -> None:
    en_json = Path(args.en) if args.en else None
    zh_json = Path(args.zh) if args.zh else None
    output = Path(args.output)
    if en_json and zh_json:
        db = build_text_db(en_json, zh_json)
    else:
        if not args.data_root:
            raise RuntimeError("缺少 --en/--zh 或 --data-root")
        data_root = Path(args.data_root)
        db = build_text_db_from_root_all(data_root)
    save_text_db(db, output)
    print(f"OK: {output}")


def cmd_hash(args: argparse.Namespace) -> None:
    print(WwiseHash().hash_str(args.text))


def cmd_search(args: argparse.Namespace) -> None:
    db = _load_db(Path(args.db))
    query_raw = args.query.lower()
    query_norm = normalize_en(args.query)

    if query_norm in db:
        print(f"Match found for key: {query_norm}")
        print(json.dumps(db[query_norm], ensure_ascii=False, indent=2))
        return

    # 内容匹配 (CN/EN)
    hits = []
    for k, v in db.items():
        found = False
        for m in v.get("matches", []):
            if query_raw in m.get("official_en", "").lower() or query_raw in m.get("official_cn", "").lower():
                hits.append((k, m))
                found = True
                break # 每一组只出一个
        if len(hits) >= 10: break

    if hits:
        print(f"Found {len(hits)} content matches:")
        for k, m in hits:
            print(f"\n[Key: {k}]")
            print(json.dumps(m, ensure_ascii=False, indent=2))
        return

    searcher = FuzzySearcher()
    best, score = searcher.search(query_norm, db.keys())
    print(f"Best key match: {best}  score={score:.3f}")
    print(json.dumps(db.get(best, {}), ensure_ascii=False, indent=2))


def cmd_ocr(args: argparse.Namespace) -> None:
    db = _load_db(Path(args.db))
    engine = OCREngine(lang=args.lang, use_gpu=args.gpu)
    ocr_result = engine.recognize_pipeline(Path(args.image))
    lines = ocr_result.lines

    if not lines:
        print("OCR 未识别到文本")
        return

    matcher = TextMatcher(db)
    for text, conf in lines:
        result = matcher.match([(text, conf)])
        if not result:
            continue
        print(f"[OCR {conf:.2f}] {text}")
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _build_audio_resolver(cfg, index: AudioCacheIndex | None = None) -> AudioResolver:
    return AudioResolver(
        cfg,
        voice_event_index=get_voice_event_index(cfg),
        audio_index=index,
    )


def _coerce_audio_hash(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _play_audio_for_key(
    text_key: str,
    cfg,
    index: AudioCacheIndex | None = None,
    audio_event: str | None = None,
    audio_hash: str | int | None = None,
) -> bool:
    resolver = _build_audio_resolver(cfg, index)
    resolution = resolver.resolve(
        text_key,
        db_event=audio_event,
        db_hash=_coerce_audio_hash(audio_hash),
    )
    if resolution is None:
        return False

    print(f"[AUDIO] 尝试播放 TextKey: {text_key}")
    print(
        f"[AUDIO] 解析结果: event={resolution.event_name}, "
        f"hash={resolution.hash_value}, source={resolution.source_type}"
    )

    audio_path = None
    if resolution.source_type == "cache":
        audio_path = resolver.get_cached_path(resolution.hash_value, resolution.event_name)
    if audio_path is None:
        audio_path = resolver.ensure_playable_audio(
            resolution.hash_value,
            text_key,
            resolution.event_name,
        )
    if audio_path is None:
        return False

    AudioPlayer().play(str(audio_path))
    return True


def cmd_play(args: argparse.Namespace) -> None:
    cache_dir = Path(args.cache)
    index = AudioCacheIndex(cache_dir)
    index.load()
    index.scan()

    if args.hash is not None:
        hash_value = int(args.hash)
        audio_path = index.find(hash_value)
    else:
        from ludiglot.core.config import AppConfig

        cfg = AppConfig(
            data_root=None,
            en_json=Path(),
            zh_json=Path(),
            db_path=Path(),
            image_path=Path(),
            audio_cache_path=cache_dir,
            audio_cache_index_path=cache_dir / "audio_index.json",
        )
        resolver = AudioResolver(cfg, audio_index=index)
        resolution = resolver.resolve(args.text_key)
        audio_path = (
            resolver.get_cached_path(resolution.hash_value, resolution.event_name, trusted_only=False)
            if resolution
            else None
        )
        hash_value = resolution.hash_value if resolution else None

    if audio_path is None:
        target = hash_value if hash_value is not None else args.text_key
        print(f"未找到音频文件：{target}")
        return
    AudioPlayer().play(str(audio_path))

def cmd_audio_extract(args: argparse.Namespace) -> None:
    wem_root = Path(args.wem_root)
    cache_dir = Path(args.cache)
    vgmstream_path = Path(args.vgmstream) if args.vgmstream else default_vgmstream_path()
    files = collect_wem_files(
        wem_root,
        extensions=args.ext,
        contains=args.contains,
        limit=args.limit,
    )
    if not files:
        print("未找到可用的 WEM 文件")
        return
    result = convert_wem_to_wav(
        files,
        vgmstream_path,
        cache_dir,
        preserve_paths=args.preserve_paths,
        root_dir=wem_root,
        skip_existing=not args.force,
    )
    index_path = Path(args.index_path) if args.index_path else None
    index = build_audio_index(cache_dir, index_path=index_path, max_mb=args.max_mb)
    print(
        f"WEM: {len(files)} | 转码: {result.converted} | 跳过: {result.skipped} | 失败: {result.failed} | 索引: {len(index.entries)}"
    )


def cmd_audio_build(args: argparse.Namespace) -> None:
    cfg = load_config(Path(args.config))
    wem_root = Path(args.wem_root) if args.wem_root else cfg.audio_wem_root
    cache_dir = Path(args.cache) if args.cache else cfg.audio_cache_path
    if wem_root is None:
        raise RuntimeError("缺少 --wem-root 或 config.audio_wem_root")
    if cache_dir is None:
        raise RuntimeError("缺少 --cache 或 config.audio_cache_path")
    vgmstream_path = None
    if args.vgmstream:
        vgmstream_path = Path(args.vgmstream)
    elif cfg.vgmstream_path:
        vgmstream_path = cfg.vgmstream_path
    else:
        vgmstream_path = default_vgmstream_path()

    index_path = Path(args.index_path) if args.index_path else cfg.audio_cache_index_path
    cfg.audio_wem_root = wem_root
    cfg.audio_cache_path = cache_dir
    cfg.audio_cache_index_path = index_path
    cfg.vgmstream_path = vgmstream_path
    index = build_audio_index(cache_dir, index_path=index_path, max_mb=args.max_mb)

    if args.full_convert:
        files = collect_wem_files(
            wem_root,
            extensions=args.ext,
            contains=args.contains,
            limit=args.limit,
        )
        if not files:
            print("未找到可用的 WEM 文件")
            return
        result = convert_wem_to_wav(
            files,
            vgmstream_path,
            cache_dir,
            preserve_paths=args.preserve_paths,
            root_dir=wem_root,
            skip_existing=not args.force,
        )
        index.scan()
        print(
            f"WEM: {len(files)} | 转码: {result.converted} | 跳过: {result.skipped} | 失败: {result.failed} | 索引: {len(index.entries)}"
        )

    if args.test_hash or args.test_text_key:
        resolver = _build_audio_resolver(cfg, index)
        if args.test_hash:
            hash_val = int(args.test_hash)
            audio_path = resolver.get_cached_path(hash_val, trusted_only=False)
            if audio_path is None:
                audio_path = resolver.ensure_playable_audio(hash_val, None, None)
            if audio_path:
                AudioPlayer().play(str(audio_path))
            else:
                print(f"未找到哈希 {hash_val} 对应的音频")
        else:
            if not _play_audio_for_key(args.test_text_key, cfg, index):
                print(f"测试播放失败：未找到对应音频 {args.test_text_key}")

    if args.start_gui:
        from ludiglot.ui.app_shell import run_gui
        run_gui(Path(args.config))


def cmd_run(args: argparse.Namespace) -> None:
    cfg = load_config(Path(args.config))

    if not cfg.db_path.exists():
        if cfg.data_root:
            db = build_text_db_from_root_all(cfg.data_root)
        else:
            db = build_text_db(cfg.en_json, cfg.zh_json)
        save_text_db(db, cfg.db_path)

    if str(cfg.capture_mode).lower() == "select":
        raise RuntimeError("CLI run 不支持 capture_mode=select；请使用 gui 或配置 window/region/image")
    capture_input = capture_input_to_memory(
        capture_options_from_config(cfg),
        adapters=CaptureInputAdapters(on_fallback=print),
    )

    db = _load_db(cfg.db_path)
    engine = OCREngine(
        lang=cfg.ocr_lang,
        use_gpu=cfg.ocr_gpu,
        mode=cfg.ocr_mode,
    )
    try:
        engine.prewarm(getattr(cfg, "ocr_backend", "auto"), async_=True)
    except Exception as exc:
        print(f"预热 OCR 引擎失败（已忽略）：{exc}", file=sys.stderr)
    try:
        engine.win_ocr_adaptive = bool(getattr(cfg, "ocr_adaptive", True))
        engine.win_ocr_preprocess = bool(getattr(cfg, "ocr_preprocess", False))
        engine.win_ocr_line_refine = bool(getattr(cfg, "ocr_line_refine", False))
        engine.win_ocr_segment = bool(getattr(cfg, "ocr_word_segment", False))
        engine.win_ocr_multiscale = bool(getattr(cfg, "ocr_multiscale", False))
    except Exception as exc:
        print(f"OCR 配置失败（已忽略）：{exc}", file=sys.stderr)
    cache_index = None
    if cfg.audio_cache_path and cfg.scan_audio_on_start:
        cache_index = AudioCacheIndex(
            cfg.audio_cache_path,
            index_path=cfg.audio_cache_index_path,
            max_mb=cfg.audio_cache_max_mb,
        )
        cache_index.load()
        cache_index.scan()
    ocr_result = engine.recognize_pipeline(capture_input, backend=cfg.ocr_backend)
    lines = ocr_result.lines

    if not lines:
        print("OCR 未识别到文本")
        return

    matcher = TextMatcher(db, gender_preference=cfg.gender_preference)
    for text, conf in lines:
        result = matcher.match([(text, conf)])
        if not result:
            continue

        print(f"[OCR {conf:.2f}] {text}")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if cfg.play_audio and cfg.audio_cache_path and result.get("matches"):
            match = result["matches"][0]
            text_key = match.get("text_key")
            if text_key:
                _play_audio_for_key(
                    text_key,
                    cfg,
                    index=cache_index,
                    audio_event=match.get("audio_event"),
                    audio_hash=match.get("audio_hash")
                )


def cmd_gui(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    from ludiglot.ui.app_shell import run_gui
    run_gui(config_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ludiglot")
    parser.add_argument("--config", default="config/settings.json")
    sub = parser.add_subparsers(dest="cmd", required=False)

    demo = sub.add_parser("demo", help="生成演示 DB 并做一次查询")
    demo.add_argument("--output", default="game_text_db.json")
    demo.set_defaults(func=cmd_demo)

    build = sub.add_parser("build", help="从 MultiText JSON 构建 DB")
    build.add_argument("--en", help="英文 MultiText JSON")
    build.add_argument("--zh", help="中文 MultiText JSON")
    build.add_argument("--data-root", help="WutheringData 根目录（可自动定位 MultiText）")
    build.add_argument("--output", default="game_text_db.json")
    build.set_defaults(func=cmd_build)

    hash_cmd = sub.add_parser("hash", help="计算 vo_ 哈希")
    hash_cmd.add_argument("text")
    hash_cmd.set_defaults(func=cmd_hash)

    search = sub.add_parser("search", help="在 DB 中查询")
    search.add_argument("--db", required=True)
    search.add_argument("query")
    search.set_defaults(func=cmd_search)

    ocr = sub.add_parser("ocr", help="对图片进行 OCR 并查库")
    ocr.add_argument("--image", required=True, help="截图或图片路径")
    ocr.add_argument("--db", required=True, help="game_text_db.json")
    ocr.add_argument("--lang", default="en", help="OCR 语言，如 en/zh")
    ocr.add_argument("--gpu", action="store_true", help="启用 GPU")
    ocr.set_defaults(func=cmd_ocr)

    play = sub.add_parser("play", help="按 TextKey/Hash 播放音频")
    play.add_argument("--cache", required=True, help="音频缓存目录")
    group = play.add_mutually_exclusive_group(required=True)
    group.add_argument("--text-key", help="文本 Key，如 Main_LahaiRoi_3_1_1_2")
    group.add_argument("--hash", help="已知的 Hash 数值")
    play.set_defaults(func=cmd_play)

    audio = sub.add_parser("audio-extract", help="从 FModel 导出的 WEM 转码并建立索引")
    audio.add_argument("--wem-root", required=True, help="FModel 导出的 WEM 根目录")
    audio.add_argument("--cache", required=True, help="输出音频缓存目录")
    audio.add_argument("--vgmstream", help="vgmstream-cli.exe 路径")
    audio.add_argument("--ext", nargs="+", default=[".wem"], help="过滤扩展名")
    audio.add_argument("--contains", nargs="*", default=None, help="路径包含关键词")
    audio.add_argument("--limit", type=int, help="仅处理前 N 个")
    audio.add_argument("--preserve-paths", action="store_true", help="保留导出目录结构")
    audio.add_argument("--force", action="store_true", help="覆盖已有文件")
    audio.add_argument("--index-path", help="缓存索引输出路径")
    audio.add_argument("--max-mb", type=int, default=2048, help="缓存上限 (MB)")
    audio.set_defaults(func=cmd_audio_extract)

    audio_build = sub.add_parser("audio-build", help="自动构建语音缓存并可选测试/启动 GUI")
    audio_build.add_argument("--config", default="config/settings.json")
    audio_build.add_argument("--wem-root", help="FModel 导出的 WEM 根目录")
    audio_build.add_argument("--cache", help="输出音频缓存目录")
    audio_build.add_argument("--vgmstream", help="vgmstream-cli.exe 路径")
    audio_build.add_argument("--ext", nargs="+", default=[".wem"], help="过滤扩展名")
    audio_build.add_argument("--contains", nargs="*", default=None, help="路径包含关键词")
    audio_build.add_argument("--limit", type=int, help="仅处理前 N 个")
    audio_build.add_argument("--preserve-paths", action="store_true", help="保留导出目录结构")
    audio_build.add_argument("--force", action="store_true", help="覆盖已有文件")
    audio_build.add_argument("--index-path", help="缓存索引输出路径")
    audio_build.add_argument("--max-mb", type=int, default=2048, help="缓存上限 (MB)")
    audio_build.add_argument("--full-convert", action="store_true", help="全量转码（默认按需）")
    audio_build.add_argument("--test-text-key", help="构建后按 TextKey 播放测试")
    audio_build.add_argument("--test-hash", help="构建后按 Hash 播放测试")
    audio_build.add_argument("--start-gui", action="store_true", help="构建完成后启动 GUI")
    audio_build.set_defaults(func=cmd_audio_build)

    run = sub.add_parser("run", help="一键运行：构建DB + OCR + 查库(+播放)")
    run.add_argument("--config", default="config/settings.json")
    run.set_defaults(func=cmd_run)

    gui = sub.add_parser("gui", help="启动 GUI 覆盖层")
    gui.add_argument("--config", default="config/settings.json")
    gui.set_defaults(func=cmd_gui)

    pak_update = sub.add_parser("pak-update", help="从本地游戏 Pak 解包并重建数据库")
    pak_update.add_argument("--config", default="config/settings.json")
    pak_update.set_defaults(func=cmd_pak_update)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd is None:
        cmd_gui(argparse.Namespace(config=args.config))
        return
    args.func(args)


def cmd_pak_update(args: argparse.Namespace) -> None:
    cfg = load_config(Path(args.config), validate_data=False)
    try:
        update_from_game_paks(cfg, Path(args.config), cfg.db_path, progress=print)
        print("✅ Pak 更新完成")
    except GamePakUpdateError as exc:
        print(f"❌ Pak 更新失败: {exc}")


if __name__ == "__main__":
    main()
