"""Curate a new release without modifying the working installation.

Source selection is an allowlist. Personal data never enters the public tree.
The executable is copied from the already-tested clean PyInstaller output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import zipfile
from importlib.metadata import distributions
import sys

VERSION = '1.0.0'


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def inside(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError('Path leaves its data directory')
    return target


def copy_file(original: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, target)


def curate_source(root: Path, target: Path, gpl: Path):
    target.mkdir(parents=True, exist_ok=False)
    for folder in ('src', 'tests'):
        for source in (root / folder).rglob('*'):
            if source.is_file() and source.suffix in ('.py', '.qss'):
                copy_file(source, target / source.relative_to(root))
    mapping = {
        'release/README.md': 'README.md', 'release/NOTICE.md': 'NOTICE.md',
        'release/THIRD_PARTY.md': 'THIRD_PARTY.md', 'release/SOURCES.md': 'SOURCES.md',
        'release/BUILD.md': 'docs/BUILD.md', 'release/USER-GUIDE.md': 'docs/USER-GUIDE.md',
        'release/pyproject.toml': 'pyproject.toml', 'LICENSE': 'LICENSES/Ludiglot-MIT.txt',
        'requirements-desktop-lock.txt': 'requirements-desktop-lock.txt',
        'Ludiglot.spec': 'Ludiglot.spec',
        'models/light_ocr/NOTICE.md': 'models/light_ocr/NOTICE.md',
        'tools/build_portable.py': 'tools/build_portable.py',
        'tools/build_public.py': 'tools/build_public.py',
        'tools/download_light_models.py': 'tools/download_light_models.py',
        'tools/prepare_release.py': 'tools/prepare_release.py',
        'release/windows-ci.yml': '.github/workflows/windows.yml',
    }
    for original, relative in mapping.items():
        copy_file(root / original, target / relative)
    copy_file(gpl, target / 'LICENSE')
    # Generated source policy, not copied from a machine-specific gitignore.
    (target / '.gitignore').write_text(
        '.venv/\n__pycache__/\n*.py[cod]\n.pytest_cache/\nartifacts/\n'
        'userdata/\ncache/\nlog/\ndata/\nconfig/settings.json\n'
        '*.db\n*.db-shm\n*.db-wal\n*.onnx\n*.exe\n.env\n', encoding='utf-8')


def public_bundle(root: Path, clean_runtime: Path, source: Path, target: Path, licenses: Path):
    target.mkdir(parents=True, exist_ok=False)
    copy_file(clean_runtime / 'OssmocEcho.exe', target / 'OssmocEcho.exe')
    shutil.copytree(clean_runtime / '_internal', target / '_internal')
    shutil.copytree(licenses, target / 'licenses')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        copy_file(python_license, target / 'licenses/Python/LICENSE.txt')
    for distribution in distributions():
        package = distribution.metadata['Name']
        for entry in distribution.files or ():
            relative = Path(str(entry))
            if relative.is_absolute() or '..' in relative.parts:
                continue
            if relative.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'AUTHORS')):
                original = distribution.locate_file(entry)
                if original.is_file():
                    copy_file(original, target / 'licenses' / package / relative)
    for name in ('LICENSE', 'NOTICE.md', 'THIRD_PARTY.md', 'SOURCES.md'):
        copy_file(source / name, target / name)
    # Windows treats LICENSES and licenses as the same directory.
    shutil.copytree(source / 'LICENSES', target / 'licenses', dirs_exist_ok=True)
    copy_file(source / 'docs/USER-GUIDE.md', target / 'HUONG-DAN.md')
    copy_file(root / 'models/light_ocr/NOTICE.md', target / 'LICENSE-OCR-MODELS.md')


def personal_bundle(installed: Path, public: Path, target: Path):
    shutil.copytree(public, target)
    base = installed / 'userdata'
    active = json.loads((base / 'translations/active.json').read_text(encoding='utf-8-sig'))
    # Validate the entire generation before copying or changing its COPY manifest.
    db = inside(base / 'translations', active['database_path'])
    cache = inside(base / 'translations', active['cache_path'])
    if not db.is_file() or not cache.is_file():
        raise ValueError('The active generation is incomplete')
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Active database failed SQLite quick_check')
        destination = target / 'userdata/translations' / active['database_path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Include committed WAL content, never copy transient shm/wal lock files.
        with sqlite3.connect(destination) as backup:
            connection.backup(backup)
    copy_file(cache, target / 'userdata/translations' / active['cache_path'])
    active.pop('previous', None)
    active['official_data_root'] = '../data'
    write_json(target / 'userdata/translations/active.json', active)
    config = json.loads((base / 'config/settings.json').read_text(encoding='utf-8-sig'))
    config.update(db_path='translations/' + active['cache_path'],
                  wahu_db_path='translations/' + active['database_path'],
                  data_root='data', fonts_root='data/Fonts', image_path='cache/capture.png')
    # Old UI fields may remain as migration input. No logs, captures or backups are copied.
    write_json(target / 'userdata/config/settings.json', config)
    skill = base / 'data/ConfigDB/db_skill.db'
    if skill.is_file():
        copy_file(skill, target / 'userdata/data/ConfigDB/db_skill.db')
    if (installed / 'tools').is_dir():
        shutil.copytree(installed / 'tools', target / 'tools')
    (target / 'BAN-RIENG.txt').write_text(
        'BẢN CÁ NHÂN — KHÔNG ĐƯA LÊN GITHUB\n'
        'Có dữ liệu/cấu hình riêng. Chỉ giữ bộ dữ liệu đang dùng; bản phục hồi cũ\n'
        'vẫn nằm ở thư mục làm việc gốc, không nằm trong gói này.\n', encoding='utf-8')


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def archive(folder: Path, destination: Path):
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and '.git' not in path.relative_to(folder).parts:
                output.write(path, str(Path(folder.name) / path.relative_to(folder)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    # Never overwrite an existing release/user folder; failure leaves original intact.
    output.mkdir(parents=True, exist_ok=False)
    installed = root / 'artifacts/portable/Ludiglot'
    runtime = root / 'artifacts/redesign-qa/dist/Ludiglot'
    if digest(runtime / 'OssmocEcho.exe') != digest(installed / 'OssmocEcho.exe'):
        raise ValueError('Clean runtime does not match the tested installed executable')
    source = output / 'source/ossmoc-echo'
    curate_source(root, source, installed / 'licenses/PyQt6/LICENSE')
    public = output / 'public/OssmocEcho'
    public_bundle(root, runtime, source, public, installed / 'licenses')
    personal = output / 'personal/OssmocEcho'
    personal_bundle(installed, public, personal)
    for folder, name in ((source, 'source'), (public, 'Windows-x64'), (personal, 'PERSONAL')):
        archive(folder, output / f'OssmocEcho-v{VERSION}-{name}.zip')
    # Only public checksums go to GitHub. Personal checksum remains separate.
    public_names = [f'OssmocEcho-v{VERSION}-{kind}.zip' for kind in ('source', 'Windows-x64')]
    (output / 'SHA256SUMS.txt').write_text(
        ''.join(f'{digest(output / name)}  {name}\n' for name in public_names), encoding='ascii')
    print(json.dumps({'output': str(output), 'exe_sha256': digest(public / 'OssmocEcho.exe'),
                      'source_files': sum(p.is_file() for p in source.rglob('*'))}))


if __name__ == '__main__':
    main()
