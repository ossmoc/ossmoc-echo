from __future__ import annotations
import re
import time
from typing import Any, Dict, List, Tuple, Optional
from ludiglot.core.search import FuzzySearcher
from ludiglot.core.indexed_search import IndexedSearchEngine
from ludiglot.core.text_builder import normalize_en
from ludiglot.core.smart_match import build_smart_candidates, strip_speaker_prefix
from ludiglot.core.voice_event_index import VoiceEventIndex

try:
    from rapidfuzz import fuzz, process
except Exception:
    fuzz = None
    process = None

class MatchResult:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    @property
    def matched_key(self) -> str:
        return self.data.get("_matched_key", "")

    @property
    def score(self) -> float:
        return self.data.get("_score", 0.0)

    def to_dict(self) -> Dict[str, Any]:
        return self.data


class TextMatcher:
    """Core logic for matching, extracted from OverlayWindow."""

    def __init__(
        self,
        db: Dict[str, Any],
        voice_map: Dict[str, Any] | None = None,
        voice_event_index: VoiceEventIndex | None = None,
        gender_preference: str = "female",
    ):
        self.db = db
        self.voice_map = voice_map or {}
        self.voice_event_index = voice_event_index
        self.searcher = FuzzySearcher()
        pref = str(gender_preference or "female").strip().lower()
        self.gender_preference = pref if pref in {"female", "male"} else "female"
        
        # 先初始化 log_callback
        self.log_callback = None
        
        # 别名映射
        self.alias_map = {
            "hp": "mainhp",
            "atk": "mainatk",
            "def": "maindef",
            "energyregen": "mainenergyregen",
            "critrate": "maincritrate",
            "critdmg": "maincritdmg",
            "critdamage": "maincritdmg",
            "critdmgbonus": "maincritdmg",
        }
        self._title_translation_cache: dict[str, str] = {}
        
        # 然后初始化索引化搜索引擎（可能调用 log）
        db_keys = list(db.keys())
        self.indexed_searcher = IndexedSearchEngine(db_keys)
        self.log(f"[MATCHER] 索引引擎已初始化 ({len(db_keys)} keys)")

    def set_logger(self, callback):
        self.log_callback = callback

    def log(self, msg: str):
        if self.log_callback:
            self.log_callback(msg)

    def _prioritize_protagonist_gender(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """只在主角男女主并存时重排 matches[0]，避免误伤普通语音。"""
        matches = result.get("matches")
        if not isinstance(matches, list) or len(matches) < 2:
            return result

        female_tokens = ("nvzhu", "roverf", "_female")
        male_tokens = ("nanzhu", "roverm", "_male")
        target_tokens = female_tokens if self.gender_preference == "female" else male_tokens
        other_tokens = male_tokens if self.gender_preference == "female" else female_tokens
        protagonist_tokens = female_tokens + male_tokens

        scored: list[tuple[int, int, Any]] = []
        has_target = False
        has_other = False

        for idx, item in enumerate(matches):
            if not isinstance(item, dict):
                scored.append((1, idx, item))
                continue
            hay = f"{item.get('audio_event', '')} {item.get('text_key', '')}".lower()
            has_protagonist = any(tok in hay for tok in protagonist_tokens)
            hit_target = any(tok in hay for tok in target_tokens)
            hit_other = any(tok in hay for tok in other_tokens)
            has_target = has_target or hit_target
            has_other = has_other or hit_other

            if has_protagonist:
                if hit_target and not hit_other:
                    pri = 0
                elif hit_other and not hit_target:
                    pri = 2
                else:
                    pri = 1
            else:
                pri = 1
            scored.append((pri, idx, item))

        # 仅在主角男女两套并存时触发重排
        if not (has_target and has_other):
            result["matches"] = [m.copy() if isinstance(m, dict) else m for m in matches]
            return result

        scored.sort(key=lambda x: (x[0], x[1]))
        reordered = [item for _, _, item in scored]
        result["matches"] = reordered
        top = reordered[0] if reordered else {}
        if isinstance(top, dict):
            self.log(
                f"[MATCH] 主角性别优先: pref={self.gender_preference}, "
                f"text_key={top.get('text_key')}, event={top.get('audio_event')}"
            )
        return result

    def _build_result(self, matched_key: str) -> Dict[str, Any]:
        result = dict(self.db.get(matched_key, {}))
        result["_matched_key"] = matched_key
        return self._prioritize_protagonist_gender(result)

    def _has_time_expression(self, text: str) -> bool:
        if not text:
            return False
        return bool(re.search(r"\b\d+\s*[dhms](?:\s+\d+\s*[dhms])*\b", text, flags=re.IGNORECASE))

    def resolve_title_cn(self, title: str) -> str:
        """解析标题（如角色名）对应的中文显示，供 UI 直接渲染。"""
        cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(title or "")).strip()
        key = normalize_en(cleaned)
        if not key:
            return ""

        if key in self._title_translation_cache:
            return self._title_translation_cache[key]

        result, score = self.search_key(key)
        if not isinstance(result, dict):
            self._title_translation_cache[key] = ""
            return ""

        matches = result.get("matches", [])
        best_cn = ""
        best_rank = -10_000.0

        if isinstance(matches, list):
            for item in matches:
                if not isinstance(item, dict):
                    continue
                cn = str(item.get("official_cn") or item.get("cn") or "").strip()
                en = str(item.get("official_en") or "").strip()
                text_key = str(item.get("text_key") or "")
                if not cn or not en:
                    continue

                en_clean = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in en).strip()
                en_key = normalize_en(en_clean)
                if en_key != key:
                    continue

                tk = text_key.lower()
                en_l = en.lower()
                rank = 0.0

                # 优先 Name/RoleInfo/Speaker 条目，压低 Main 对白条目
                if tk.endswith("_name") or "_name_" in tk or "roleinfo_" in tk or "speaker_" in tk:
                    rank += 6.0
                if "main_" in tk:
                    rank -= 5.0
                if "_content" in tk or "_desc" in tk:
                    rank -= 2.0

                # 偏好干净人名，规避 LYNAE— / Lynae? / Lynae! 这类语气变体
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9' -]{0,40}", en):
                    rank += 3.0
                if any(ch in en for ch in ("—", "…", "?", "!", ":", ",", ".", "(", ")", "{", "}")):
                    rank -= 3.0
                if "{message}" in en_l:
                    rank -= 2.0

                # 中文人名通常较短且不包含拉长符
                if len(cn) <= 6:
                    rank += 1.0
                if "——" in cn or "…" in cn:
                    rank -= 2.0

                if rank > best_rank:
                    best_rank = rank
                    best_cn = cn

        if best_cn:
            self._title_translation_cache[key] = best_cn
            return best_cn

        # 回退：无可判别候选时沿用主匹配结果
        cn = result.get("official_cn") or result.get("cn")
        if not cn and isinstance(matches, list) and matches:
            first = matches[0]
            if isinstance(first, dict):
                cn = first.get("official_cn") or first.get("cn")
        if cn and score >= 0.7:
            value = str(cn)
            self._title_translation_cache[key] = value
            return value

        self._title_translation_cache[key] = ""
        return ""

    def _prioritize_exact_text_match(self, result: Dict[str, Any], ocr_text: str) -> Dict[str, Any]:
        """对于同键多条目碰撞（如同为 'thatsthespirit'），基于原文本标点或保留字符做次级重排。"""
        if not isinstance(result, dict) or result.get("_multi"):
            return result
            
        matches = result.get("matches")
        if not isinstance(matches, list) or len(matches) < 2:
            return result
            
        import difflib
        import re
        
        ocr_norm = ocr_text.strip().lower()
        if not ocr_norm:
            return result

        scored: list[tuple[float, int, Any]] = []
        for idx, item in enumerate(matches):
            if not isinstance(item, dict):
                scored.append((-1.0, idx, item))
                continue
            
            official_en = str(item.get("official_en") or "")
            # 移除 {PlayerName} 这类的变量，避免影响比对
            clean_en = re.sub(r"\{.*?\}", "", official_en).strip().lower()
            
            ratio = difflib.SequenceMatcher(None, ocr_norm, clean_en).ratio()

            # 基于 text_key 的类型给予微小优先级：优先选中常规叙事/对话节点，规避广播、活动小游戏等特殊节点
            tk = str(item.get("text_key") or "").upper()
            if tk.startswith("MAIN_"):
                ratio += 0.003
            elif tk.startswith("GNNPC_") or tk.startswith("POI_"):
                ratio += 0.002
            elif tk.startswith("ROLE_") or tk.startswith("SQ_"):
                ratio += 0.001
            else:
                # 其他冷门前缀（如 WFLKDJS 等活动/系统机制前缀），优先级垫底
                ratio -= 0.001

            scored.append((ratio, -idx, item)) # -idx 为稳定排序
            
        # 降序排序，分数高的在前面
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        result["matches"] = [item for _, _, item in scored]
        return result

    def _prioritize_time_context(self, result: Dict[str, Any], ocr_text: str) -> Dict[str, Any]:
        """当 OCR 明显是时间文本时，优先选择带 Time/时间 语义的同键候选。"""
        if not isinstance(result, dict) or result.get("_multi"):
            return result
        if not self._has_time_expression(ocr_text):
            return result

        matches = result.get("matches")
        if not isinstance(matches, list) or len(matches) < 2:
            return result

        scored: list[tuple[float, int, Any]] = []
        for idx, item in enumerate(matches):
            if not isinstance(item, dict):
                scored.append((-1.0, idx, item))
                continue
            text_key = str(item.get("text_key") or "")
            en = str(item.get("official_en") or "")
            cn = str(item.get("official_cn") or "")

            tk = text_key.lower()
            en_l = en.lower()
            cn_l = cn.lower()
            s = 0.0

            if "time" in tk:
                s += 2.2
            if "time" in en_l:
                s += 2.0
            if "remaining time" in en_l:
                s += 1.2
            if "时间" in cn:
                s += 2.0
            if "剩余时间" in cn:
                s += 1.2
            if "{0}" in en or "{0}" in cn:
                s += 0.3
            if re.search(r"\bday\(s\)|\bhour|\bminute|\bsecond", en_l):
                s += 0.4

            scored.append((s, idx, item))

        best_score = max((x[0] for x in scored), default=-1.0)
        if best_score <= 0:
            return result

        scored.sort(key=lambda x: (-x[0], x[1]))
        reordered = [item for _, _, item in scored]
        result["matches"] = reordered
        top = reordered[0] if reordered else {}
        if isinstance(top, dict):
            self.log(
                f"[MATCH] 时间语义优先: text_key={top.get('text_key')}, "
                f"official_en={str(top.get('official_en') or '')[:48]}"
            )
        return result

    def match(self, lines: List[Tuple[str, float]]) -> Dict[str, Any] | None:
        """Main entry point: find best DB entry for OCR lines."""
        start = time.time()
        result = self._lookup_best(lines)
        elapsed = time.time() - start

        # 兜底补齐 OCR 上下文，避免某些早退路径缺失 _ocr_text/_query_key
        if isinstance(result, dict):
            ocr_text = " ".join(str(text).strip() for text, _ in lines if str(text).strip())
            if ocr_text:
                result.setdefault("_ocr_text", ocr_text)
                result.setdefault("_query_key", normalize_en(ocr_text))
                result["_ocr_context"] = ocr_text
                result = self._prioritize_exact_text_match(result, ocr_text)
                result = self._prioritize_time_context(result, ocr_text)
        
        # 性能监控日志
        if elapsed > 1.0:
            self.log(f"[PERF] match() 耗时较长: {elapsed:.2f}s")
        
        # 缓存统计
        cache_stats = self.indexed_searcher.get_cache_stats()
        hit_rate = cache_stats['hits'] / max(cache_stats['hits'] + cache_stats['misses'], 1) * 100
        if cache_stats['hits'] + cache_stats['misses'] > 100:
            self.log(f"[CACHE] 命中率: {hit_rate:.1f}% (hits={cache_stats['hits']}, misses={cache_stats['misses']})")
        
        return result

    def _clean_ocr_line(self, text: str) -> str:
        text = re.sub(r"(?i)&lt;\s*/?\s*br\s*/?&gt;", " ", str(text or ""))
        text = re.sub(r"(?i)<\s*/?\s*br\s*/?>?", " ", text)
        # 去掉图标/分隔符噪声，保留字母数字与空格
        cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
        cleaned = " ".join(cleaned.split())
        return cleaned.strip()

    def _has_voice_match(self, result: dict) -> bool:
        """检查匹配结果是否有对应的语音文件。"""
        if not isinstance(result, dict):
            return False
        matches = result.get('matches', [])
        if not matches:
            return False
        text_key = matches[0].get('text_key', '')
        if not text_key:
            return False
        if self.voice_map and self.voice_map.get(text_key):
            return True
        if self.voice_event_index:
            event_name = f"vo_{text_key}"
            events = self.voice_event_index.find_candidates(text_key=text_key, voice_event=event_name, limit=1, min_score=0.95)
            if events:
                return True
        return False
    
    def _is_list_mode(self, lines: list[tuple[str, float]]) -> bool:
        if len(lines) < 4:
            return False
        cleaned = [self._clean_ocr_line(text) for text, _ in lines if text]
        cleaned = [c for c in cleaned if c]
        filtered = []
        for c in cleaned:
            if not c: continue
            if len(c.split()) > 3 or len(c) > 20: continue
            digit_ratio = sum(ch.isdigit() for ch in c) / max(len(c), 1)
            if digit_ratio > 0.4: continue
            filtered.append(c)
        if len(filtered) < 3: return False
        lengths = [len(c) for c in filtered]
        max_len = max(lengths)
        avg_words = sum(len(c.split()) for c in filtered) / max(len(filtered), 1)
        return max_len <= 16 and avg_words <= 2.2

    def _is_skill_meta_key(self, text_key: str | None) -> bool:
        if not text_key:
            return False
        tk = str(text_key).lower()
        return any(
            t in tk
            for t in (
                "attributename",
                "skillname",
                "descriptiontitle",
                "icontxt",
                "icontext",
                "skillpage_name",
            )
        )

    def _is_skill_body_key(self, text_key: str | None) -> bool:
        if not text_key:
            return False
        tk = str(text_key).lower()
        return any(
            t in tk
            for t in (
                "skilldescribe",
                "skillresume",
                "descriptioncontent",
                "attributesdescription",
                "_content",
                "_describe",
            )
        )

    def _is_title_text_key(self, text_key: str | None) -> bool:
        if not text_key:
            return False
        tk = str(text_key).lower()
        return (
            tk.endswith("_title")
            or tk.endswith("_title_text")
            or "_title_" in tk
            or "descriptiontitle" in tk
        )

    def _extract_first_line_title_hint(
        self,
        raw_lines: list[tuple[str, float]],
        line_info: list[dict],
    ) -> str:
        """提取第一行标题（若存在），用于单条目结果补充标题展示。"""
        if not raw_lines or not line_info:
            return ""
        first_raw = str(raw_lines[0][0] or "").strip()
        if not first_raw:
            return ""
        first_line = next((l for l in line_info if l.get("idx") == 0), None)
        if not first_line:
            return ""
        if float(first_line.get("score") or 0.0) < 0.88:
            return ""
        text_key = first_line.get("text_key") or ""
        if not self._is_title_text_key(text_key):
            return ""

        official = str(first_line.get("official_en") or "").strip()
        cleaned = str(first_line.get("cleaned") or "").strip()
        title = official or first_raw or cleaned
        if len(title) > 96 or len(title.split()) > 12:
            return ""
        return title

    def _looks_title_like_line(self, raw_text: str, cleaned: str) -> bool:
        """判断 OCR 单行是否更像标题/说话人名，而不是正文句子。"""
        c = str(cleaned or "").strip()
        if not c:
            return False
        words = [w for w in c.split() if w]
        wc = len(words)
        if wc == 0 or wc > 5:
            return False
        if len(c) > 36:
            return False

        raw = str(raw_text or "")

        # 缩写人名（如 N.A.N.A.）：经 _clean_ocr_line 后点号变为空格，cleaned 为 "N A N A"
        # 识别：所有字母均大写、无其他标点（冒号除外）
        abbr_only = c.replace(' ', '')
        if (abbr_only.isupper() and abbr_only.isalpha() and len(abbr_only) <= 10
                and not any(p in raw for p in (',', '!', '?', ';'))):
            return True

        if any(p in raw for p in (",", ".", "!", "?", ";", ":")):
            return False

        digit_ratio = sum(ch.isdigit() for ch in c) / max(len(c), 1)
        if digit_ratio > 0.30:
            return False

        alpha_words = [w for w in words if any(ch.isalpha() for ch in w)]
        if alpha_words:
            cap_like = 0
            for w in alpha_words:
                first = w[0]
                if first.isupper() or w.isupper():
                    cap_like += 1
            if cap_like / max(len(alpha_words), 1) >= 0.66:
                return True

        # 回退兼容旧逻辑：很短的 1-3 词条目依旧可判为标题
        return wc <= 3 and len(c) <= 20

    def _attach_title_hint(self, result: Dict[str, Any] | None, title_hint: str) -> Dict[str, Any] | None:
        if not isinstance(result, dict) or not title_hint:
            return result
        if result.get("_multi") or result.get("_first_line"):
            return result
        matches = result.get("matches") or []
        first = matches[0] if isinstance(matches, list) and matches else {}
        text_key = first.get("text_key") if isinstance(first, dict) else ""
        # 结果本身就是标题条目时不重复注入
        if self._is_title_text_key(text_key):
            return result
        result["_first_line"] = title_hint
        return result

    def _build_mixed_content_candidate(self, line_info: list[dict]) -> tuple[Dict[str, Any], float] | None:
        """基于首行标题 + 其余正文构建混合内容候选。"""
        if len(line_info) < 2:
            return None

        first_line = line_info[0]
        if not first_line.get("is_title_like"):
            return None

        rest_lines = line_info[1:]
        rest_text = " ".join(str(l.get("cleaned") or "") for l in rest_lines).strip()
        if not rest_text:
            return None

        rest_key = normalize_en(rest_text)
        if not rest_key:
            return None

        rest_word_count = len(rest_text.split())
        if rest_word_count < 3:
            return None

        rest_result, rest_score = self.search_key(rest_key)
        if rest_score < 0.5:
            return None

        matched_key = str(rest_result.get("_matched_key", ""))
        is_good_match = len(matched_key) >= len(rest_key) * 0.6
        if not is_good_match:
            return None

        rest_has_voice = self._has_voice_match(rest_result)
        first_has_voice = self._has_voice_match(first_line.get("result") or {})
        first_score = float(first_line.get("score") or 0.0)
        is_title_plus_body = rest_word_count >= 8 and len(rest_key) >= 40

        should_use = (
            is_title_plus_body
            or len(rest_key) > 100
            or rest_has_voice
            or (not first_has_voice and rest_score > first_score)
            or first_score < 0.75
        )
        if not should_use:
            return None

        rest_result["_score"] = round(rest_score, 3)
        rest_result["_query_key"] = rest_key
        rest_result["_ocr_text"] = rest_text
        rest_result["_first_line"] = str(first_line.get("cleaned") or "").strip()
        return rest_result, rest_score

    def _extract_anchor_tokens(self, text: str) -> list[str]:
        """提取长文本中的区分性锚词，用于抑制“同模板技能”误匹配。"""
        words = re.findall(r"[A-Za-z][A-Za-z0-9']+", text.lower())
        if not words:
            return []
        stop = {
            "the", "and", "with", "when", "after", "before", "while", "within", "without",
            "into", "from", "that", "this", "your", "their", "there", "where", "which",
            "press", "cast", "skill", "resonance", "basic", "attack", "normal", "stage",
            "points", "point", "cost", "rate", "dealing", "damage", "dmg", "considered",
            "switch", "form", "ground", "period", "certain", "close", "less", "than",
            "no", "more", "can", "be", "to", "of", "in", "is", "at", "on", "for",
            "fusion", "liberation",
        }
        tokens = []
        for w in words:
            w = w.strip("'")
            if len(w) < 6:
                continue
            if w in stop:
                continue
            if w not in tokens:
                tokens.append(w)
        # 只取前几个锚词，避免噪声过多
        return tokens[:8]

    def _anchor_rescue_search(self, context_text: str) -> Dict[str, Any] | None:
        """长段落低分时的兜底检索：按锚词一致性回捞正文条目。"""
        query_key = normalize_en(context_text)
        if len(query_key) < 120:
            return None
        anchors = self._extract_anchor_tokens(context_text)
        if len(anchors) < 2:
            return None

        head = normalize_en(" ".join(context_text.split()[:8]))
        required_hits = max(2, min(4, len(anchors)))
        best_key = ""
        best_rank = -1.0
        best_hits = 0

        for db_key in self.db.keys():
            if len(db_key) < int(len(query_key) * 0.75):
                continue
            hit = sum(1 for tok in anchors if tok in db_key)
            if hit < required_hits:
                continue

            # 排除明显短元信息条目（避免再次命中 SkillName/AttributeName）
            rec = self.db.get(db_key, {})
            matches = rec.get("matches") if isinstance(rec, dict) else None
            first = matches[0] if isinstance(matches, list) and matches else {}
            text_key = first.get("text_key") if isinstance(first, dict) else ""
            if self._is_skill_meta_key(text_key):
                continue

            stretch = len(db_key) / max(len(query_key), 1)
            rank = (hit / max(len(anchors), 1)) * 2.0
            if head and head in db_key:
                rank += 1.2
            if stretch <= 6:
                rank += 0.6
            elif stretch <= 12:
                rank += 0.2
            else:
                rank -= 0.2

            if rank > best_rank:
                best_rank = rank
                best_key = db_key
                best_hits = hit

        if not best_key:
            return None

        result = self._build_result(best_key)
        rescue_score = min(0.92, 0.55 + 0.07 * best_hits + max(best_rank - 1.0, 0.0) * 0.05)
        result["_score"] = round(rescue_score, 3)
        result["_query_key"] = query_key
        result["_ocr_text"] = context_text
        result["_weighted"] = round(rescue_score, 3)
        self.log(
            f"[MATCH] 长段落锚词救援命中: hits={best_hits}/{len(anchors)}, "
            f"text_key={(result.get('matches') or [{}])[0].get('text_key') if result.get('matches') else ''}, "
            f"score={rescue_score:.3f}"
        )
        return result

    def search_key(self, key: str) -> tuple[Dict[str, Any], float]:
        """
        高性能搜索实现 - 使用索引化搜索引擎
        
        优化策略：
        1. 精确匹配
        2. 前缀匹配
        3. 子串匹配（包含关系）
        4. 模糊搜索（使用长度预筛选）
        """
        # 1. 精确匹配（最快）
        if self.indexed_searcher.exact_match(key):
            result = self._build_result(key)
            return result, 1.0

        key_len = len(key)
        
        # 2. 前缀匹配（针对长查询）
        if key_len >= 10:
            prefix_hits = self.indexed_searcher.prefix_search(key, max_results=1)
            if prefix_hits:
                best_prefix = prefix_hits[0]
                result = self._build_result(best_prefix)
                return result, 0.99
        
        # 3. 子串匹配优化
        if key_len >= 10:
            # 查找包含query的键（OCR包含库项的情况）
            contain_in_ocr = self.indexed_searcher.substring_search(key, direction='in')
            if contain_in_ocr:
                # 选择“最短的包含项”，避免被超长描述条目误吸附
                constrained = [k for k in contain_in_ocr if len(k) <= key_len * 3]
                if not constrained:
                    constrained = [k for k in contain_in_ocr if len(k) <= key_len * 5]
                if constrained:
                    best_k = min(constrained, key=len)
                    # coverage: query 在匹配项中的占比，越高越可信
                    coverage = len(key) / max(len(best_k), 1)
                    stretch = len(best_k) / max(len(key), 1)

                    if coverage >= 0.82:
                        result = self._build_result(best_k)
                        self.log(f"[MATCH] 高覆盖子串匹配：coverage={coverage:.2f}, stretch={stretch:.2f}")
                        return result, 0.95
                    elif coverage >= 0.65:
                        # 中等覆盖度，记录但继续搜索更优匹配
                        self.log(f"[MATCH] 中覆盖子串匹配：coverage={coverage:.2f}, stretch={stretch:.2f}，继续搜索更优匹配")
                    else:
                        self.log(f"[MATCH] 低覆盖子串匹配：coverage={coverage:.2f}, stretch={stretch:.2f}，跳过")
            
            # 查找被query包含的键（库项包含OCR的情况）
            if key_len >= 50:
                contained_keys = self.indexed_searcher.substring_search(key, direction='contains')
                if contained_keys:
                    best_contain = min(contained_keys, key=len)
                    
                    # BUG FIX: Ensure the matched key is substantial enough to represent the query
                    # If direction='contains' means "DB Key is in Query" (which seems to be the case based on logs),
                    # we must ensure we aren't matching a tiny generic word inside a long sentence.
                    if len(best_contain) > key_len * 0.7 or (len(best_contain) >= 20 and len(best_contain) > key_len * 0.4):
                        if len(best_contain) <= key_len * 3: # Keep original safety check
                            result = self._build_result(best_contain)
                            self.log(f"[MATCH] 部分截屏匹配成功：query_len={key_len}, matched_len={len(best_contain)}")
                            return result, 0.98
        
        # 4. 短查询精确匹配（严格相似度）
        if key_len < 20:
            fuzzy_results = self.indexed_searcher.fuzzy_search(key, top_k=1, score_threshold=0.85)
            if fuzzy_results:
                best_item, score = fuzzy_results[0]
                result = self._build_result(best_item)
                self.log(f"[MATCH] 短查询精确匹配：query_len={key_len}, matched_len={len(best_item)}, score={score:.3f}")
                return result, score
        
        # 5. 常规模糊搜索（使用索引加速）
        fuzzy_results = self.indexed_searcher.fuzzy_search(key, top_k=3, score_threshold=0.4)
        if fuzzy_results:
            best_item, score = fuzzy_results[0]
            result = self._build_result(best_item)
            return result, score
        
        # 6. 未找到任何匹配
        return {}, 0.0

    def _lookup_best(self, lines: list[tuple[str, float]]) -> Dict[str, Any] | None:
        best_result: Dict[str, Any] | None = None
        best_score = -1.0
        best_text = ""
        best_conf = 0.0

        context_text = " ".join(self._clean_ocr_line(text) for text, _ in lines if text)
        context_words = [w for w in context_text.split() if w]
        context_len = len(normalize_en(context_text))
        context_anchors = self._extract_anchor_tokens(context_text) if context_len >= 120 else []

        line_info: list[dict] = []
        for idx, (text, conf) in enumerate(lines):
            cleaned = self._clean_ocr_line(text)
            if not cleaned: continue
            key = normalize_en(cleaned)
            if not key: continue
            key = self.alias_map.get(key, key)
            result, score = self.search_key(key)
            matches = result.get("matches", []) if isinstance(result, dict) else []
            first_match = matches[0] if matches else {}
            primary_text_key = first_match.get("text_key") if isinstance(first_match, dict) else ""
            official_en = first_match.get("official_en") if isinstance(first_match, dict) else ""
            
            is_title_like = self._looks_title_like_line(text, cleaned)
            
            line_info.append({
                'idx': idx, 'text': text, 'cleaned': cleaned, 'key': key,
                'conf': conf, 'score': score, 'result': result,
                'is_title_like': is_title_like, 'score_val': score, # cache score
                'text_key': primary_text_key, 'official_en': official_en,
            })

        if not line_info: return None
        title_hint = self._extract_first_line_title_hint(lines, line_info)
        mixed_candidate = self._build_mixed_content_candidate(line_info)

        # 0. 尝试全量文本合并匹配 (针对长句被OCR拆分的情况)
        full_text_key = normalize_en(context_text)
        if full_text_key and len(full_text_key) > 30:
             # Try substring first for safety
             full_res, full_score = self.search_key(full_text_key)
             matched_key = full_res.get('_matched_key', '') if isinstance(full_res, dict) else ''
             full_ratio = len(matched_key) / max(len(full_text_key), 1) if matched_key else 0.0
             # 收紧早退：避免中低分命中导致长技能文本跳错
             if full_score >= 0.82 or (full_score >= 0.72 and full_ratio >= 0.75):
                 if mixed_candidate is not None:
                     mixed_res, mixed_score = mixed_candidate
                     mixed_key = str(mixed_res.get('_matched_key', ''))
                     same_target = bool(matched_key and mixed_key and matched_key == mixed_key)
                     if same_target or mixed_score >= full_score - 0.06:
                         self.log(
                             f"[MATCH] 完整文本块命中后采用混合内容: "
                             f"full={full_score:.3f}, mixed={mixed_score:.3f}, same_target={same_target}"
                         )
                         return mixed_res
                 self.log(f"[MATCH] 完整文本块匹配成功: score={full_score:.3f}, ratio={full_ratio:.2f}")
                 return self._attach_title_hint(full_res, title_hint)
             if full_score >= 0.5:
                 self.log(f"[MATCH] 完整文本块命中被跳过: score={full_score:.3f}, ratio={full_ratio:.2f}")

        # 0b. 说话者前缀剥离早退：处理 "Name: dialogue" 或分行人名场景
        # 合并原始前 1-2 行尝试匹配（支持 "Luuk\nHerssen: dialogue" 和 "N.A.N.A.: dialogue"）
        if len(lines) >= 1:
            raw_combined = " ".join(
                str(t).strip() for t, _ in lines[:2] if str(t).strip()
            )
            speaker_stripped = strip_speaker_prefix(raw_combined)
            if speaker_stripped:
                speaker_name, dialogue_text = speaker_stripped
                stripped_key = normalize_en(self._clean_ocr_line(dialogue_text))
                if stripped_key and len(stripped_key) >= 15:
                    stripped_res, stripped_score = self.search_key(stripped_key)
                    if stripped_score >= 0.80:
                        self.log(
                            f"[MATCH] 说话者前缀剥离命中: score={stripped_score:.3f}, "
                            f"key_len={len(stripped_key)}"
                        )
                        stripped_res["_query_key"] = stripped_key
                        stripped_res["_ocr_text"] = dialogue_text
                        stripped_res["_score"] = round(stripped_score, 3)
                        stripped_res["_speaker_name"] = speaker_name
                        return self._attach_title_hint(stripped_res, title_hint)

        # --- Multiline Checks (from original code) ---
        multi_items = []
        for line in line_info:
            cleaned = line['cleaned']
            # Time format check
            if re.match(r'^\d+[dhms](\s+\d+[dhms])*$', cleaned.lower().strip()):
                 if multi_items: multi_items[-1]['time_suffix'] = cleaned
                 continue
            if sum(ch.isdigit() for ch in cleaned) / max(len(cleaned), 1) > 0.8: continue

            # Quality check
            matched_key = line['result'].get('_matched_key', '')
            key_len = len(line['key'])
            matched_len = len(matched_key)
            is_extreme_mismatch = key_len >= 15 and matched_len > key_len * 3.0
            
            special_char_count = len(re.findall(r'[^\w\s\-]', cleaned))
            has_special_pollution = (special_char_count / max(len(cleaned), 1)) > 0.15

            is_high_score = line['score'] >= 0.75 and not has_special_pollution
            is_length_match = matched_len >= key_len * 0.5 and matched_len <= key_len * 2.0
            is_long_text = key_len > 50 and line['score'] >= 0.60
            is_short_text_strict = key_len < 15 and line['score'] >= 0.85
            is_good_match = is_high_score or (is_length_match and line['score'] >= 0.55) or is_long_text or is_short_text_strict
            
            if has_special_pollution and line['score'] < 0.85: continue
            if is_extreme_mismatch and line['score'] < 0.98:
                self.log(
                    f"[FILTER] 跳过极端长度失配: {cleaned} "
                    f"(score={line['score']:.3f}, query_len={key_len}, matched_len={matched_len})"
                )
                continue

            if is_good_match:
                multi_items.append(line)
                self.log(f"[FILTER] 保留条目: {cleaned} (score={line['score']:.3f}, len={key_len})")
        
        if len(multi_items) >= 3:
            # 去重：合并匹配到同一个 text_key 的多个 OCR 行
            text_key_map: dict[str, list[dict]] = {}  # text_key -> list of line info
            for line in multi_items:
                matches = line['result'].get("matches")
                match = matches[0] if matches else {}
                text_key = match.get("text_key")
                if not text_key:
                    continue
                if text_key not in text_key_map:
                    text_key_map[text_key] = []
                text_key_map[text_key].append(line)
            
            text_key_best_score: dict[str, float] = {
                tk: max(l['score'] for l in tk_lines) for tk, tk_lines in text_key_map.items()
            }
            high_conf_unique = [tk for tk, s in text_key_best_score.items() if s >= 0.85]

            # 构建去重后的条目列表
            self.log(
                f"[MATCH] 检测到 {len(multi_items)} 个匹配，去重后 {len(text_key_map)} 个独立条目"
                f" (high_conf_unique={len(high_conf_unique)})"
            )

            # 连续段落场景：避免把同一段技能描述拆成“多条目”
            dense_line_count = sum(1 for l in line_info if len(l['cleaned'].split()) >= 4)
            paragraph_like = (
                len(lines) >= 6
                and context_len >= 120
                and dense_line_count >= 4
            )
            cohesive_block_like = (
                len(lines) >= 3
                and context_len >= 80
                and dense_line_count >= max(2, len(lines) - 1)
                and not self._is_list_mode(lines)
            )
            if len(text_key_map) >= 3 and (
                paragraph_like
                or (cohesive_block_like and len(high_conf_unique) <= 2)
            ):
                self.log(
                    f"[MATCH] 连续段落疑似误分流，跳过多条目模式: "
                    f"lines={len(lines)}, context_len={context_len}, dense={dense_line_count}, "
                    f"unique={len(text_key_map)}, high_conf_unique={len(high_conf_unique)}"
                )
            else:
            
                # 如果去重后只剩1-2个条目，说明是同一条长文本被OCR拆分了
                if len(text_key_map) <= 2:
                    # 合并为单条目处理
                    merged_lines = []
                    for text_key, tk_lines in text_key_map.items():
                        # 合并所有OCR文本
                        merged_ocr = " ".join(l['cleaned'] for l in tk_lines)
                        merged_key = normalize_en(merged_ocr)
                        # 使用第一个匹配结果（它们都指向同一个条目）
                        first_line = tk_lines[0]
                        merged_lines.append({
                            'cleaned': merged_ocr,
                            'key': merged_key,
                            'score': max(l['score'] for l in tk_lines),  # 使用最高分
                            'result': first_line['result'],
                            'conf': max(l['conf'] for l in tk_lines),
                        })
                    
                    # 如果只有一个条目，返回单条目结果
                    if len(merged_lines) == 1:
                        line = merged_lines[0]
                        self.log(f"[MATCH] OCR拆分检测：多行匹配同一条目，合并为单条目")
                        result = line['result']
                        result['_score'] = round(line['score'], 3)
                        result['_query_key'] = line['key']
                        result['_ocr_text'] = line['cleaned']
                        return self._attach_title_hint(result, title_hint)
                
                # 构建多条目结果
                items = []
                has_high_confidence_audio = False

                for text_key, tk_lines in text_key_map.items():
                    # 合并同一条目的多个OCR行
                    merged_ocr = " ".join(l['cleaned'] for l in tk_lines)
                    merged_key = normalize_en(merged_ocr)
                    max_score = max(l['score'] for l in tk_lines)

                    first_line = tk_lines[0]
                    matches = first_line['result'].get("matches")
                    match = matches[0] if matches else {}
                    official_en = match.get("official_en") or ""
                    official_cn = match.get("official_cn") or ""
                    
                    # 处理时间后缀
                    time_suffix = first_line.get('time_suffix')
                    if time_suffix:
                        official_en = f"{official_en} {time_suffix}" if official_en and not official_en.rstrip().endswith(':') else f"{official_en}: {time_suffix}" if official_en else ""
                        official_cn = f"{official_cn} {time_suffix}" if official_cn and not official_cn.rstrip().endswith('：') else f"{official_cn}：{time_suffix}" if official_cn else ""
                    
                    # 检测是否有高置信度音频
                    if max_score >= 0.85 and self._has_voice_match(first_line['result']):
                        has_high_confidence_audio = True
                    
                    items.append({
                        "ocr": merged_ocr,
                        "query_key": merged_key,
                        "score": round(max_score, 3),
                        "text_key": text_key,
                        "official_en": official_en,
                        "official_cn": official_cn,
                    })
                
                return {
                    "_multi": True, 
                    "items": items,
                    "_has_audio": has_high_confidence_audio,  # 新增标记
                    "_official_en": " / ".join([i.get("official_en") or i.get("ocr") or "" for i in items if i.get("official_en") or i.get("ocr")]),
                    "_official_cn": " / ".join([i.get("official_cn") or "" for i in items if i.get("official_cn")]),
                    "_query_key": " / ".join([i["query_key"] for i in items if i.get("query_key")]),
                    "_ocr_text": " / ".join([i["ocr"] for i in items if i.get("ocr")]),
                }

        # Mixed Content Check
        if mixed_candidate is not None and len(multi_items) < 3:
            self.log(f"[MATCH] 混合内容策略生效")
            mixed_res, _ = mixed_candidate
            return mixed_res

        # List Mode Check (Original logic copied)
        line_scores = [(l['cleaned'], l['score'], l['result']) for l in line_info]
        if self._is_list_mode(lines) and line_scores:
            strong_lines = [(c, s, r) for c, s, r in line_scores if s >= 0.9] # Simplified check
            if len(strong_lines) >= 3:
                 # Return list
                 items = []
                 for cleaned, score, result in strong_lines:
                     matches = result.get("matches")
                     match = matches[0] if matches else {}
                     items.append({"ocr": cleaned, "query_key": normalize_en(cleaned), "score": round(score, 3), "text_key": match.get("text_key"), "official_cn": match.get("official_cn")})
                 return {"_multi": True, "items": items, "_query_key": "list", "_ocr_text": "list"}

        # Single-line high-confidence fast path:
        # 避免短剧情句在 smart-candidate 的后置过滤阶段被误丢弃。
        if len(line_info) == 1:
            line = line_info[0]
            matched_key = line['result'].get('_matched_key', '')
            key_len = len(line['key'])
            matched_len = len(matched_key)
            if (
                line['score'] >= 0.95
                and key_len >= 12
                and matched_len >= max(10, int(key_len * 0.75))
                and matched_len <= key_len * 2
            ):
                self.log(f"[MATCH] 单行高置信快速命中: score={line['score']:.3f}, len={key_len}")
                result = line['result']
                result['_score'] = round(line['score'], 3)
                result['_query_key'] = line['key']
                result['_ocr_text'] = line['cleaned']
                result['_ocr_conf'] = round(float(line.get('conf', 0.0)), 3)
                result['_weighted'] = round(float(line['score']), 3)
                return self._attach_title_hint(result, title_hint)

        # Smart Candidates
        smart_result = build_smart_candidates(lines)
        candidates = smart_result.get('candidates', [])
        strategy = smart_result.get('strategy', 'unknown')
        self.log(f"[SEARCH] 智能匹配策略={strategy}, 评估 {len(candidates)} 个候选...")

        start_time = time.time()
        
        # 优化：减少候选数量，优先处理高质量候选
        if len(candidates) > 10:
            max_candidates = 15 if context_len >= 120 else 10
        else:
            max_candidates = len(candidates)
        
        successful_sub_matches = []
        seen_text_keys = set()
        
        for idx, (text, conf) in enumerate(candidates[:max_candidates]):
             # 早期退出：如果已经找到高质量匹配，停止搜索
             if best_score > 0.96 and len(best_text.split()) > 5:
                 self.log(f"[SEARCH] 早期退出：已找到高质量匹配 (score={best_score:.3f})")
                 break
             
             key = normalize_en(text)
             if not key: continue

             # Filter short garbage
             # 仅在长上下文中启用，避免误杀“短但完整”的剧情句。
             if context_len >= 40:
                 if len(text.split()) <= 2: continue
                 if len(key) < 12: continue

             result, score = self.search_key(key)
             matched_key = result.get("_matched_key", "")
             matches = result.get("matches", [])
             first_match = matches[0] if matches else {}
             primary_text_key = first_match.get("text_key") if isinstance(first_match, dict) else ""
             anchor_hit = 0
             anchor_ratio = 0.0
             if context_anchors and matched_key:
                 anchor_hit = sum(1 for tok in context_anchors if tok in matched_key)
                 anchor_ratio = anchor_hit / max(len(context_anchors), 1)
             
             word_count = max(len(text.split()), 1)
             length_bonus = min(len(key) / 100.0, 1.0)
             word_bonus = min(word_count / 8.0, 1.0)
             weighted_score = score * (0.6 + 0.2 * length_bonus + 0.2 * word_bonus)
             
             # Penalties
             if matched_key:
                 key_len = len(key)
                 matched_len = len(matched_key)
                 length_diff = abs(key_len - matched_len)
                 length_ratio = matched_len / max(key_len, 1)

                 if key_len > 25 and matched_len < 20:
                     weighted_score *= 0.4 # Relaxed from 0.2
                     self.log(f"[MATCH] 长查询匹配短条目惩罚: score={weighted_score:.3f}")
                 elif length_diff > 15 and length_ratio < 0.6:
                     weighted_score *= 0.6 # Relaxed from 0.4
                 elif key_len > matched_len * 2:
                     weighted_score *= 0.7 # Relaxed from 0.5
                 elif key_len > matched_len * 1.5 and score < 0.97:
                     weighted_score *= 0.85 # Relaxed from 0.75

                 if (
                     strategy == "single"
                     and len(line_info) == 1
                     and context_len >= 25
                     and key_len < context_len
                     and matched_key != key
                     and len(text.split()) <= max(2, len(context_words) // 2)
                 ):
                     candidate_coverage = key_len / max(context_len, 1)
                     matched_coverage = matched_len / max(context_len, 1)
                     if candidate_coverage < 0.65 and matched_coverage < 0.65:
                         weighted_score *= 0.35
                         self.log(f"[MATCH] 单行短片段候选降权: coverage={candidate_coverage:.2f}, matched={matched_coverage:.2f}")

             # 长技能段落：降低“技能短条目”(SkillName/AttributeName)的优先级，提升正文条目
             if context_len >= 120 and primary_text_key:
                 if self._is_skill_meta_key(primary_text_key):
                     weighted_score *= 0.35
                     self.log(f"[MATCH] 技能短条目降权: text_key={primary_text_key}, weighted={weighted_score:.3f}")
                 elif self._is_skill_body_key(primary_text_key):
                     if context_anchors and anchor_ratio < 0.5:
                         weighted_score *= 0.85
                         self.log(f"[MATCH] 技能正文弱锚词降权: text_key={primary_text_key}, weighted={weighted_score:.3f}")
                     else:
                         weighted_score *= 1.10
                         self.log(f"[MATCH] 技能正文加权: text_key={primary_text_key}, weighted={weighted_score:.3f}")

             # 长段落锚词一致性：候选若缺少核心锚词，降权
             if context_anchors and matched_key:
                 is_sub_candidate = len(text) < len(context_text) * 0.7
                 if is_sub_candidate and anchor_hit == 0:
                     if score < 0.85:
                         weighted_score *= 0.8
                     anchor_hit = 1
                     anchor_ratio = 0.6
                 
                 if anchor_hit == 0:
                     weighted_score *= 0.20
                     self.log(f"[MATCH] 锚词缺失降权: hits=0/{len(context_anchors)}, weighted={weighted_score:.3f}")
                 elif anchor_ratio < 0.35:
                     weighted_score *= 0.45
                     self.log(f"[MATCH] 锚词弱匹配降权: hits={anchor_hit}/{len(context_anchors)}, weighted={weighted_score:.3f}")
                 elif anchor_ratio < 0.6:
                     weighted_score *= 0.75
                     self.log(f"[MATCH] 锚词中弱匹配降权: hits={anchor_hit}/{len(context_anchors)}, weighted={weighted_score:.3f}")
                 elif anchor_ratio >= 0.8:
                     weighted_score *= 1.12
                     self.log(f"[MATCH] 锚词强匹配加权: hits={anchor_hit}/{len(context_anchors)}, weighted={weighted_score:.3f}")
            
             # Audio Bonus - Check if match has audio
             has_audio = False
             if matches:
                 if first_match.get("audio_hash") or first_match.get("audio_event"):
                     has_audio = True
                 elif self._has_voice_match(result):
                     has_audio = True
            
             if has_audio:
                 # FIX: Only apply audio bonus if the base score is decent
                 if score > 0.65: 
                     weighted_score *= 1.15
                     self.log(f"[MATCH] 语音条目加成: has_audio=True, weighted={weighted_score:.3f}")
                 else:
                     self.log(f"[MATCH] 语音条目忽视: 分数过低 ({score:.3f})")

             if weighted_score > best_score:
                 best_score = weighted_score
                 best_result = result
                 best_text = text
                 best_conf = conf
                 best_result["_score"] = round(score, 3)
                 best_result["_query_key"] = key
                 best_result["_ocr_text"] = best_text
                 best_result["_ocr_conf"] = round(best_conf, 3)
                 best_result["_weighted"] = round(weighted_score, 3)
             
             # 收集成功匹配的子句
             if weighted_score >= 0.55:
                 is_sub = len(text) < len(context_text) * 0.95
                 if is_sub:
                     matches = result.get("matches", [])
                     first_match = matches[0] if matches else {}
                     tk = first_match.get("text_key")
                     if tk and tk not in seen_text_keys:
                         seen_text_keys.add(tk)
                         official_en = first_match.get("official_en") or text
                         official_cn = first_match.get("official_cn") or first_match.get("cn") or ""
                         successful_sub_matches.append({
                             "ocr": text,
                             "query_key": key,
                             "score": round(score, 3),
                             "text_key": tk,
                             "official_en": official_en,
                             "official_cn": official_cn,
                             "result_obj": result,
                             "weighted": weighted_score
                         })

        elapsed = time.time() - start_time

        # 长段落低分兜底：锚词救援检索
        if context_len >= 120 and (not best_result or best_score < 0.65):
            rescue = self._anchor_rescue_search(context_text)
            if rescue is not None:
                self.log(f"[SEARCH] 耗时: {elapsed:.2f}s, 采用锚词救援结果")
                return self._attach_title_hint(rescue, title_hint)

        if len(successful_sub_matches) >= 2 and best_score >= 0.62:
            def get_start_pos(item):
                try:
                    return context_text.lower().find(item["query_key"][:10])
                except Exception:
                    return 999
            successful_sub_matches.sort(key=get_start_pos)
            
            items = []
            has_high_confidence_audio = False
            for item in successful_sub_matches:
                items.append({
                    "ocr": item["ocr"],
                    "query_key": item["query_key"],
                    "score": item["score"],
                    "text_key": item["text_key"],
                    "official_en": item["official_en"],
                    "official_cn": item["official_cn"],
                })
                if item["score"] >= 0.85 and self._has_voice_match(item["result_obj"]):
                    has_high_confidence_audio = True
            
            multi_res = {
                "_multi": True,
                "items": items,
                "_has_audio": has_high_confidence_audio,
                "_official_en": " / ".join([i.get("official_en") or i.get("ocr") or "" for i in items if i.get("official_en") or i.get("ocr")]),
                "_official_cn": " / ".join([i.get("official_cn") or "" for i in items if i.get("official_cn")]),
                "_query_key": " / ".join([i["query_key"] for i in items if i.get("query_key")]),
                "_ocr_text": " / ".join([i["ocr"] for i in items if i.get("ocr")]),
                "_score": round(best_score, 3),
                "_weighted": round(best_score, 3),
            }
            if best_result:
                multi_res["matches"] = best_result.get("matches", [])
            self.log(f"[MATCH] 智能子句合并多条目输出: 合并了 {len(items)} 个独立子句台词")
            return self._attach_title_hint(multi_res, title_hint)

        if best_result:
            # 修改：增加全局置信度阈值
            if best_score < 0.55:
                self.log(f"[SEARCH] 耗时: {elapsed:.2f}s, 最佳匹配权重过低 ({best_score:.3f}), 丢弃结果")
                return None
            
            self.log(f"[SEARCH] 耗时: {elapsed:.2f}s, 最佳匹配: {best_result.get('_query_key')} (score={best_result.get('_score')}, weighted={best_score:.3f})")
        else:
            self.log(f"[SEARCH] 耗时: {elapsed:.2f}s, 未找到合适匹配")
        
        return self._attach_title_hint(best_result, title_hint)
