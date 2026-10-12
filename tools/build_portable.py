"""Build a private portable bundle from this machine's existing translation data."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from importlib.metadata import distribution, PackageNotFoundError
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ludiglot.core.config import load_config
from ludiglot.core.desktop_models import migrate_desktop_settings
from ludiglot.core.preferences import ConfigJsonStore, atomic_write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument('--icon-only',action='store_true')
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "settings.json")
    args = parser.parse_args()
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    from PIL import Image, ImageDraw
    icon = Image.new("RGBA", (256, 256), (20, 28, 38, 255))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((25, 25, 231, 211), radius=42, fill="#2dd4bf")
    draw.polygon([(65, 195), (65, 238), (110, 198)], fill="#2dd4bf")
    draw.line([(82,65),(82,161)],fill='#14202a',width=20)
    for y,end in ((65,166),(111,149),(161,166)):
        draw.line([(82,y),(end,y)],fill='#14202a',width=20)
    icon.save(artifacts / "app-icon.ico", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    if args.icon_only:
        return
    bundle = artifacts / "portable" / "Ludiglot"
    if not args.skip_build:
        # COLLECT replaces its output tree. Build elsewhere so rebuilding can
        # never remove the user's portable settings, generations or logs.
        build_output = artifacts / "build-output" / uuid4().hex
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(build_output), "--workpath", str(artifacts / "pyinstaller-work"), str(ROOT / "Ludiglot.spec")], check=True, cwd=ROOT)
        shutil.copytree(build_output / "Ludiglot", bundle, dirs_exist_ok=True)
        for name in ("icuuc.dll", "icudt78.dll"):
            stale = bundle / "_internal" / name
            if stale.is_file():
                quarantine = artifacts / "obsolete-binaries" / uuid4().hex
                quarantine.mkdir(parents=True)
                shutil.move(str(stale), quarantine / name)
    if not (bundle / "OssmocEcho.exe").is_file():
        raise RuntimeError("Missing executable; run build first")
    # Existing shortcuts keep working and share the same single-instance key.
    shutil.copy2(bundle / 'OssmocEcho.exe', bundle / 'Ludiglot.exe')
    raw = ConfigJsonStore(args.config).load_raw()
    cfg = load_config(args.config, validate_data=False)
    userdata = bundle / "userdata"
    config = userdata / "config" / "settings.json"
    if not config.exists():
        required = (cfg.db_path.stat().st_size if cfg.db_path.is_file() else 0) + (cfg.wahu_db_path.stat().st_size if cfg.wahu_db_path and cfg.wahu_db_path.is_file() else 0)
        if shutil.disk_usage(bundle).free < required + 512 * 1024**2:
            raise RuntimeError("Insufficient disk space to provision translation data")
        (userdata / "cache").mkdir(parents=True, exist_ok=True)
        if cfg.db_path.is_file():
            shutil.copy2(cfg.db_path, userdata / "cache" / "game_text_db.json")
        if cfg.wahu_db_path and cfg.wahu_db_path.is_file():
            (userdata / "translations" / "baseline").mkdir(parents=True, exist_ok=True)
            shutil.copy2(cfg.wahu_db_path, userdata / "translations" / "baseline" / "project.db")
            raw["wahu_db_path"] = "translations/baseline/project.db"
        raw.update(db_path="cache/game_text_db.json", data_root="data", image_path="cache/capture.png", auto_rebuild_db=False,
               ocr_backend="windows", ocr_lang="en", ocr_mode="cpu", extract_audio=False, play_audio=False,
               scan_audio_on_start=False, fonts_root="data/Fonts", desktop_onboarding_done=False)
        for key in ("en_json", "zh_json", "game_data_root", "game_audio_root", "audio_cache_path", "audio_wem_root", "audio_bnk_root", "vgmstream_path", "wwiser_path"):
            raw.pop(key, None)
        raw = migrate_desktop_settings(raw)
        (userdata / "data").mkdir(exist_ok=True)
        if cfg.data_root:
            skill = cfg.data_root / "ConfigDB" / "db_skill.db"
            if skill.is_file():
                target = userdata / "data" / "ConfigDB" / "db_skill.db"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(skill, target)
        atomic_write_json(config, raw)
    else:
        print("Existing portable settings and data preserved.")
    aes_cache = ROOT / "cache" / "aes_archive.md"
    if aes_cache.is_file():
        shutil.copy2(aes_cache, userdata / "cache" / "aes_archive.md")
    for source in (ROOT / "tools" / "FModelCLI.exe",):
        if source.is_file():
            (bundle / "tools").mkdir(exist_ok=True)
            shutil.copy2(source, bundle / "tools" / source.name)
    if (ROOT / "tools" / ".data").is_dir():
        shutil.copytree(ROOT / "tools" / ".data", bundle / "tools" / ".data", dirs_exist_ok=True)
    shutil.copy2(artifacts / "app-icon.ico", bundle / "app-icon.ico")
    guide = ROOT / "docs" / "usage" / "desktop-vi.md"
    if guide.is_file():
        shutil.copy2(guide, bundle / "HUONG-DAN.md")
    license_file = ROOT / "LICENSE"
    if license_file.is_file():
        shutil.copy2(license_file, bundle / "LICENSE-Ludiglot.txt")
    model_notice = ROOT / 'models' / 'light_ocr' / 'NOTICE.md'
    if model_notice.is_file():
        shutil.copy2(model_notice, bundle/'LICENSE-OCR-MODELS.md')
    for package in ("PyQt6", "PyQt6-Qt6", "PyQt6-sip", "Pillow", "rapidfuzz", "mss", "pywin32", "pyinstaller", "windows-capture", "numpy", "opencv-python",
                    'rapidocr','onnxruntime','opencv-python-headless','pyclipper','Shapely','PyYAML','omegaconf','requests','urllib3','certifi','charset-normalizer','idna','tqdm','colorlog','colorama','protobuf','flatbuffers','packaging','six'):
        try:
            metadata = distribution(package)
            for item in metadata.files or ():
                if Path(str(item)).name.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
                    source = metadata.locate_file(item)
                    if source.is_file():
                        destination = bundle / "licenses" / package / source.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
        except PackageNotFoundError:
            continue
    print(f"Portable app ready: {bundle / 'OssmocEcho.exe'}")
    print("Private machine-local bundle: includes game/community data; do not publish it automatically.")


if __name__ == "__main__":
    main()
