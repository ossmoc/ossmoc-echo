from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import threading
import zipfile

import pytest

from ludiglot.core.translation_update import (
    RELEASE_API, ReleaseAsset, ReleaseInfo, TranslationUpdateCancelled,
    TranslationUpdateError, TranslationUpdater,
)


TAG = "v3.0.14"
DOWNLOAD = f"https://github.com/WahuVN/Viet-Hoa-WuWa/releases/download/{TAG}/"


@pytest.mark.parametrize("text,expected", [
    ("VHWuWa v3.0.16\nBản dịch cho **Wuthering Waves 3.7.0**", "3.7.0"),
    ("WAHU 3.0.16\nWuWa v3.7", "3.7"),
    ("VHWuWa v3.0.16", None),
])
def test_game_version_from_notes_is_not_wahu_version(text, expected):
    from ludiglot.core.translation_update import extract_game_version
    assert extract_game_version(text) == expected


@pytest.mark.parametrize("installed_tag,available", [("v3.0.16", False), ("3.0.16.0", False),
    ("v3.0.15", True), ("v3.0.9", True), ("v3.0.17", False), (None, True)])
def test_compare_installed_release_numerically(installed_tag, available):
    from ludiglot.core.translation_update import translation_update_status
    latest = ReleaseInfo("v3.0.16", body="## README\nWuthering Waves 3.7\nLots of notes", game_version="3.7")
    result, message = translation_update_status(latest, {"release_tag": installed_tag})
    assert result is available
    assert message == ("Có bản cập nhật mới" if available else "Đã là bản mới nhất Wuthering Waves 3.7")
    assert "README" not in message


def database(path: Path, mode: str = "en", *, text: str = "Xin chao Jinhsi", english: str = "Hello Jinhsi") -> bytes:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE entries(dbfile TEXT,key TEXT,en TEXT,vi TEXT,status TEXT,PRIMARY KEY(dbfile,key))")
        connection.execute("INSERT INTO entries VALUES (?,?,?,?,?)", ("lang_text.db", "DIALOG_1", english, text, "reviewed"))
        connection.execute("CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)")
        connection.execute("INSERT INTO meta VALUES ('name_mode',?)", (mode,))
    return path.read_bytes()


def cache(path: Path, *, english: str = "Hello Jinhsi", chinese: str = "原文") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hello": {"matches": [{"source_json": "lang_text.db", "text_key": "DIALOG_1", "official_en": english, "official_cn": chinese}]}}, ensure_ascii=False), encoding="utf-8")
    return path


def archive(data: bytes, names: tuple[str, ...] = ("WAHU/project.db",)) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name in names:
            package.writestr(name, data)
        package.writestr("WAHU/WAHU Community.exe", b"do not run me")
    return stream.getvalue()


def asset(name: str, data: bytes, *, digest: str | None = None) -> ReleaseAsset:
    return ReleaseAsset(name, DOWNLOAD + name, len(data), digest if digest is not None else hashlib.sha256(data).hexdigest())


class Response(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


def opener(payloads: dict[str, bytes]):
    def open_request(request, timeout):
        assert timeout <= 20
        return Response(payloads[request.full_url], request.full_url)
    return open_request


def release_fixture(tmp_path: Path, mode: str = "en"):
    raw = database(tmp_path / "input.db", mode=mode)
    payload = archive(raw)
    item = asset(f"App-Dich-WuWa-{TAG}.zip", payload)
    release = ReleaseInfo(TAG, "WAHU — Wuthering Waves 3.7.0", "body", "2026-10-02", "3.7.0", (item,))
    return release, {item.url: payload}


def test_missing_english_cache_has_clear_instruction_and_no_staging(tmp_path):
    release, _ = release_fixture(tmp_path)
    updater = TranslationUpdater(tmp_path/'translations',opener=lambda *_a,**_k:pytest.fail('No download'))
    with pytest.raises(TranslationUpdateError, match='Chưa có dữ liệu English'):
        updater.install_release(release,tmp_path/'cache/missing.json')
    assert not (tmp_path/'translations').exists()


def test_release_serializes_without_losing_assets(tmp_path):
    release, _ = release_fixture(tmp_path)
    assert ReleaseInfo.from_dict(release.to_dict()) == release


def test_check_latest_exact_asset_and_source_version(tmp_path):
    release, _ = release_fixture(tmp_path)
    raw = {
        "tag_name": release.tag, "name": release.name, "body": release.body,
        "published_at": release.published_at,
        "assets": [{"name": a.name, "browser_download_url": a.url, "size": a.size, "digest": "sha256:" + a.sha256} for a in release.assets],
    }
    updater = TranslationUpdater(tmp_path / "updates", opener=opener({RELEASE_API: json.dumps(raw).encode()}))
    result = updater.check_latest()
    assert result.game_version == "3.7.0"
    assert result.assets == release.assets
    assert not updater.root.exists()  # Checking doesn't install/create local generations.


def test_renamed_3017_bundle_checks_and_installs_with_hash_validation(tmp_path):
    raw_db = database(tmp_path/'source.db')
    payload = archive(raw_db, ('editor/project.db',))
    item = asset(f'VietHoa-WuWa-{TAG}.zip', payload)
    raw = {'tag_name': TAG, 'assets': [{'name': item.name,
        'browser_download_url': item.url, 'size': item.size, 'digest': 'sha256:'+item.sha256}]}
    updater = TranslationUpdater(tmp_path/'updates', opener=opener({
        RELEASE_API:json.dumps(raw).encode(), item.url:payload}))
    release = updater.check_latest()
    assert release.database_asset() == item
    result = updater.install_release(release, cache(tmp_path/'english.json'))
    assert result.manifest['hashes'][item.name] == item.sha256
    assert result.database_path.is_file()
    assert not list(updater.root.rglob('*.exe'))


def test_unrecognized_zip_is_not_selected_as_translation_data():
    release = ReleaseInfo(TAG, assets=(ReleaseAsset('random-client.zip', DOWNLOAD+'random-client.zip', 100, 'a'*64),))
    with pytest.raises(TranslationUpdateError, match='gói dữ liệu'):
        release.database_asset()


@pytest.mark.parametrize('english,success',[('Hello Jinhsi',True),('Changed source sentence',False)])
def test_empty_seed_recovers_from_same_release_sources_and_en_pak(tmp_path,monkeypatch,english,success):
    source = tmp_path/'seed.db'
    database(source)
    with sqlite3.connect(source) as connection:
        connection.execute('DELETE FROM entries')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,'w') as package:
        package.writestr('editor/project.db',source.read_bytes())
        package.writestr('editor/build_support/TextSource/36_source/3.6_part01.txt',
                         '@@ lang_text.db||Text||DIALOG_1\nEN: Hello Jinhsi\n')
    payload = stream.getvalue()
    bundle = asset(f'VietHoa-WuWa-{TAG}.zip',payload)
    pak = asset('WuWaVH_EN_99_P.pak',b'verified compiled EN pak')
    release = ReleaseInfo(TAG,game_version='3.7',assets=(bundle,pak))
    updater = TranslationUpdater(tmp_path/'updates',opener=opener({bundle.url:payload,pak.url:b'verified compiled EN pak'}))
    monkeypatch.setattr(updater,'_extract_canonical',lambda *_a:{('lang_text.db','DIALOG_1'):'Xin chào Jinhsi'})
    official = cache(tmp_path/'official.json',english=english)
    before = official.read_bytes()
    if success:
        result = updater.install_release(release,official)
        assert result.manifest['database_name_mode']=='packaged_seed'
        row = json.loads(result.cache_path.read_text(encoding='utf-8'))['hello']['matches'][0]
        assert row['official_vi']=='Xin chào Jinhsi'
    else:
        with pytest.raises(TranslationUpdateError,match='giữ nguyên'):
            updater.install_release(release,official)
        assert updater.active_manifest()=={}
    assert official.read_bytes()==before


def test_check_latest_uses_published_checksums_if_api_digest_missing(tmp_path):
    release, _ = release_fixture(tmp_path)
    a = release.assets[0]
    checksum = f"{a.sha256}  {a.name}\n".encode()
    sum_asset = asset(f"VHWuWa-{TAG}-SHA256SUMS.txt", checksum)
    raw = {"tag_name": TAG, "assets": [
        {"name": a.name, "browser_download_url": a.url, "size": a.size},
        {"name": sum_asset.name, "browser_download_url": sum_asset.url, "size": sum_asset.size, "digest": "sha256:" + sum_asset.sha256},
    ]}
    result = TranslationUpdater(tmp_path / "updates", opener=opener({RELEASE_API: json.dumps(raw).encode(), sum_asset.url: checksum})).check_latest()
    assert result.assets[0].sha256 == a.sha256


def test_check_latest_rejects_missing_hash(tmp_path):
    release, _ = release_fixture(tmp_path)
    a = release.assets[0]
    raw = {"tag_name": TAG, "assets": [{"name": a.name, "browser_download_url": a.url, "size": a.size}]}
    with pytest.raises(TranslationUpdateError, match="SHA-256"):
        TranslationUpdater(tmp_path / "updates", opener=opener({RELEASE_API: json.dumps(raw).encode()})).check_latest()


def test_check_network_failure_keeps_active_manifest(tmp_path):
    def offline(*args, **kwargs):
        raise OSError("offline")
    updater = TranslationUpdater(tmp_path / "updates", opener=offline)
    before = cache(tmp_path / "updates/generations/base/cache.json")
    updater.activate_prepared(before, None)
    manifest_bytes = (updater.root / "active.json").read_bytes()
    with pytest.raises(TranslationUpdateError, match="offline"):
        updater.check_latest()
    assert (updater.root / "active.json").read_bytes() == manifest_bytes


def test_install_reviewed_en_without_running_any_downloaded_exe(tmp_path):
    release, payloads = release_fixture(tmp_path)
    old_cache = cache(tmp_path / "old-cache.json")
    old_provider = tmp_path / "input.db"
    before = old_provider.read_bytes()
    messages = []
    updater = TranslationUpdater(tmp_path / "updates", source_version="3.6", database_path=old_provider, opener=opener(payloads))
    result = updater.install_release(release, old_cache, progress=messages.append)
    row = json.loads(result.cache_path.read_text(encoding="utf-8"))["hello"]["matches"][0]
    assert row["official_vi"] == "Xin chao Jinhsi"
    assert row["official_zh"] == "原文"
    assert result.manifest["coverage"]["vietnamese_matches"] == 1
    assert result.manifest["warnings"]  # 3.7 translation vs 3.6 official source.
    assert updater.active_manifest()["cache_path"] == str(result.cache_path)
    assert old_provider.read_bytes() == before
    assert not list(updater.root.rglob("*.exe"))
    assert messages
    rollback = updater.rollback()
    assert rollback.cache_path == old_cache
    assert rollback.database_path == old_provider


def test_bad_hash_never_creates_active_manifest(tmp_path):
    release, payloads = release_fixture(tmp_path)
    wrong = ReleaseAsset(release.assets[0].name, release.assets[0].url, release.assets[0].size, "0" * 64)
    updater = TranslationUpdater(tmp_path / "updates", opener=opener(payloads))
    with pytest.raises(TranslationUpdateError, match="SHA-256"):
        updater.install_release(ReleaseInfo(TAG, assets=(wrong,)), cache(tmp_path / "old.json"))
    assert updater.active_manifest() == {}


@pytest.mark.parametrize("names", [("../project.db",), ("C:/project.db",), ("a/project.db", "b/project.db")])
def test_zip_rejects_unsafe_or_ambiguous_database(tmp_path, names):
    raw = database(tmp_path / "source.db")
    package = tmp_path / "bad.zip"
    package.write_bytes(archive(raw, names))
    with pytest.raises(TranslationUpdateError):
        TranslationUpdater._extract_database(package, tmp_path / "extracted.db", None)
    assert not (tmp_path / "extracted.db").exists()


def test_zip_rejects_symlinks(tmp_path):
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as output:
        entry = zipfile.ZipInfo("project.db")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(entry, "elsewhere")
    with pytest.raises(TranslationUpdateError, match="liên kết"):
        TranslationUpdater._extract_database(package, tmp_path / "extracted.db", None)


def test_cancelled_download_preserves_previous_generation(tmp_path):
    release, payloads = release_fixture(tmp_path)
    updater = TranslationUpdater(tmp_path / "updates", opener=opener(payloads))
    base = cache(tmp_path / "updates/generations/base/cache.json")
    updater.activate_prepared(base, None)
    before = updater.active_manifest()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranslationUpdateCancelled):
        updater.install_release(release, base, cancel=cancel)
    assert updater.active_manifest() == before


def test_missing_en_pak_for_hv_does_not_activate(tmp_path):
    release, payloads = release_fixture(tmp_path, "hv")
    updater = TranslationUpdater(tmp_path / "updates", opener=opener(payloads))
    with pytest.raises(TranslationUpdateError, match="WuWaVH_EN"):
        updater.install_release(release, cache(tmp_path / "old.json"))
    assert updater.active_manifest() == {}


def test_hv_extraction_failure_keeps_previous_en(tmp_path, monkeypatch):
    release, payloads = release_fixture(tmp_path, "hv")
    pak_data = b"fixture pak"
    pak_asset = asset("WuWaVH_EN_99_P.pak", pak_data)
    release = ReleaseInfo(TAG, game_version="3.7", assets=(*release.assets, pak_asset))
    payloads[pak_asset.url] = pak_data
    updater = TranslationUpdater(tmp_path / "updates", opener=opener(payloads))
    base = cache(tmp_path / "updates/generations/base/cache.json")
    updater.activate_prepared(base, None)
    before = updater.active_manifest()
    def fail(*args):
        raise TranslationUpdateError("FModelCLI failed fixture")
    monkeypatch.setattr(updater, "_extract_canonical", fail)
    with pytest.raises(TranslationUpdateError, match="FModelCLI"):
        updater.install_release(release, base)
    assert updater.active_manifest() == before


def test_hv_uses_canonical_en_and_persists_en_provider(tmp_path, monkeypatch):
    release, payloads = release_fixture(tmp_path, "hv")
    pak_data = b"fixture pak"
    pak_asset = asset("WuWaVH_EN_99_P.pak", pak_data)
    release = ReleaseInfo(TAG, game_version="3.7", assets=(*release.assets, pak_asset))
    payloads[pak_asset.url] = pak_data
    updater = TranslationUpdater(tmp_path / "updates", source_version="3.7", opener=opener(payloads))
    monkeypatch.setattr(updater, "_extract_canonical", lambda *args: {("lang_text.db", "DIALOG_1"): "Ban Viet Jinhsi"})
    result = updater.install_release(release, cache(tmp_path / "old.json"))
    assert json.loads(result.cache_path.read_text(encoding="utf-8"))["hello"]["matches"][0]["official_vi"] == "Ban Viet Jinhsi"
    assert TranslationUpdater._validate_database(result.database_path) == "en"
    with sqlite3.connect(result.database_path) as conn:
        assert conn.execute("SELECT vi FROM entries").fetchone() == ("Ban Viet Jinhsi",)


@pytest.mark.parametrize("mode", ["hv", "", "unknown"])
def test_offline_import_rejects_non_en_database(tmp_path, mode):
    path = tmp_path / "local.db"
    database(path, mode=mode)
    updater = TranslationUpdater(tmp_path / "updates")
    with pytest.raises(TranslationUpdateError):
        updater.import_database(path, cache(tmp_path / "old.json"))
    assert updater.active_manifest() == {}


def test_import_en_zip_and_rollback(tmp_path):
    raw = database(tmp_path / "local.db")
    local_zip = tmp_path / "local.zip"
    local_zip.write_bytes(archive(raw))
    old = cache(tmp_path / "old.json")
    updater = TranslationUpdater(tmp_path / "updates")
    result = updater.import_database(local_zip, old)
    assert result.manifest["source_kind"] == "local_import"
    assert result.manifest["hashes"][local_zip.name] == hashlib.sha256(local_zip.read_bytes()).hexdigest()
    assert updater.rollback().cache_path == old


def test_import_rejects_live_wal(tmp_path):
    local = tmp_path / "local.db"
    database(local)
    Path(str(local) + "-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(TranslationUpdateError, match="WAL"):
        TranslationUpdater(tmp_path / "updates").import_database(local, cache(tmp_path / "old.json"))


def test_wrong_schema_or_corruption_rejected(tmp_path):
    corrupt = tmp_path / "bad.db"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises(TranslationUpdateError):
        TranslationUpdater._validate_database(corrupt)
    with sqlite3.connect(tmp_path / "schema.db") as conn:
        conn.execute("CREATE TABLE entries(foo TEXT)")
    with pytest.raises(TranslationUpdateError, match="cột"):
        TranslationUpdater._validate_database(tmp_path / "schema.db")


def test_failed_atomic_activation_retains_old_manifest(tmp_path, monkeypatch):
    import ludiglot.core.translation_update as module
    updater = TranslationUpdater(tmp_path / "updates")
    old = cache(tmp_path / "updates/generations/base/cache.json")
    updater.activate_prepared(old, None)
    before = (updater.root / "active.json").read_bytes()
    local = tmp_path / "local.db"
    database(local)
    def fail(*args):
        raise PermissionError("fixture manifest locked")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(PermissionError):
        updater.import_database(local, old)
    assert (updater.root / "active.json").read_bytes() == before
    assert not list(updater.root.glob("active-*.tmp"))


def test_prepared_cache_must_be_app_owned_but_provider_optional(tmp_path):
    updater = TranslationUpdater(tmp_path / "updates")
    outside = cache(tmp_path / "outside.json")
    with pytest.raises(TranslationUpdateError, match="generations"):
        updater.activate_prepared(outside, None)
    inside = cache(tmp_path / "updates/generations/game-one/cache.json")
    result = updater.activate_prepared(inside, None, {"source_version": "3.7", "official_data_root": str(inside.parent / "data")})
    assert result.database_path is None
    assert result.manifest["source_version"] == "3.7"


def test_portable_relocation_resolves_current_and_previous_paths(tmp_path):
    data_root = tmp_path / "original/userdata"
    updates = data_root / "translations"
    first = cache(updates / "generations/game-one/cache.json")
    second = cache(updates / "generations/game-two/cache.json")
    source = data_root / "official"
    source.mkdir()
    provider = updates / "generations/game-one/project.db"
    database(provider)
    updater = TranslationUpdater(updates, source_data_root=source)
    updater.activate_prepared(first, provider)
    updater.activate_prepared(second, provider)
    stored = json.loads((updates / "active.json").read_text(encoding="utf-8"))
    assert stored["cache_path"].startswith("generations/")
    assert stored["official_data_root"] == "../official"
    moved = tmp_path / "moved/userdata"
    shutil.copytree(data_root, moved)
    relocated = TranslationUpdater(moved / "translations")
    result = relocated.active_manifest()
    assert Path(result["cache_path"]).is_relative_to(moved)
    assert Path(result["official_data_root"]) == moved / "official"
    assert relocated.rollback().cache_path == moved / "translations/generations/game-one/cache.json"


def test_relative_manifest_cannot_escape_userdata(tmp_path):
    updates = tmp_path / "userdata/translations"
    updates.mkdir(parents=True)
    (updates / "active.json").write_text(json.dumps({"schema_version": 1, "cache_path": "../../outside.json"}), encoding="utf-8")
    with pytest.raises(TranslationUpdateError, match="vượt"):
        TranslationUpdater(updates).active_manifest()


def test_insufficient_space_fails_before_activation(tmp_path, monkeypatch):
    import ludiglot.core.translation_update as module
    release, payloads = release_fixture(tmp_path)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 0})())
    updater = TranslationUpdater(tmp_path / "updates", opener=opener(payloads))
    with pytest.raises(TranslationUpdateError, match="chỗ trống"):
        updater.install_release(release, cache(tmp_path / "old.json"))
    assert updater.active_manifest() == {}


def test_canonical_reader_does_not_guess_colliding_table_ids(tmp_path):
    target = tmp_path / "canonical/Client/Content/Aki/ConfigDB/en/lang_text.db"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE One(Key TEXT,Text TEXT)")
        conn.execute("CREATE TABLE Two(Key TEXT,Text TEXT)")
        conn.execute("INSERT INTO One VALUES ('shared','one')")
        conn.execute("INSERT INTO Two VALUES ('shared','two')")
        conn.execute("INSERT INTO One VALUES ('safe','Jinhsi')")
    assert TranslationUpdater._read_canonical(tmp_path / "canonical", None) == {("lang_text.db", "safe"): "Jinhsi"}


def test_source_mismatch_does_not_activate_empty_vi_generation(tmp_path):
    release, payloads = release_fixture(tmp_path)
    updater = TranslationUpdater(tmp_path / "updates", source_version="3.6", opener=opener(payloads))
    with pytest.raises(TranslationUpdateError, match="Không có bản Việt"):
        updater.install_release(release, cache(tmp_path / "old.json", english="A reused identity"))
    assert updater.active_manifest() == {}


def test_first_prepared_game_rescan_retains_trusted_baseline_without_copy(tmp_path):
    old = cache(tmp_path / "legacy-cache.json")
    local = tmp_path / "local.db"
    database(local)
    old_data = tmp_path / "old-data"
    old_data.mkdir()
    updates = tmp_path / "userdata/translations"
    new = cache(updates / "generations/game-one/cache.json")
    new_data = new.parent / "data"
    new_data.mkdir()
    baseline = {"cache_path": str(old), "database_path": str(local), "official_data_root": str(old_data), "source_version": "3.6", "release_tag": "v3.0.9"}
    updater = TranslationUpdater(updates)
    result = updater.activate_prepared(new, local, {"source_version": "3.7", "official_data_root": str(new_data), "release_tag": "v3.0.9"}, baseline=baseline)
    assert result.manifest["release_tag"] == "v3.0.9"
    restored = updater.rollback()
    assert restored.cache_path == old
    assert restored.manifest["official_data_root"] == str(old_data)
    assert restored.manifest["source_version"] == "3.6"


def test_cancel_requested_by_final_progress_message_prevents_commit(tmp_path):
    raw = tmp_path / "local.db"
    database(raw)
    old = cache(tmp_path / "old.json")
    cancel = threading.Event()
    def progress(message):
        if message.startswith("Khớp Việt:"):
            cancel.set()
    updater = TranslationUpdater(tmp_path / "updates")
    with pytest.raises(TranslationUpdateCancelled):
        updater.import_database(raw, old, progress=progress, cancel=cancel)
    assert updater.active_manifest() == {}


@pytest.mark.parametrize("unsafe", [
    "../ConfigDB/en/lang_text.db", "C:/ConfigDB/en/lang_text.db",
    "/ConfigDB/en/lang_text.db", "Client/ConfigDB/en/../../outside.db",
])
def test_pak_pre_listing_rejects_escaped_paths(tmp_path, unsafe):
    listing = tmp_path / "list.log"
    listing.write_text("[Info] ready\n[File] " + unsafe, encoding="utf-8")
    with pytest.raises(TranslationUpdateError, match="đường dẫn"):
        TranslationUpdater._validate_pak_listing(listing)


def test_pak_pre_listing_accepts_only_safe_configdb_paths(tmp_path):
    listing = tmp_path / "list.log"
    listing.write_text("[Scan] Final Files: 1\n[File] Client/Content/Aki/ConfigDB/en/lang_text.db\n", encoding="utf-8")
    assert TranslationUpdater._validate_pak_listing(listing) == {"Client/Content/Aki/ConfigDB/en/lang_text.db"}


def test_canonical_reader_supports_exact_json_bindata_but_not_guessed_blobs(tmp_path):
    target = tmp_path / "canonical/Client/Content/Aki/ConfigDB/en/lang_text.db"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE Data(Id TEXT,BinData BLOB)")
        conn.executemany("INSERT INTO Data VALUES (?,?)", [
            ("good", json.dumps({"Id": "good", "Content": "Jinhsi"}).encode()),
            ("content-only", json.dumps({"Text": "Changli"}).encode()),
            ("wrong", json.dumps({"Id": "another", "Content": "Wrong identity"}).encode()),
            ("ambiguous", json.dumps({"Text": "one", "Content": "two"}).encode()),
            ("raw", b"\x00\x03Jinhsi human text not JSON"),
        ])
    assert TranslationUpdater._read_canonical(tmp_path / "canonical", None) == {
        ("lang_text.db", "good"): "Jinhsi", ("lang_text.db", "content-only"): "Changli",
    }


def test_canonical_extraction_lists_before_extracting(tmp_path, monkeypatch):
    tool = tmp_path / "trusted-extractor.exe"
    tool.write_bytes(b"fixture never executed")
    pak = tmp_path / "pak"
    pak.mkdir()
    output = tmp_path / "canonical"
    calls = []
    def run(arguments, log_path, deadline, cancel):
        calls.append(arguments)
        if "--list" in arguments:
            log_path.write_text("[File] Client/Content/Aki/ConfigDB/en/lang_text.db\n", encoding="utf-8")
        else:
            target = output / "Client/Content/Aki/ConfigDB/en/lang_text.db"
            target.parent.mkdir(parents=True)
            with sqlite3.connect(target) as conn:
                conn.execute("CREATE TABLE Data(Key TEXT,Text TEXT)")
                conn.execute("INSERT INTO Data VALUES ('key','Jinhsi')")
    updater = TranslationUpdater(tmp_path / "updates", tool_path=tool)
    monkeypatch.setattr(updater, "_run_extractor", run)
    assert updater._extract_canonical(pak, output, None, None) == {("lang_text.db", "key"): "Jinhsi"}
    assert "--list" in calls[0]
    assert "--list" not in calls[1]
    assert all(call[2] == "0x" + "0" * 64 and call[-1] == "ConfigDB/en" for call in calls)


def test_canonical_bad_pre_listing_never_runs_extraction(tmp_path, monkeypatch):
    tool = tmp_path / "trusted-extractor.exe"
    tool.write_bytes(b"fixture never executed")
    pak = tmp_path / "pak"
    pak.mkdir()
    calls = []
    def run(arguments, log_path, deadline, cancel):
        calls.append(arguments)
        log_path.write_text("[File] ../ConfigDB/en/escape.db\n", encoding="utf-8")
    updater = TranslationUpdater(tmp_path / "updates", tool_path=tool)
    monkeypatch.setattr(updater, "_run_extractor", run)
    with pytest.raises(TranslationUpdateError):
        updater._extract_canonical(pak, tmp_path / "canonical", None, None)
    assert len(calls) == 1
