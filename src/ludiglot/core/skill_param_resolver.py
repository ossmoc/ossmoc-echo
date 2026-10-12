from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Callable

_PRINTABLE_RUN_RE = re.compile(rb"[\x20-\x7E]{2,}")
_SKILL_ID_RE = re.compile(r"^Skill_(\d+)_")
_SKILL_KEY_RE = re.compile(r"^Skill_\d+_[A-Za-z0-9_.]+$")
_BRANCH_SUFFIX_RE = re.compile(r"_branch_[0-9.]+$")
_NUMERIC_TOKEN_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?(?:%|[dhms])?(?:\+\d+(?:\.\d+)?%?)?)$",
    re.IGNORECASE,
)


class SkillParamResolver:
    """Resolve indexed placeholder values for skill texts from db_skill.db."""

    def __init__(
        self,
        db_path: Path | None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path).resolve() if db_path else None
        self._logger = logger
        self._conn: sqlite3.Connection | None = None
        self._runs_cache: dict[int, list[str]] = {}
        self._value_cache: dict[tuple[str, int], list[str]] = {}

    @property
    def available(self) -> bool:
        return bool(self.db_path and self.db_path.exists())

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _log(self, message: str) -> None:
        if self._logger:
            self._logger(message)

    def _ensure_connection(self) -> sqlite3.Connection | None:
        if not self.available:
            return None
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            except Exception:
                self._conn = None
        return self._conn

    def _extract_skill_id(self, text_key: str) -> int | None:
        m = _SKILL_ID_RE.match(text_key)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _normalize_text_key(self, text_key: str) -> str:
        return _BRANCH_SUFFIX_RE.sub("", text_key)

    def _build_key_variants(self, text_key: str) -> set[str]:
        base = self._normalize_text_key(text_key)
        variants: set[str] = {text_key, base}

        m = re.match(r"^(Skill_\d+_)", base)
        if not m:
            return variants
        prefix = m.group(1)
        for suffix in (
            "SkillDescribe",
            "SkillResume",
            "MultiSkillDescribe",
            "MultiSkillDescribe_1.1",
            "SkillDescribe_1.1",
            "SkillResume_1.1",
        ):
            variants.add(prefix + suffix)
        return variants

    def _is_numeric_token(self, token: str) -> bool:
        t = token.strip()
        if not _NUMERIC_TOKEN_RE.fullmatch(t):
            return False
        # Filter out obvious IDs in key names.
        if t.isdigit() and len(t) >= 6:
            return False
        return True

    def _collect_numeric_block(self, runs: list[str], key_idx: int) -> list[str]:
        values: list[str] = []
        idx = key_idx - 1
        while idx >= 0:
            token = runs[idx].strip()
            if self._is_numeric_token(token):
                values.append(token)
                idx -= 1
                continue
            break
        values.reverse()
        return values

    def _trim_values(self, values: list[str], placeholder_count: int) -> list[str]:
        out = list(values)
        if not out:
            return out

        max_pattern = min(len(out) // 2, 8)
        for size in range(1, max_pattern + 1):
            chunk = out[:size]
            pos = size
            repeat = 1
            while pos + size <= len(out) and out[pos: pos + size] == chunk:
                repeat += 1
                pos += size
            if repeat >= 2:
                out = chunk + out[pos:]
                break

        if placeholder_count > 0 and len(out) > placeholder_count:
            out = out[:placeholder_count]
        return out

    def _load_skill_runs(self, skill_id: int) -> list[str]:
        cached = self._runs_cache.get(skill_id)
        if cached is not None:
            return cached

        conn = self._ensure_connection()
        if conn is None:
            self._runs_cache[skill_id] = []
            return []

        try:
            cur = conn.cursor()
            cur.execute("SELECT BinData FROM skill WHERE Id=?", (skill_id,))
            row = cur.fetchone()
        except Exception:
            self._runs_cache[skill_id] = []
            return []

        if not row or not isinstance(row[0], (bytes, bytearray)):
            self._runs_cache[skill_id] = []
            return []

        blob = row[0]
        runs = [m.group(0).decode("ascii", errors="ignore").strip() for m in _PRINTABLE_RUN_RE.finditer(blob)]
        runs = [r for r in runs if r]
        self._runs_cache[skill_id] = runs
        return runs

    def resolve_values(self, text_key: str | None, placeholder_count: int = 0) -> list[str]:
        key = str(text_key or "").strip()
        if not key:
            return []

        cache_key = (key, max(int(placeholder_count), 0))
        cached = self._value_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        skill_id = self._extract_skill_id(key)
        if skill_id is None:
            self._value_cache[cache_key] = []
            return []

        runs = self._load_skill_runs(skill_id)
        if not runs:
            self._value_cache[cache_key] = []
            return []

        key_variants = self._build_key_variants(key)
        candidates: list[tuple[int, int, list[str], str]] = []

        for idx, run in enumerate(runs):
            if run in key_variants:
                block = self._collect_numeric_block(runs, idx)
                if block:
                    pri = 0 if run == key else 1
                    candidates.append((pri, idx, block, run))

        # Fallback: any key in the same skill row that has nearby numeric tokens.
        if not candidates:
            for idx, run in enumerate(runs):
                if not _SKILL_KEY_RE.fullmatch(run):
                    continue
                block = self._collect_numeric_block(runs, idx)
                if block:
                    candidates.append((2, idx, block, run))

        if not candidates:
            self._value_cache[cache_key] = []
            return []

        candidates.sort(key=lambda item: (item[0], item[1]))
        _, _, values, source_key = candidates[0]
        trimmed = self._trim_values(values, placeholder_count)
        self._value_cache[cache_key] = list(trimmed)

        if trimmed:
            self._log(
                f"[PARAM] 技能参数命中: text_key={key}, source={source_key}, values={trimmed}"
            )

        return list(trimmed)
