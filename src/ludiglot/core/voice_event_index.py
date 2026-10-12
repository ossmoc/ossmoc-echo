from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

try:
    from rapidfuzz import fuzz  # type: ignore
except Exception:  # pragma: no cover
    fuzz = None


_CAMEL_SPLIT = re.compile(r"([a-z0-9])([A-Z])")
_NON_WORD = re.compile(r"[^a-zA-Z0-9_]+")
_MULTI_UNDERSCORE = re.compile(r"_+")


def _normalize_name(name: str) -> str:
    raw = name.strip()
    if not raw:
        return ""
    raw = raw.replace("/", "_").replace("\\", "_").replace(".", "_")
    raw = _CAMEL_SPLIT.sub(r"\1_\2", raw)
    raw = _NON_WORD.sub("_", raw)
    raw = raw.lower()
    raw = _MULTI_UNDERSCORE.sub("_", raw)
    return raw.strip("_")


def _compact_name(normalized: str) -> str:
    return normalized.replace("_", "")


def _tokenize(normalized: str) -> List[str]:
    tokens = [t for t in normalized.split("_") if t]
    # 过滤掉极常见的噪声 token
    noise = {"play", "vo", "v", "p", "voice", "audio", "event"}
    return [t for t in tokens if t not in noise]


def _latest_mtime(paths: Iterable[Path]) -> float:
    latest = 0.0
    for path in paths:
        try:
            latest = max(latest, path.stat().st_mtime)
        except Exception:
            continue
    return latest


@dataclass
class EventIndexCache:
    mtime: float
    names: List[str]


class VoiceEventIndex:
    """从 BNK/TXTP 等资源遍历构建事件名索引，用于提升语音命名鲁棒性。"""

    def __init__(
        self,
        bnk_root: Path | None,
        txtp_root: Path | None = None,
        cache_path: Path | None = None,
        extra_names: Iterable[str] | None = None,
    ) -> None:
        self.bnk_root = bnk_root
        self.txtp_root = txtp_root
        self.cache_path = cache_path
        self.extra_names = list(extra_names or [])
        self.names: List[str] = []
        self._normalized: List[str] = []
        self._compact: List[str] = []
        self._token_map: Dict[str, Set[int]] = {}

    def load_or_build(self) -> None:
        latest = self._resource_latest_mtime()
        cache = self._load_cache(latest)

        if cache is None:
            self.names, latest = self._build_names(latest)
            self._write_cache(latest)
        else:
            self.names = cache.names

        self._build_token_index()

    def _resource_latest_mtime(self) -> float:
        roots = [p for p in [self.bnk_root, self.txtp_root] if p]
        if not roots:
            return 0.0
        return _latest_mtime(self._iter_files(roots))

    def _load_cache(self, latest: float) -> EventIndexCache | None:
        if not self.cache_path or not self.cache_path.exists():
            return None
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or float(raw.get("mtime", -1)) < latest:
                return None
            names = raw.get("names")
            if not isinstance(names, list):
                return None
            return EventIndexCache(mtime=float(raw.get("mtime")), names=[str(n) for n in names])
        except Exception:
            return None

    def _write_cache(self, latest: float) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps({"mtime": latest, "names": self.names}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _iter_files(self, roots: Iterable[Path]) -> Iterable[Path]:
        for root in roots:
            if not root or not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    yield path

    def _build_names(self, latest: float) -> Tuple[List[str], float]:
        names: Set[str] = set()
        latest_mtime = latest

        def collect(root: Path | None, suffix: str) -> None:
            nonlocal latest_mtime
            if not root or not root.exists():
                return
            for path in root.rglob(f"*{suffix}"):
                if not path.is_file():
                    continue
                names.add(path.stem)
                try:
                    latest_mtime = max(latest_mtime, path.stat().st_mtime)
                except Exception:
                    continue

        collect(self.bnk_root, ".bnk")
        collect(self.txtp_root, ".txtp")

        for item in self.extra_names:
            if isinstance(item, str) and item.strip():
                names.add(item.strip())

        return sorted(names), latest_mtime

    def _build_token_index(self) -> None:
        self._normalized = []
        self._compact = []
        self._token_map = {}
        for idx, name in enumerate(self.names):
            norm = _normalize_name(name)
            comp = _compact_name(norm)
            self._normalized.append(norm)
            self._compact.append(comp)
            for token in _tokenize(norm):
                self._token_map.setdefault(token, set()).add(idx)

    def _candidate_indices(self, tokens: List[str]) -> Set[int]:
        if not tokens:
            return set(range(len(self.names)))
        buckets = [self._token_map.get(t, set()) for t in tokens]
        if not buckets:
            return set()
        # 优先交集，若为空再取并集
        intersection = set.intersection(*buckets) if buckets else set()
        if intersection:
            return intersection
        union: Set[int] = set()
        for b in buckets:
            union.update(b)
        return union

    def _score(self, seed_norm: str, seed_comp: str, cand_norm: str, cand_comp: str) -> float:
        if seed_norm == cand_norm:
            return 1.0
        if seed_comp == cand_comp and seed_comp:
            return 0.98
        if seed_norm in cand_norm or cand_norm in seed_norm:
            base = 0.9
        else:
            base = 0.0

        if fuzz is not None:
            ratio = float(fuzz.token_set_ratio(seed_norm, cand_norm)) / 100.0
        else:
            ratio = SequenceMatcher(None, seed_norm, cand_norm).ratio()
        return max(base, ratio)

    def find_candidates(
        self,
        text_key: str | None,
        voice_event: str | None,
        limit: int = 8,
        min_score: float = 0.65,
    ) -> List[str]:
        if not self.names:
            return []
        seeds = []
        for item in (voice_event, text_key):
            if isinstance(item, str) and item.strip():
                seeds.append(item.strip())

        if not seeds:
            return []

        scored: Dict[int, float] = {}
        for seed in seeds:
            seed_norm = _normalize_name(seed)
            if not seed_norm:
                continue
            seed_comp = _compact_name(seed_norm)
            tokens = _tokenize(seed_norm)
            candidate_indices = self._candidate_indices(tokens)
            if not candidate_indices:
                candidate_indices = set(range(len(self.names)))
            for idx in candidate_indices:
                cand_norm = self._normalized[idx]
                cand_comp = self._compact[idx]
                score = self._score(seed_norm, seed_comp, cand_norm, cand_comp)
                if score >= min_score:
                    prev = scored.get(idx, 0.0)
                    if score > prev:
                        scored[idx] = score

        if not scored:
            return []
        ordered = sorted(scored.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [self.names[idx] for idx, _ in ordered]
