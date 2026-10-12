from pathlib import Path
import importlib.util
import json
import sqlite3
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_packaging', ROOT / 'tools/prepare_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_source_allowlist_keeps_code_but_never_user_data(tmp_path):
    original = tmp_path / 'original'
    # Create all mandatory mapped inputs from the actual curated source policy.
    for relative in ('src/ludiglot/app.py', 'tests/test_app.py', 'src/ludiglot/style.qss',
                     'release/README.md', 'release/NOTICE.md', 'release/THIRD_PARTY.md',
                     'release/SOURCES.md', 'release/BUILD.md', 'release/USER-GUIDE.md',
                     'release/pyproject.toml', 'release/windows-ci.yml', 'LICENSE', 'requirements-desktop-lock.txt',
                     'Ludiglot.spec', 'models/light_ocr/NOTICE.md', 'tools/build_portable.py',
                     'tools/build_public.py', 'tools/download_light_models.py', 'tools/prepare_release.py',
                     'userdata/config/settings.json', 'src/private.png', 'tests/private.jpg',
                     'src/__pycache__/app.pyc', '.git/config', 'cache/project.db', '.env'):
        path = original / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('sample')
    gpl = tmp_path / 'gpl'
    gpl.write_text('GPL version 3')
    target = tmp_path / 'source'
    release.curate_source(original, target, gpl)
    assert (target / 'LICENSE').read_text() == 'GPL version 3'
    assert (target / 'LICENSES/Ludiglot-MIT.txt').read_text() == 'sample'
    assert (target / 'src/ludiglot/app.py').is_file()
    assert (target / 'tests/test_app.py').is_file()
    assert not any(p.suffix in ('.png', '.jpg', '.db', '.pyc') for p in target.rglob('*'))
    assert not (target / 'userdata').exists()
    assert not (target / '.git').exists()
    assert not (target / '.env').exists()


@pytest.mark.parametrize('relative', ['../outside.db', '/outside.db'])
def test_reject_manifest_escape(tmp_path, relative):
    with pytest.raises(ValueError):
        release.inside(tmp_path / 'translations', relative)


def test_source_refuses_overwrite(tmp_path):
    with pytest.raises(FileExistsError):
        release.curate_source(tmp_path, tmp_path, tmp_path / 'GPL')


def test_archive_safe_and_no_git_history(tmp_path):
    folder = tmp_path / 'ossmoc-echo'
    (folder / '.git').mkdir(parents=True)
    (folder / '.git/config').write_text('private history')
    (folder / 'README.md').write_text('readme')
    archive = tmp_path / 'source.zip'
    release.archive(folder, archive)
    with zipfile.ZipFile(archive) as output:
        assert output.namelist() == ['ossmoc-echo/README.md']
        assert output.testzip() is None
    with pytest.raises(FileExistsError):
        release.archive(folder, archive)


def test_personal_data_relocates_and_excludes_previous_logs(tmp_path):
    installed = tmp_path / 'installed'
    data = installed / 'userdata'
    translations = data / 'translations'
    generation = translations / 'generations/current'
    generation.mkdir(parents=True)
    with sqlite3.connect(generation / 'project.db') as db:
        db.execute('CREATE TABLE sample (text TEXT)')
        db.execute("INSERT INTO sample VALUES ('verified')")
    (generation / 'cache.json').write_text('{}')
    release.write_json(translations / 'active.json', {
        'schema_version': 1, 'database_path': 'generations/current/project.db',
        'cache_path': 'generations/current/cache.json', 'previous': {'database_path': 'baseline/project.db'}})
    release.write_json(data / 'config/settings.json', {'db_path': 'C:/old/cache.json',
        'wahu_db_path': 'C:/old/project.db', 'auto_wuwa': {'enabled': False}})
    (data / 'logs').mkdir()
    (data / 'logs/private.log').write_text('private')
    public = tmp_path / 'public'
    public.mkdir()
    (public / 'OssmocEcho.exe').write_bytes(b'test')
    target = tmp_path / 'Tiếng Việt portable'
    release.personal_bundle(installed, public, target)
    active = json.loads((target / 'userdata/translations/active.json').read_text())
    assert 'previous' not in active
    settings = json.loads((target / 'userdata/config/settings.json').read_text())
    assert settings['db_path'] == 'translations/generations/current/cache.json'
    assert settings['auto_wuwa'] == {'enabled': False}
    with sqlite3.connect(target / 'userdata' / settings['wahu_db_path']) as db:
        assert db.execute('SELECT text FROM sample').fetchone() == ('verified',)
    assert not (target / 'userdata/logs').exists()
    assert 'previous' in json.loads((translations / 'active.json').read_text())


def test_bad_personal_generation_cannot_escape(tmp_path):
    installed = tmp_path / 'installed'
    release.write_json(installed / 'userdata/translations/active.json', {
        'database_path': '../../elsewhere.db', 'cache_path': 'cache.json'})
    public = tmp_path / 'public'
    public.mkdir()
    with pytest.raises(ValueError):
        release.personal_bundle(installed, public, tmp_path / 'target')


def test_digest_known_value(tmp_path):
    path = tmp_path / 'empty'
    path.write_bytes(b'')
    assert release.digest(path) == 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
