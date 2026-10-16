"""External, transactional WAHU data updates. This module never writes to the game.

SHA-256 verifies release byte integrity; it is not a publisher digital signature.
Only a single app-owned active.json is replaced on activation. Generations and
the baseline remain available for rollback without copying a large old cache.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
import uuid
import zipfile

from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider


RELEASE_API = "https://api.github.com/repos/WahuVN/Viet-Hoa-WuWa/releases/latest"
_DOWNLOAD_ROOT = "https://github.com/WahuVN/Viet-Hoa-WuWa/releases/download/"
_TAG_RE = re.compile(r"^v\d+(?:\.\d+){1,3}$")
_SHA_RE = re.compile(r"^[a-fA-F0-9]{64}$")
SOURCE_BUILD_REVISION = 4
Progress = Callable[[str], None]


def extract_game_version(text: str) -> str | None:
    """Read the supported game version, never the VHWuWa app version."""
    plain = re.sub(r"[*_`#]", "", text)
    match = re.search(r"(?<![A-Za-z0-9])(?:Wuthering\s+Waves|WuWa)\s*(?:(?:version|phiên\s+bản|v)\s*)?[:：\-]?\s*(\d+\.\d+(?:\.\d+)?)", plain, re.I)
    return match[1] if match else None


def translation_update_status(release: ReleaseInfo, installed: Mapping[str, Any]) -> tuple[bool, str]:
    """Compare the active generation with GitHub, not the last checked release."""
    local_release = installed.get("release")
    tag = installed.get("release_tag") or (local_release.get("tag") if isinstance(local_release, dict) else None)
    def version(value):
        if not isinstance(value, str) or not re.fullmatch(r"[vV]?\d+(?:\.\d+){1,3}", value.strip()):
            return None
        parts = tuple(int(p) for p in value.strip().lstrip("vV").split("."))
        return parts + (0,) * (4 - len(parts))
    local, remote = version(tag), version(release.tag)
    current = local is not None and remote is not None and local >= remote
    if not current and local is None:
        # An offline archive can prove its exact release by the published hash.
        hashes = installed.get("hashes") or {}
        current = any(a.name in database_archive_names(release.tag) and a.sha256 and
                      hashes.get(a.name) == a.sha256 for a in release.assets)
    if current:
        if (local==remote and installed.get('database_name_mode')=='packaged_seed'
                and installed.get('source_build_revision')!=SOURCE_BUILD_REVISION):
            return True, 'Có bản sửa cách ghép dữ liệu; cập nhật lại bản dịch.'
        game = installed.get("translation_game_version") or release.game_version or extract_game_version(release.body)
        return False, f"Đã là bản mới nhất Wuthering Waves {game or '(chưa rõ phiên bản)'}"
    return True, "Có bản cập nhật mới"


class TranslationUpdateError(RuntimeError):
    pass


class TranslationUpdateCancelled(TranslationUpdateError):
    pass


class EmptyTranslationDatabase(TranslationUpdateError):
    """A valid SQLite seed whose entry table is empty, not corrupt data."""


def database_archive_names(tag: str) -> tuple[str, ...]:
    # Only exact, known data bundles, never an arbitrary installer/client ZIP.
    return (f'App-Dich-WuWa-{tag}.zip', f'VietHoa-WuWa-{tag}.zip')


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReleaseAsset:
        return cls(str(value["name"]), str(value["url"]), int(value["size"]), str(value.get("sha256") or ""))


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    name: str = ""
    body: str = ""
    published_at: str = ""
    game_version: str | None = None
    assets: tuple[ReleaseAsset, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "assets": [asset.to_dict() for asset in self.assets]}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReleaseInfo:
        return cls(
            tag=str(value["tag"]), name=str(value.get("name") or ""),
            body=str(value.get("body") or ""), published_at=str(value.get("published_at") or ""),
            game_version=value.get("game_version"),
            assets=tuple(ReleaseAsset.from_dict(asset) for asset in value.get("assets", [])),
        )

    def asset_named(self, name: str) -> ReleaseAsset:
        found = [asset for asset in self.assets if asset.name == name]
        if len(found) != 1:
            raise TranslationUpdateError(f"Release thiếu hoặc trùng tệp {name}.")
        return found[0]

    def database_asset(self) -> ReleaseAsset:
        for name in database_archive_names(self.tag):
            if any(asset.name == name for asset in self.assets):
                return self.asset_named(name)
        raise TranslationUpdateError('Release chưa có gói dữ liệu bản dịch được hỗ trợ: '
                                     + ' hoặc '.join(database_archive_names(self.tag)))


@dataclass(frozen=True)
class GenerationResult:
    cache_path: Path
    database_path: Path | None
    manifest: dict[str, Any]


class TranslationUpdater:
    def __init__(
        self, root: Path, *, tool_path: Path | None = None,
        source_version: str | None = None, source_data_root: Path | None = None,
        database_path: Path | None = None, opener: Callable[..., Any] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.tool_path = Path(tool_path).resolve() if tool_path else None
        self.source_version = source_version
        self.source_data_root = Path(source_data_root).resolve() if source_data_root else None
        self.database_path = Path(database_path).resolve() if database_path else None
        self._open = opener or urlopen
        self._lock = threading.RLock()

    def check_latest(self) -> ReleaseInfo:
        raw = json.loads(self._read_url(RELEASE_API, limit=2 * 1024 * 1024).decode("utf-8"))
        tag = str(raw.get("tag_name") or "")
        if not _TAG_RE.fullmatch(tag) or raw.get("draft") or raw.get("prerelease"):
            raise TranslationUpdateError("GitHub không trả về release WAHU ổn định hợp lệ.")
        assets: list[ReleaseAsset] = []
        for item in raw.get("assets", []):
            digest = str(item.get("digest") or "")
            sha = digest[7:].lower() if digest.startswith("sha256:") else ""
            assets.append(ReleaseAsset(str(item["name"]), str(item["browser_download_url"]), int(item["size"]), sha))
        checksum = next((asset for asset in assets if asset.name == f"VHWuWa-{tag}-SHA256SUMS.txt"), None)
        relevant = (*database_archive_names(tag), "WuWaVH_EN_99_P.pak")
        if checksum and any(asset.name in relevant and not _SHA_RE.fullmatch(asset.sha256) for asset in assets):
            self._validate_asset_url(checksum, tag)
            data = self._read_url(checksum.url, limit=128 * 1024)
            if checksum.sha256 and hashlib.sha256(data).hexdigest() != checksum.sha256:
                raise TranslationUpdateError("SHA-256 của danh sách checksum không khớp.")
            sums: dict[str, str] = {}
            for line in data.decode("utf-8-sig").splitlines():
                match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line.strip())
                if match:
                    sums[match[2]] = match[1].lower()
            assets = [ReleaseAsset(a.name, a.url, a.size, a.sha256 or sums.get(a.name, "")) for a in assets]
        title = str(raw.get("name") or "")
        body = str(raw.get("body") or "")
        game = extract_game_version(title + "\n" + body)
        result = ReleaseInfo(tag, title, body, str(raw.get("published_at") or ""), game, tuple(assets))
        # Never advertise an installable release without the exact data asset/hash.
        self._validate_asset(result.database_asset(), tag)
        return result

    def active_manifest(self) -> dict[str, Any]:
        path = self.root / "active.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise ValueError("unsupported manifest")
            value = self._resolve_manifest_paths(value)
            self._result(value)
            return value
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise TranslationUpdateError(f"Manifest bản dịch không hợp lệ: {exc}") from exc

    def install_release(
        self, release: ReleaseInfo, official_cache: Path, *,
        progress: Progress | None = None, cancel: threading.Event | None = None,
    ) -> GenerationResult:
        with self._lock:
            self._check_cancel(cancel)
            archive_asset = release.database_asset()
            self._validate_asset(archive_asset, release.tag)
            official_cache = Path(official_cache).resolve()
            if not official_cache.is_file():
                from ludiglot.core.usage_help import MISSING_ENGLISH_DATA
                raise TranslationUpdateError(MISSING_ENGLISH_DATA)
            generation = self._new_generation(release.tag)
            self._check_space(archive_asset.size + official_cache.stat().st_size * 2 + 2 * 1024**3)
            archive = generation / "download.zip"
            self._download(archive_asset, release.tag, archive, progress, cancel)
            database = generation / "project.db"
            self._extract_database(archive, database, cancel)
            try:
                mode = self._validate_database(database, cancel)
            except EmptyTranslationDatabase:
                mode = 'packaged_seed'
                self._emit(progress, 'Database trong gói là bản khởi tạo rỗng; đang đọc nguồn English và PAK EN cùng release…')
            canonical = None
            hashes = {archive_asset.name: archive_asset.sha256.lower()}
            if mode != "en":
                self._emit(progress, "Đang lấy dữ liệu Tên Anh canonical cùng release (không cài vào game)…")
                asset = release.asset_named("WuWaVH_EN_99_P.pak")
                self._validate_asset(asset, release.tag)
                self._check_space(asset.size + database.stat().st_size)
                pak_root = generation / "pak"
                pak_root.mkdir()
                pak = pak_root / asset.name
                self._download(asset, release.tag, pak, progress, cancel)
                hashes[asset.name] = asset.sha256.lower()
                canonical = self._extract_canonical(pak_root, generation / "canonical", progress, cancel)
                # Persist an app-owned EN provider, so later game rescans cannot
                # accidentally reapply the Community DB's Hán-Việt target.
                if mode == 'packaged_seed':
                    from ludiglot.core.release_sources import rebuild_from_release_sources
                    try:
                        database = rebuild_from_release_sources(archive, generation / 'project-en.db', canonical,
                                                                 lambda: self._check_cancel(cancel))
                    except (ValueError, UnicodeError, zipfile.BadZipFile) as exc:
                        raise TranslationUpdateError(f'Không đọc được nguồn trong gói bản dịch: {exc}') from exc
                else:
                    database = self._derive_en_database(database, generation / "project-en.db", canonical, cancel)
                self._validate_database(database, cancel)
            metadata = {
                "release_tag": release.tag, "release": release.to_dict(), "variant": "en",
                "hashes": hashes, "translation_game_version": release.game_version,
                "source_kind": "wahu_release", "database_name_mode": mode,
                "source_build_revision": SOURCE_BUILD_REVISION if mode=='packaged_seed' else None,
                "canonical_en_path": str(generation / "canonical") if canonical is not None else None,
            }
            result = self._merge_and_activate(generation, database, official_cache, metadata, canonical, progress, cancel)
            # Only known staging files are removed. Retain all data generations.
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                self._emit(progress, "Bản mới đã kích hoạt; ZIP tạm chưa dọn được và có thể xóa sau.")
            return result

    def import_database(
        self, path: Path, official_cache: Path, *,
        progress: Progress | None = None, cancel: threading.Event | None = None,
    ) -> GenerationResult:
        with self._lock:
            path = Path(path).resolve()
            official_cache = Path(official_cache).resolve()
            self._check_cancel(cancel)
            if not path.is_file():
                raise TranslationUpdateError("Không tìm thấy database để nhập.")
            wal = Path(str(path) + "-wal")
            if wal.exists() and wal.stat().st_size:
                raise TranslationUpdateError("Hãy đóng WAHU và checkpoint database trước khi nhập (WAL còn dữ liệu).")
            generation = self._new_generation("import")
            self._check_space(path.stat().st_size + official_cache.stat().st_size * 2)
            database = generation / "project.db"
            if path.suffix.casefold() == ".zip":
                local_archive = generation / "import.zip"
                digest = self._copy_file(path, local_archive, cancel)
                self._extract_database(local_archive, database, cancel)
            else:
                digest = self._copy_file(path, database, cancel)
            mode = self._validate_database(database, cancel)
            if mode != "en":
                raise TranslationUpdateError("Database nhập phải có name_mode=en; không tự đoán cách đổi tên Hán Việt.")
            metadata = {
                "release_tag": None, "variant": "en", "source_kind": "local_import",
                "hashes": {path.name: digest}, "database_name_mode": mode,
                "translation_game_version": None,
            }
            result = self._merge_and_activate(generation, database, official_cache, metadata, None, progress, cancel)
            if path.suffix.casefold() == ".zip":
                try:
                    local_archive.unlink(missing_ok=True)
                except OSError:
                    self._emit(progress, "Bản mới đã kích hoạt; ZIP nhập tạm chưa dọn được.")
            return result

    def activate_prepared(
        self, cache_path: Path, database_path: Path | None,
        metadata: dict[str, Any] | None = None,
        *, baseline: Mapping[str, Any] | None = None,
        cancel: threading.Event | None = None,
    ) -> GenerationResult:
        """Activate a trusted, already built official-source generation.

        The cache must be under our generations directory. A provider may remain
        at its existing read-only external path, or be absent for English-only.
        """
        with self._lock:
            cache_path = Path(cache_path).resolve()
            if not cache_path.is_relative_to(self.root / "generations"):
                raise TranslationUpdateError("Cache chuẩn bị phải nằm trong thư mục generations của ứng dụng.")
            self._read_cache(cache_path)
            db = Path(database_path).resolve() if database_path else None
            if db and not db.is_file():
                raise TranslationUpdateError("Không tìm thấy database bản dịch đã chuẩn bị.")
            value = {
                "schema_version": 1, "generation_id": cache_path.parent.name,
                "cache_path": str(cache_path), "database_path": str(db) if db else None,
                "official_data_root": str(self.source_data_root) if self.source_data_root else None,
                "source_version": self.source_version, "release_tag": None,
                "variant": "en", "source_kind": "official_rescan", "activated_at": self._now(),
                **(metadata or {}),
            }
            # Metadata cannot redirect activation to unvalidated cache/provider.
            value.update(schema_version=1, cache_path=str(cache_path), database_path=str(db) if db else None)
            baseline_value = None
            if baseline:
                baseline_value = {
                    "schema_version": 1, "generation_id": "baseline",
                    "release_tag": None, "variant": "en", "source_kind": "existing_baseline",
                    **dict(baseline),
                }
                self._result(baseline_value)
            self._check_cancel(cancel)
            self._commit(value, baseline=baseline_value)
            return self._result(value)

    def rollback(self) -> GenerationResult:
        with self._lock:
            current = self.active_manifest()
            previous = current.get("previous")
            if not isinstance(previous, dict):
                raise TranslationUpdateError("Chưa có bản dữ liệu trước để khôi phục.")
            result = self._result(previous)
            self._read_cache(result.cache_path)
            restored = {**previous, "previous": self._without_previous(current), "activated_at": self._now()}
            self._write_manifest(restored)
            return self._result(restored)

    def _merge_and_activate(
        self, generation: Path, database: Path, official_cache: Path,
        metadata: dict[str, Any], canonical: dict[tuple[str, str], str] | None,
        progress: Progress | None, cancel: threading.Event | None,
    ) -> GenerationResult:
        self._emit(progress, "Đang đối chiếu ID nguồn English và bản Việt đã duyệt…")
        cache = self._read_cache(official_cache)
        self._check_cancel(cancel)
        current = self.active_manifest()
        source_version = self.source_version or current.get("source_version")
        target = metadata.get("translation_game_version")
        cross_version = bool(source_version and target and self._version_pair(source_version) != self._version_pair(target))
        warnings: list[str] = []
        if cross_version:
            warnings.append(f"Bản dịch cho game {target}, văn bản nguồn {source_version}. Quét lại game để có nội dung mới.")
        if not source_version:
            warnings.append("Chưa xác định phiên bản văn bản nguồn; vẫn kiểm tra nội dung English khi WAHU cung cấp.")
        provider = WahuTranslationProvider(
            database, canonical_translations=canonical,
            require_source_english=cross_version or not source_version or not target,
        )
        stats = provider.enrich(cache)
        if not stats.vietnamese_matches:
            raise TranslationUpdateError("Không có bản Việt Tên Anh đã duyệt khớp nguồn hiện tại; giữ nguyên dữ liệu cũ.")
        self._check_cancel(cancel)
        cache_path = generation / "cache.json"
        with cache_path.open("w", encoding="utf-8") as stream:
            json.dump(cache, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        del cache
        self._check_cancel(cancel)
        official_data = self.source_data_root or current.get("official_data_root")
        value = {
            **metadata, "schema_version": 1, "generation_id": generation.name,
            "cache_path": str(cache_path), "database_path": str(database),
            "official_data_root": str(official_data) if official_data else None,
            "source_version": source_version, "coverage": asdict(stats),
            "warnings": warnings, "activated_at": self._now(),
        }
        baseline = {
            "schema_version": 1, "generation_id": "baseline", "cache_path": str(official_cache),
            "database_path": str(self.database_path) if self.database_path else None,
            "official_data_root": str(self.source_data_root) if self.source_data_root else None,
            "source_version": source_version, "release_tag": None, "variant": "en",
            "source_kind": "existing_baseline",
        }
        self._emit(progress, f"Khớp Việt: {stats.vietnamese_matches}; English dự phòng: {stats.english_fallbacks}.")
        self._check_cancel(cancel)
        self._commit(value, baseline=baseline)
        return self._result(value)

    def _new_generation(self, label: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
            raise TranslationUpdateError("Tên generation không hợp lệ.")
        path = self.root / "generations" / f"{label}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def _commit(self, value: dict[str, Any], *, baseline: dict[str, Any] | None = None) -> None:
        current = self.active_manifest()
        if current or baseline:
            value["previous"] = self._without_previous(current or baseline or {})
        self._write_manifest(value)

    def _write_manifest(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"active-{uuid.uuid4().hex}.tmp"
        try:
            with path.open("w", encoding="utf-8") as stream:
                json.dump(self._store_manifest_paths(value), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.root / "active.json")
        finally:
            path.unlink(missing_ok=True)

    def _store_manifest_paths(self, value: dict[str, Any]) -> dict[str, Any]:
        result = dict(value)
        for key in ("cache_path", "database_path", "official_data_root", "canonical_en_path"):
            if result.get(key):
                path = Path(result[key]).resolve()
                if path.is_relative_to(self.root):
                    result[key] = path.relative_to(self.root).as_posix()
                elif self.root.parent.name == "userdata" and path.is_relative_to(self.root.parent):
                    result[key] = "../" + path.relative_to(self.root.parent).as_posix()
        if isinstance(result.get("previous"), dict):
            result["previous"] = self._store_manifest_paths(result["previous"])
        return result

    def _resolve_manifest_paths(self, value: dict[str, Any]) -> dict[str, Any]:
        result = dict(value)
        for key in ("cache_path", "database_path", "official_data_root", "canonical_en_path"):
            if result.get(key):
                path = Path(result[key])
                if not path.is_absolute():
                    path = (self.root / path).resolve()
                    boundary = self.root.parent if self.root.parent.name == "userdata" else self.root
                    if not path.is_relative_to(boundary):
                        raise TranslationUpdateError("Đường dẫn manifest vượt ra khỏi dữ liệu app.")
                result[key] = str(path)
        if isinstance(result.get("previous"), dict):
            result["previous"] = self._resolve_manifest_paths(result["previous"])
        return result

    @staticmethod
    def _without_previous(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if key != "previous"}

    @staticmethod
    def _result(value: dict[str, Any]) -> GenerationResult:
        cache = Path(value["cache_path"])
        db = Path(value["database_path"]) if value.get("database_path") else None
        if not cache.is_absolute() or not cache.is_file() or (db and (not db.is_absolute() or not db.is_file())):
            raise TranslationUpdateError("Generation tham chiếu tệp không tồn tại hoặc đường dẫn không tuyệt đối.")
        return GenerationResult(cache, db, value)

    @staticmethod
    def _read_cache(path: Path) -> dict[str, Any]:
        try:
            with Path(path).open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            if not isinstance(value, dict) or not value:
                raise ValueError("cache rỗng")
            for payload in value.values():
                if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
                    raise ValueError("cache không có matches")
                if any(not isinstance(row, dict) for row in payload["matches"]):
                    raise ValueError("match không hợp lệ")
            return value
        except (OSError, ValueError, TypeError) as exc:
            raise TranslationUpdateError(f"Cache nguồn không hợp lệ: {exc}") from exc

    @staticmethod
    def _validate_database(path: Path, cancel: threading.Event | None = None) -> str:
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("PRAGMA trusted_schema=OFF")
                conn.set_progress_handler(lambda: int(bool(cancel and cancel.is_set())), 10000)
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result != ("ok",):
                    raise TranslationUpdateError("Kiểm tra toàn vẹn SQLite thất bại.")
                columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
                if not {"dbfile", "key", "vi", "status"} <= columns:
                    raise TranslationUpdateError("Database WAHU thiếu các cột ID/bản Việt/trạng thái bắt buộc.")
                if not conn.execute("SELECT 1 FROM entries LIMIT 1").fetchone():
                    raise EmptyTranslationDatabase("Database trong gói bản dịch không có dữ liệu.")
                mode_row = conn.execute("SELECT v FROM meta WHERE k='name_mode'").fetchone()
                mode = str(mode_row[0]).strip().casefold() if mode_row else ""
                if mode not in {"en", "hv", "hanviet"}:
                    raise TranslationUpdateError("Database thiếu name_mode rõ ràng; không đoán biến thể tên.")
                return mode
        except sqlite3.Error as exc:
            TranslationUpdater._check_cancel(cancel)
            raise TranslationUpdateError(f"Không đọc được database WAHU: {exc}") from exc

    @staticmethod
    def _extract_database(archive: Path, target: Path, cancel: threading.Event | None) -> None:
        try:
            with zipfile.ZipFile(archive) as package:
                entries = package.infolist()
                if len(entries) > 100000:
                    raise TranslationUpdateError("ZIP có quá nhiều tệp.")
                candidates = []
                for entry in entries:
                    parts = PurePosixPath(entry.filename.replace("\\", "/"))
                    if parts.is_absolute() or ".." in parts.parts or any(":" in part for part in parts.parts):
                        raise TranslationUpdateError("ZIP chứa đường dẫn không an toàn.")
                    if stat.S_ISLNK(entry.external_attr >> 16):
                        raise TranslationUpdateError("ZIP chứa liên kết không an toàn.")
                    if not entry.is_dir() and parts.name.casefold() == "project.db":
                        candidates.append(entry)
                if len(candidates) != 1:
                    raise TranslationUpdateError("ZIP phải chứa đúng một project.db.")
                entry = candidates[0]
                if not 100 <= entry.file_size <= 4 * 1024**3 or entry.flag_bits & 1:
                    raise TranslationUpdateError("Database trong ZIP có kích thước không hợp lệ hoặc được mã hóa.")
                if shutil.disk_usage(target.parent).free < entry.file_size + 64 * 1024**2:
                    raise TranslationUpdateError("Không đủ chỗ trống để giải nén database WAHU.")
                TranslationUpdater._check_cancel(cancel)
                with package.open(entry) as source, target.open("wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        TranslationUpdater._check_cancel(cancel)
                        destination.write(chunk)
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            if isinstance(exc, TranslationUpdateError):
                raise
            raise TranslationUpdateError(f"Không giải nén được database: {exc}") from exc

    def _extract_canonical(
        self, pak_root: Path, output: Path, progress: Progress | None,
        cancel: threading.Event | None,
    ) -> dict[tuple[str, str], str]:
        if not self.tool_path or not self.tool_path.is_file():
            raise TranslationUpdateError("Bản DB không dùng Tên Anh và chưa có FModelCLI đáng tin cậy để đọc PAK EN.")
        output.mkdir()
        self._emit(progress, "Đang kiểm tra danh sách đường dẫn ConfigDB/en trước khi giải nén…")
        list_log = output / "list.log"
        deadline = time.monotonic() + 180
        self._run_extractor(
            [str(self.tool_path), str(pak_root), "0x" + "0" * 64, "--list", "ConfigDB/en"],
            list_log, deadline, cancel,
        )
        listed = self._validate_pak_listing(list_log)
        if not listed:
            raise TranslationUpdateError("PAK Tên Anh không liệt kê được ConfigDB/en an toàn.")
        log_path = output / "extract.log"
        self._run_extractor(
            [str(self.tool_path), str(pak_root), "0x" + "0" * 64, str(output), "ConfigDB/en"],
            log_path, deadline, cancel,
        )
        canonical = self._read_canonical(output, cancel)
        if not canonical:
            raise TranslationUpdateError("PAK Tên Anh không xuất được ConfigDB/en có ID và văn bản đọc được.")
        self._emit(progress, f"Đã đọc {len(canonical)} ID canonical Tên Anh ngoài thư mục game.")
        return canonical

    @staticmethod
    def _run_extractor(
        arguments: list[str], log_path: Path, deadline: float,
        cancel: threading.Event | None,
    ) -> None:
        TranslationUpdater._check_cancel(cancel)
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                arguments,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                while process.poll() is None:
                    TranslationUpdater._check_cancel(cancel)
                    if time.monotonic() > deadline:
                        raise TranslationUpdateError("FModelCLI quá thời gian đọc PAK Tên Anh; giữ bản cũ.")
                    time.sleep(0.1)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            if process.returncode:
                raise TranslationUpdateError("FModelCLI không đọc được PAK Tên Anh; giữ nguyên dữ liệu cũ.")

    @staticmethod
    def _validate_pak_listing(path: Path) -> set[str]:
        if path.stat().st_size > 8 * 1024**2:
            raise TranslationUpdateError("Danh sách PAK vượt giới hạn an toàn.")
        files: set[str] = set()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("[File] "):
                continue
            name = line[7:].strip().replace("\\", "/")
            parts = PurePosixPath(name)
            if (
                parts.is_absolute() or ".." in parts.parts
                or any(":" in part or "\x00" in part for part in parts.parts)
                or "/configdb/en/" not in "/" + name.casefold()
            ):
                raise TranslationUpdateError("PAK chứa đường dẫn ConfigDB/en không an toàn.")
            files.add(name)
        return files

    @staticmethod
    def _derive_en_database(
        source: Path, target: Path, canonical: dict[tuple[str, str], str],
        cancel: threading.Event | None,
    ) -> Path:
        provider = WahuTranslationProvider(source, canonical_translations=canonical)
        reviewed = provider.load_exact_translations()
        with sqlite3.connect(target) as conn:
            conn.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
            conn.executemany("INSERT INTO meta VALUES (?,?)", [("name_mode", "en"), ("derived_from", "same_release_canonical_en_pak")])
            conn.execute("CREATE TABLE entries(dbfile TEXT, key TEXT, en TEXT, vi TEXT, status TEXT, PRIMARY KEY(dbfile,key))")
            rows = []
            for identity, vietnamese in reviewed.items():
                TranslationUpdater._check_cancel(cancel)
                if vietnamese:
                    rows.append((*identity, provider._source_english.get(identity, ""), vietnamese, "reviewed"))
                if len(rows) >= 1000:
                    conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?)", rows)
                    rows.clear()
            conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?)", rows)
        return target

    @staticmethod
    def _read_canonical(output: Path, cancel: threading.Event | None) -> dict[tuple[str, str], str]:
        translations: dict[tuple[str, str], str] = {}
        duplicates: set[tuple[str, str]] = set()
        for path in output.rglob("*.db"):
            TranslationUpdater._check_cancel(cancel)
            if "/configdb/en/" not in ("/" + path.relative_to(output).as_posix().lower()):
                continue
            try:
                with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
                    conn.execute("PRAGMA query_only=ON")
                    conn.execute("PRAGMA trusted_schema=OFF")
                    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    for table in tables:
                        quoted_table = '"' + table.replace('"', '""') + '"'
                        columns = {row[1].lower(): row[1] for row in conn.execute(f"PRAGMA table_info({quoted_table})")}
                        key = next((columns[name] for name in ("textid", "textmapid", "key", "id") if name in columns), None)
                        text = next((columns[name] for name in ("text", "content", "value") if name in columns), None)
                        if not text:
                            text = columns.get("bindata")
                        if not key or not text:
                            continue
                        qkey, qtext = '"' + key.replace('"', '""') + '"', '"' + text.replace('"', '""') + '"'
                        for text_key, value in conn.execute(f"SELECT {qkey}, {qtext} FROM {quoted_table}"):
                            TranslationUpdater._check_cancel(cancel)
                            value = TranslationUpdater._canonical_value(value, str(text_key))
                            if text_key is None or not isinstance(value, str) or not value.strip():
                                continue
                            identity = (path.name.casefold(), str(text_key))
                            # The overlay does not yet carry table identity. Never guess collisions.
                            if identity in translations or identity in duplicates:
                                translations.pop(identity, None)
                                duplicates.add(identity)
                            else:
                                translations[identity] = value
            except sqlite3.Error as exc:
                raise TranslationUpdateError(f"ConfigDB canonical không hợp lệ: {path.name}: {exc}") from exc
        return translations

    @staticmethod
    def _canonical_value(value: Any, text_key: str) -> str | None:
        if isinstance(value, str):
            return value
        if not isinstance(value, (bytes, bytearray)) or len(value) > 2 * 1024**2:
            return None
        # Support only explicit JSON BinData records with an exact row identity.
        # FlatBuffer byte-scanning/guessed text would violate canonical matching.
        try:
            decoded = json.loads(bytes(value).decode("utf-8-sig"))
        except (UnicodeError, ValueError):
            return None
        if not isinstance(decoded, dict):
            return None
        columns = {str(key).casefold(): item for key, item in decoded.items()}
        embedded_id = next((columns[key] for key in ("textid", "textmapid", "key", "id") if key in columns), None)
        if embedded_id is not None and str(embedded_id) != text_key:
            return None
        candidates = [columns[key] for key in ("text", "content", "value") if isinstance(columns.get(key), str)]
        unique = set(candidates)
        return candidates[0] if len(unique) == 1 else None

    def _download(
        self, asset: ReleaseAsset, tag: str, destination: Path,
        progress: Progress | None, cancel: threading.Event | None,
    ) -> None:
        self._validate_asset(asset, tag)
        self._check_cancel(cancel)
        digest = hashlib.sha256()
        total = 0
        last_percent = -1
        try:
            with self._open(Request(asset.url, headers={"User-Agent": "Ludiglot-Translation-Updater/1.0"}), timeout=20) as response:
                self._validate_redirect(response)
                with destination.open("wb") as stream:
                    while chunk := response.read(1024 * 1024):
                        self._check_cancel(cancel)
                        total += len(chunk)
                        if total > asset.size:
                            raise TranslationUpdateError("Tệp tải về vượt kích thước release khai báo.")
                        stream.write(chunk)
                        digest.update(chunk)
                        percent = int(total * 100 / asset.size)
                        if percent != last_percent:
                            self._emit(progress, f"Đang tải {asset.name}: {percent}%")
                            last_percent = percent
            self._check_cancel(cancel)
            if total != asset.size or digest.hexdigest() != asset.sha256.lower():
                raise TranslationUpdateError("Kích thước hoặc SHA-256 không khớp; không kích hoạt bản tải về.")
        except TranslationUpdateError:
            destination.unlink(missing_ok=True)
            raise
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise TranslationUpdateError(f"Không tải được dữ liệu WAHU: {exc}") from exc

    def _read_url(self, url: str, *, limit: int) -> bytes:
        try:
            with self._open(Request(url, headers={"User-Agent": "Ludiglot-Translation-Updater/1.0", "Accept": "application/vnd.github+json"}), timeout=20) as response:
                self._validate_redirect(response)
                result = response.read(limit + 1)
                if len(result) > limit:
                    raise TranslationUpdateError("Phản hồi GitHub quá lớn.")
                return result
        except (OSError, ValueError) as exc:
            raise TranslationUpdateError(f"Không kiểm tra được GitHub Releases: {exc}") from exc

    @staticmethod
    def _validate_redirect(response: Any) -> None:
        url = response.geturl() if hasattr(response, "geturl") else ""
        if not url:
            return
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host in {"github.com", "api.github.com"} or host.endswith(".githubusercontent.com")):
            raise TranslationUpdateError("GitHub chuyển hướng tới nguồn không được tin cậy.")

    @staticmethod
    def _validate_asset_url(asset: ReleaseAsset, tag: str) -> None:
        if not _TAG_RE.fullmatch(tag):
            raise TranslationUpdateError("Tag release không hợp lệ.")
        expected = _DOWNLOAD_ROOT + quote(tag, safe="") + "/" + quote(asset.name, safe="")
        if asset.url != expected:
            raise TranslationUpdateError("Asset không thuộc đúng release WahuVN/Viet-Hoa-WuWa.")

    @classmethod
    def _validate_asset(cls, asset: ReleaseAsset, tag: str) -> None:
        cls._validate_asset_url(asset, tag)
        if not _SHA_RE.fullmatch(asset.sha256) or not 0 < asset.size <= 2 * 1024**3:
            raise TranslationUpdateError("Asset thiếu SHA-256 hoặc kích thước hợp lệ.")

    def _check_space(self, required: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.root).free < required + 64 * 1024**2:
            raise TranslationUpdateError("Không đủ chỗ trống để staging và giữ bản trước. Cần vài GB cho gói WAHU đầy đủ.")

    @staticmethod
    def _copy_file(source: Path, target: Path, cancel: threading.Event | None) -> str:
        digest = hashlib.sha256()
        with source.open("rb") as stream, target.open("wb") as output:
            while chunk := stream.read(1024 * 1024):
                TranslationUpdater._check_cancel(cancel)
                output.write(chunk)
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _check_cancel(cancel: threading.Event | None) -> None:
        if cancel and cancel.is_set():
            raise TranslationUpdateCancelled("Đã hủy cập nhật; dữ liệu đang dùng không thay đổi.")

    @staticmethod
    def _emit(progress: Progress | None, message: str) -> None:
        if progress:
            progress(message)

    @staticmethod
    def _version_pair(value: str) -> tuple[str, ...]:
        return tuple(re.findall(r"\d+", str(value))[:2])

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
