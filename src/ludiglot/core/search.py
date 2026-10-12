from __future__ import annotations

from difflib import SequenceMatcher
from typing import Iterable, Tuple

try:
    from rapidfuzz import fuzz, process
except Exception:  # pragma: no cover
    fuzz = None
    process = None


class FuzzySearcher:
    """占位：基于 RapidFuzz 的模糊搜索。"""

    def search(self, query: str, candidates: Iterable[str]) -> Tuple[str, float]:
        if process is not None and fuzz is not None:
            hit = process.extractOne(query, candidates, scorer=fuzz.ratio)
            if hit is None:
                return "", 0.0
            best_item, score, _ = hit
            return str(best_item), float(score) / 100.0

        best_item = ""
        best_score = 0.0
        for item in candidates:
            score = SequenceMatcher(None, query, item).ratio()
            if score > best_score:
                best_item = item
                best_score = score
        return best_item, best_score
