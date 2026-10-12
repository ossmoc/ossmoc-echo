from __future__ import annotations

import json
from dataclasses import dataclass
import re
from typing import Iterable, List


_VERSION_RE = re.compile(r"^##\s+([0-9]+(?:\.[0-9]+)*)\s*(\([^\n]+\))?")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_PAKCHUNK_RE = re.compile(r"pakchunk[-_]?([0-9]+)", re.IGNORECASE)


@dataclass(frozen=True)
class AesKeyEntry:
    version_label: str
    version_number: str
    pak_name: str
    aes_key: str
    os: str
    server: str


@dataclass(frozen=True)
class AesSelection:
    version_label: str
    os: str
    server: str
    keys: list[AesKeyEntry]


def _normalize_os(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    low = value.lower()
    if "windows" in low:
        return "Windows"
    if "android" in low:
        return "Android"
    if "ios" in low:
        return "iOS"
    return value


def _normalize_server(value: str) -> list[str]:
    value = value.strip().upper()
    if not value:
        return []
    if "CN" in value and "OS" in value:
        return ["CN", "OS"]
    if "CN" in value:
        return ["CN"]
    if "OS" in value:
        return ["OS"]
    return [value]


def _clean_pak_name(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    if "(" in raw:
        raw = raw.split("(", 1)[0].strip()
    return raw


def _parse_table_row(line: str) -> list[str] | None:
    match = _TABLE_ROW_RE.match(line.strip())
    if not match:
        return None
    cols = [c.strip() for c in match.group(1).split("|")]
    if len(cols) < 4:
        return None
    return cols


def parse_aes_archive(text: str) -> list[AesKeyEntry]:
    entries: list[AesKeyEntry] = []
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            main_key = data.get("mainKey")
            dynamic_keys = data.get("dynamicKeys", [])
            for os_n in ["Windows", "Android", "iOS"]:
                for svr in ["OS", "CN"]:
                    if main_key:
                        entries.append(
                            AesKeyEntry(
                                version_label="Latest",
                                version_number="Latest",
                                pak_name="main",
                                aes_key=main_key.strip(),
                                os=os_n,
                                server=svr,
                            )
                        )
                    for idx, dyn in enumerate(dynamic_keys):
                        k = dyn.get("key")
                        if k:
                            entries.append(
                                AesKeyEntry(
                                    version_label="Latest",
                                    version_number="Latest",
                                    pak_name=f"pakchunk{1000 + idx}",
                                    aes_key=k.strip(),
                                    os=os_n,
                                    server=svr,
                                )
                            )
            return entries
        except Exception:
            pass

    current_label = ""
    current_version = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        version_match = _VERSION_RE.match(line)
        if version_match:
            current_version = version_match.group(1)
            tail = version_match.group(2) or ""
            current_label = f"{current_version} {tail}".strip()
            continue
        row = _parse_table_row(line)
        if not row:
            continue
        if any("PAK" in c.upper() for c in row[:2]) and any("AES" in c.upper() for c in row[:2]):
            continue
        if set(c.strip("-: ") for c in row[:4]) == {""}:
            continue
        pak_name = _clean_pak_name(row[0])
        aes_key = row[1].strip()
        os_name = _normalize_os(row[2])
        servers = _normalize_server(row[3])
        if not current_version or not current_label:
            continue
        if not pak_name or not aes_key or aes_key.upper() == "TBD":
            continue
        if not os_name or not servers:
            continue
        for server in servers:
            entries.append(
                AesKeyEntry(
                    version_label=current_label,
                    version_number=current_version,
                    pak_name=pak_name,
                    aes_key=aes_key,
                    os=os_name,
                    server=server,
                )
            )
    return entries


def list_versions(entries: Iterable[AesKeyEntry]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in entries:
        if entry.version_label in seen:
            continue
        seen.add(entry.version_label)
        ordered.append(entry.version_label)
    return ordered


def _pakchunk_id(pak_name: str) -> int | None:
    match = _PAKCHUNK_RE.search(pak_name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def select_keys(
    entries: Iterable[AesKeyEntry],
    version: str,
    os_name: str,
    server: str,
    max_chunks: int = 10000,
) -> AesSelection:
    # First, filter by OS and server
    all_entries = [e for e in entries if e.os == os_name and e.server == server]
    
    # If no version specified, find the latest version
    if not version:
        # Get unique versions in order of appearance (newest last in MD file)
        versions_seen: list[str] = []
        for e in all_entries:
            if e.version_label not in versions_seen:
                versions_seen.append(e.version_label)
        # Pick the last one (newest) that's NOT a beta
        latest = None
        for v in reversed(versions_seen):
            if "(Beta)" not in v and "(UNKNOWN)" not in v:
                latest = v
                break
        # If all are beta, just use the last one
        if not latest and versions_seen:
            latest = versions_seen[-1]
        version = latest or ""
    
    # Filter by version
    filtered: list[AesKeyEntry] = []
    for entry in all_entries:
        if version in (entry.version_label, entry.version_number):
            filtered.append(entry)
        elif entry.version_label.startswith(version):
            filtered.append(entry)

    main_entries = [e for e in filtered if e.pak_name.lower() == "main"]
    chunk_entries = [e for e in filtered if _pakchunk_id(e.pak_name) is not None]
    
    # Sort them normally just in case, but keep all by default using a high limit
    chunk_entries.sort(key=lambda e: _pakchunk_id(e.pak_name) or -1)

    selected: list[AesKeyEntry] = []
    if main_entries:
        selected.append(main_entries[0])
    selected.extend(chunk_entries[:max_chunks])

    label = main_entries[0].version_label if main_entries else (filtered[0].version_label if filtered else version)
    return AesSelection(version_label=label, os=os_name, server=server, keys=selected)

