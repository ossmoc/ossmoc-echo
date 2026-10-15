from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from ludiglot.core.aes_archive import parse_aes_archive, select_keys


AES_ARCHIVE_URL = "https://raw.githubusercontent.com/yarik0chka/wuwa-keys/main/keys.json"
_PAKCHUNK_RE = re.compile(r"pakchunk[-_]?([0-9]+)", re.IGNORECASE)


@dataclass
class GamePakOptions:
    version: str
    platform: str
    server: str
    languages: list[str]
    audio_languages: list[str]
    extract_audio: bool


@dataclass(frozen=True)
class ExtractionStep:
    filter: str
    message: str
    warn_on_failure: str | None = None


@dataclass(frozen=True)
class DirectoryMove:
    source: Path
    target: Path


@dataclass(frozen=True)
class GamePakUpdatePlan:
    data_root: Path
    extraction_steps: list[ExtractionStep]
    directory_moves: list[DirectoryMove]
    staged_fonts_dir: Path
    fonts_target: Path
    cleanup_paths: list[Path]
    audio_wem_root: Path | None
    audio_bnk_root: Path | None


class GamePakUpdateError(RuntimeError):
    pass


def resolve_game_root(folder: Path) -> Path:
    """Accept the game root, its Client folder, or its launcher parent."""
    folder = Path(folder).resolve()
    candidates = [folder, folder / 'Wuthering Waves Game']
    if folder.name.casefold() == 'client':
        candidates.insert(0, folder.parent)
    for candidate in candidates:
        paks = candidate / 'Client' / 'Content' / 'Paks'
        if paks.is_dir() and next(paks.rglob('*.pak'), None) is not None:
            return candidate
    raise GamePakUpdateError(
        'Chưa tìm thấy dữ liệu game. Chọn thư mục Wuthering Waves Game '
        'có Client/Content/Paks và đã tải game đầy đủ. Bạn có thể chọn lại thư mục.')


def read_launcher_game_version(game: Path) -> str | None:
    """Optional display metadata, not proof of extracted text provenance."""
    try:
        with (game / 'launcherDownloadConfig.json').open(encoding='utf-8-sig') as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            return None
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        # The top-level launcher version can remain stale after an SD/UHD
        # bundle update. Prefer a single completed common-resource version.
        bundles = data.get('bundles')
        installed = set()
        if isinstance(bundles, dict):
            for bundle in bundles.values():
                if (isinstance(bundle, dict) and not bundle.get('state')
                        and isinstance(bundle.get('resourcePacks'), list)
                        and 'common' in bundle['resourcePacks']):
                    value = bundle.get('version')
                    if isinstance(value, str) and re.fullmatch(r'\d+\.\d+(?:\.\d+){0,2}', value.strip()):
                        installed.add(value.strip())
            if len(installed) > 1:
                return None  # Mixed bundles: do not guess the highest version.
        version = next(iter(installed)) if installed else data.get('version')
        if isinstance(version, str) and re.fullmatch(r'\d+\.\d+(?:\.\d+){0,2}', version.strip()):
            return version.strip()
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def _log(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _prompt_choice(title: str, options: list[str], default_index: int = 0) -> str:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    for i, opt in enumerate(options, start=1):
        marker = "(默认)" if i - 1 == default_index else ""
        print(f"  [{i}] {opt} {marker}")
    while True:
        raw = input(f"请选择 [1-{len(options)}] (Enter=默认): ").strip()
        if not raw:
            return options[default_index]
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        print("❌ 无效输入，请重试。")


def _prompt_multi_select(title: str, options: list[str], default: list[str]) -> list[str]:
    default_set = {opt.lower() for opt in default}
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    for i, opt in enumerate(options, start=1):
        marker = "(默认)" if opt.lower() in default_set else ""
        print(f"  [{i}] {opt} {marker}")
    print("支持多选：用逗号分隔编号，例如 1,3,4。回车=默认。")
    while True:
        raw = input("请选择: ").strip()
        if not raw:
            return [opt for opt in options if opt.lower() in default_set]
        indices = [part.strip() for part in raw.split(",") if part.strip()]
        selected: list[str] = []
        ok = True
        for part in indices:
            if not part.isdigit():
                ok = False
                break
            idx = int(part) - 1
            if idx < 0 or idx >= len(options):
                ok = False
                break
            selected.append(options[idx])
        if ok and selected:
            return selected
        print("❌ 无效输入，请重试。")


def _fetch_aes_archive(url: str) -> str:
    """获取 AES 归档文本，优先使用网络，网络失败时自动回退到本地缓存。"""
    application_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
    cache_file = application_root / ("userdata" if getattr(sys, "frozen", False) else ".") / "cache" / "aes_archive.md"
    
    from urllib.request import Request
    
    # 尝试从网络获取
    req = Request(url, headers={"User-Agent": "Ludiglot-Updater/1.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            # 成功后缓存到本地
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(content, encoding="utf-8")
            print("[PAK] AES 表已从网络更新")
            return content
    except Exception as e:
        # 网络失败，尝试使用缓存
        if cache_file.exists():
            print(f"[PAK] 网络请求失败 (可能受限)，正在使用本地缓存: {cache_file.name}")
            return cache_file.read_text(encoding="utf-8")
        raise GamePakUpdateError(
            f"无法获取 AES 表：网络请求失败 ({type(e).__name__}) 且无本地缓存。\n"
            f"请检查网络连接或手动下载 {url} 并保存到 {cache_file}"
        )



def _list_pak_files(pak_dir: Path) -> list[Path]:
    return sorted(pak_dir.rglob("*.pak"))


def _pak_name_to_pattern(pak_name: str) -> str:
    if pak_name.lower() == "main":
        return "pakchunk0"
    match = _PAKCHUNK_RE.search(pak_name)
    if match:
        return f"pakchunk{match.group(1)}"
    return pak_name.replace("-", "").replace("_", "")


def _match_paks(pak_dir: Path, pak_name: str) -> list[Path]:
    pattern = _pak_name_to_pattern(pak_name)
    matches = []
    for path in _list_pak_files(pak_dir):
        if pattern.lower() in path.name.lower():
            matches.append(path)
    return matches


def build_game_pak_update_plan(data_root: Path, options: GamePakOptions) -> GamePakUpdatePlan:
    staged_root = data_root / "Client" / "Content" / "Aki"
    extraction_steps = [
        ExtractionStep("Config/Json", "Đang đọc cấu hình game (Config/Json)…"),
        ExtractionStep("ConfigDB/", "Đang đọc dữ liệu văn bản (ConfigDB, gồm tất cả ngôn ngữ)…"),
    ]
    # FModelCLI uses substring matching: ConfigDB/ already includes en and
    # zh-Hans. Re-running it per language remounts every PAK unnecessarily.
    for lang in options.languages:
        extraction_steps.append(
            ExtractionStep(f"TextMap/{lang}", f"[PAK] 正在提取 TextMap/{lang} ...")
        )
    extraction_steps.append(ExtractionStep("UI/Framework/LGUI/Font/", "[PAK] 正在提取 Fonts ..."))
    if options.extract_audio:
        for audio_lang in options.audio_languages:
            extraction_steps.extend(
                [
                    ExtractionStep(f"Event/{audio_lang}/", f"[PAK] 正在提取语音资源 ({audio_lang}) ..."),
                    ExtractionStep(f"Media/{audio_lang}/", f"[PAK] 正在提取语音资源 ({audio_lang}) ..."),
                    ExtractionStep(f"WwiseExternalSource/{audio_lang}_", f"[PAK] 正在提取语音资源 ({audio_lang}) ..."),
                ]
            )

    media_lang = options.audio_languages[0] if options.audio_languages else "zh"
    return GamePakUpdatePlan(
        data_root=data_root,
        extraction_steps=extraction_steps,
        directory_moves=[
            DirectoryMove(staged_root / folder, data_root / folder)
            for folder in ("ConfigDB", "TextMap", "WwiseAudio_Generated", "Config")
        ],
        staged_fonts_dir=staged_root / "UI" / "Framework" / "LGUI" / "Font",
        fonts_target=data_root / "Fonts",
        cleanup_paths=[data_root / "Client", data_root / "Audio_Extract_Temp", data_root / "Audio"],
        audio_wem_root=(data_root / "WwiseAudio_Generated" / "Media" / media_lang) if options.extract_audio else None,
        audio_bnk_root=(data_root / "WwiseAudio_Generated" / "Event" / media_lang) if options.extract_audio else None,
    )


def _choose_options(entries, cfg) -> GamePakOptions:
    # Empty means select the newest stable group for this platform/server.
    # A rolling JSON feed is labelled Latest, not a numeric game version.
    version_choice = cfg.game_version or ""

    if not _is_tty():
        if not (cfg.game_platform and cfg.game_server and cfg.game_languages):
            raise GamePakUpdateError("缺少 game_platform/game_server/game_languages 配置")
        return GamePakOptions(
            version=version_choice,
            platform=cfg.game_platform,
            server=cfg.game_server,
            languages=cfg.game_languages,
            audio_languages=cfg.game_audio_languages or cfg.game_languages,
            extract_audio=bool(cfg.extract_audio),
        )

    # 交互模式下，平台、区服、语言等仍保留交互或使用配置
    platform_choice = cfg.game_platform or _prompt_choice("请选择平台", ["Windows", "Android", "iOS"], 0)
    server_choice = cfg.game_server or _prompt_choice("请选择区服", ["OS", "CN"], 0)


    if cfg.game_languages:
        languages = cfg.game_languages
    else:
        languages = _prompt_multi_select("请选择文本语言", ["en", "ja", "ko", "zh-Hans", "zh-CN", "fr", "de", "es", "ru"], ["en", "zh-Hans"])

    audio_languages = cfg.game_audio_languages
    if not audio_languages:
        audio_languages = _prompt_multi_select(
            "请选择语音语言", ["zh", "en", "ja", "ko"], ["zh"]
        )

    extract_audio = cfg.extract_audio
    if extract_audio is None:
        choice = _prompt_choice("是否解包语音资源", ["是", "否"], 0)
        extract_audio = choice == "是"

    return GamePakOptions(
        version=version_choice,
        platform=platform_choice,
        server=server_choice,
        languages=languages,
        audio_languages=audio_languages,
        extract_audio=bool(extract_audio),
    )




def update_from_game_paks(cfg, config_path: Path, output_db_path: Path, progress: Callable[[str], None] | None = None) -> None:
    _log(progress, "[PAK] 正在加载 AES 表...")
    url = cfg.aes_archive_url or AES_ARCHIVE_URL
    archive_text = _fetch_aes_archive(url)
    entries = parse_aes_archive(archive_text)
    if not entries:
        raise GamePakUpdateError("无法解析 AES 表，请检查网络或格式")

    options = _choose_options(entries, cfg)

    selection = select_keys(entries, options.version, options.platform, options.server)
    if not selection.keys:
        raise GamePakUpdateError('Chưa có bộ khóa đọc dữ liệu phù hợp cho '
            f'{options.platform}/{options.server}. Thử lại sau khi nguồn khóa được cập nhật; dữ liệu cũ được giữ nguyên.')

    _log(progress, f"[PAK] 版本: {selection.version_label} | 平台: {options.platform} | 区服: {options.server}")
    for key in selection.keys:
        _log(progress, f"[PAK] AES: {key.pak_name} -> {key.aes_key[:10]}...")

    # The root directory for all PAK files.
    # Use the game install root to find paks in Saved/Resources as well.
    pak_root = cfg.game_install_root
    if not pak_root:
        if _is_tty():
            raw = input("请输入游戏安装目录 (例如 .../Wuthering Waves Game): ").strip().strip('"')
            if raw:
                pak_root = Path(raw)
        if not pak_root:
            raise GamePakUpdateError("找不到游戏安装目录，请设置 game_install_root")

    pak_root = Path(pak_root)
    if not pak_root.exists():
        raise GamePakUpdateError(f"游戏安装目录不存在: {pak_root}")

    _log(progress, f"[PAK] 游戏根目录: {pak_root}")

    # FModelCLI is now the only supported extraction method
    _log(progress, f"[PAK] Paks 目录: {pak_root}")

    # data_root 应该指向项目根目录下的 data/ 目录
    # 这样解包后的结构是 data/ConfigDB, data/TextMap, data/WwiseAudio_Generated
    if cfg.data_root:
        data_root = Path(cfg.data_root).resolve()
    else:
        # Fallback to default
        data_root = Path(__file__).parents[3] / "data"
        
    data_root.mkdir(parents=True, exist_ok=True)

    # 语言探测 (目前 FModelCLI 不支持列表语言，跳过自动探测)
    detected_langs: set[str] = set()
    aes_keys = [entry.aes_key for entry in selection.keys]
    
    if detected_langs:
        detected_list = sorted(detected_langs)
        if _is_tty() and not cfg.game_languages:
            options.languages = _prompt_multi_select("检测到可用语言", detected_list, options.languages)

    # 使用 FModelCLI 提取资源
    from ludiglot.infrastructure.native_extractor import NativeExtractor
    from ludiglot.infrastructure.tool_manager import ToolManager
    
    # Ensure tool exists
    tm = ToolManager()
    tm.ensure_fmodel_cli()

    native_extractor = NativeExtractor()
    
    if not native_extractor.tool_path.exists():
        raise GamePakUpdateError(f"FModelCLI not found at {native_extractor.tool_path}. Please run ToolManager setup.")
    
    _log(progress, f"[PAK] 使用 FModelCLI: {native_extractor.tool_path}")
    _log(progress, f"[PAK] 提取语言: {options.languages}, 音频语言: {options.audio_languages}")

    keys_str = ";".join(aes_keys)

    plan = build_game_pak_update_plan(data_root, options)

    for step in plan.extraction_steps:
        _log(progress, step.message)
        success = native_extractor.run_extraction(pak_root, keys_str, plan.data_root, step.filter,
                                                  progress=progress)
        if not success:
            raise GamePakUpdateError(f'Không đọc được dữ liệu PAK ({step.filter}). '
                                     'Hãy thoát game và thử lại; dữ liệu cũ được giữ nguyên.')
        if not success and step.warn_on_failure:
            _log(progress, step.warn_on_failure)

    if options.extract_audio:
        _log(progress, "[PAK] 语音资源提取完成...")

    for move in plan.directory_moves:
        if not move.source.exists():
            continue
        if move.target.exists():
            shutil.rmtree(move.target, ignore_errors=True)
        shutil.move(str(move.source), str(move.target))
        _log(progress, f"[PAK] 已同步 {move.target.name} 到 {move.target}")

    if plan.staged_fonts_dir.exists():
        plan.fonts_target.mkdir(parents=True, exist_ok=True)
        for ufont in plan.staged_fonts_dir.glob("*.ufont"):
            ttf_name = ufont.stem + ".ttf"
            target_file = plan.fonts_target / ttf_name
            shutil.move(str(ufont), str(target_file))
            _log(progress, f"[PAK] 已同步字体: {ufont.name} -> {ttf_name}")

    for cleanup_path in plan.cleanup_paths:
        shutil.rmtree(cleanup_path, ignore_errors=True)

    # 更新配置文件
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        raw["data_root"] = _to_rel_path(config_path, data_root)
        # 不再保存 game_version，以便下次运行自动获取最新版
        if "game_version" in raw:
            del raw["game_version"]

        raw["game_platform"] = options.platform
        raw["game_server"] = options.server
        raw["game_languages"] = options.languages
        raw["game_audio_languages"] = options.audio_languages
        raw["fonts_root"] = _to_rel_path(config_path, plan.fonts_target)
        if options.extract_audio and plan.audio_wem_root and plan.audio_bnk_root:
            raw["audio_wem_root"] = _to_rel_path(config_path, plan.audio_wem_root)
            raw["audio_bnk_root"] = _to_rel_path(config_path, plan.audio_bnk_root)
        config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # 构建数据库
    from ludiglot.core.text_builder import build_text_db_from_root_all, save_text_db
    from ludiglot.adapters.wuthering_waves.wahu import apply_configured_wahu_provider
    db = build_text_db_from_root_all(data_root)
    stats = apply_configured_wahu_provider(db, cfg)
    if stats:
        _log(
            progress,
            "[WAHU] "
            f"official={stats.official_entries}, exact_id={stats.exact_id_matches}, "
            f"vietnamese={stats.vietnamese_matches}, english_fallback={stats.english_fallbacks}, "
            f"missing={stats.missing_entries}",
        )
    save_text_db(db, output_db_path)
    _log(progress, f"[PAK] 数据库已保存: {output_db_path} ({len(db)} 条)")


def _to_rel_path(config_path: Path, target: Path) -> str:
    project_root = config_path.resolve().parents[1]
    try:
        return str(target.resolve().relative_to(project_root))
    except Exception:
        return str(target)
