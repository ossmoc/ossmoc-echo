"""
高性能索引化搜索引擎
提供多级索引和缓存机制，大幅提升文本匹配速度
"""
from __future__ import annotations
from typing import Dict, List, Tuple, Optional, Any
from functools import lru_cache
from collections import defaultdict
import bisect

try:
    from rapidfuzz import fuzz, process
except Exception:
    fuzz = None
    process = None


class LengthBucketIndex:
    """基于长度的分桶索引，避免不必要的字符串比较"""
    
    def __init__(self, keys: List[str], bucket_size: int = 5):
        """
        Args:
            keys: 所有数据库键
            bucket_size: 桶的长度范围（例如5表示0-4, 5-9, 10-14...分组）
        """
        self.bucket_size = bucket_size
        self.buckets: Dict[int, List[str]] = defaultdict(list)
        self._sorted_bucket_ids: List[int] = []
        
        # 构建索引
        for key in keys:
            bucket_id = len(key) // bucket_size
            self.buckets[bucket_id].append(key)
        
        # 排序桶ID以便二分查找
        self._sorted_bucket_ids = sorted(self.buckets.keys())
    
    def get_candidates_by_length(self, query_len: int, tolerance: float = 0.4) -> List[str]:
        """
        根据查询长度获取候选键
        
        Args:
            query_len: 查询字符串长度
            tolerance: 容忍度（0.4表示允许±40%的长度差异）
        
        Returns:
            候选键列表
        """
        min_len = int(query_len * (1 - tolerance))
        max_len = int(query_len * (1 + tolerance))
        
        min_bucket = min_len // self.bucket_size
        max_bucket = max_len // self.bucket_size
        
        # 获取相关桶的所有键
        candidates = []
        for bucket_id in self._sorted_bucket_ids:
            if bucket_id < min_bucket:
                continue
            if bucket_id > max_bucket:
                break
            candidates.extend(self.buckets[bucket_id])
        
        return candidates


class PrefixIndex:
    """前缀索引，用于快速前缀匹配"""
    
    def __init__(self, keys: List[str], prefix_len: int = 3):
        """
        Args:
            keys: 所有数据库键
            prefix_len: 索引的前缀长度
        """
        self.prefix_len = prefix_len
        self.index: Dict[str, List[str]] = defaultdict(list)
        
        for key in keys:
            if len(key) >= prefix_len:
                prefix = key[:prefix_len]
                self.index[prefix].append(key)
    
    def get_by_prefix(self, query: str) -> List[str]:
        """获取具有相同前缀的所有键"""
        if len(query) < self.prefix_len:
            return []
        prefix = query[:self.prefix_len]
        return self.index.get(prefix, [])


class SubstringIndex:
    """子串索引，用于快速包含关系查询（延迟构建）"""
    
    def __init__(self, keys: List[str], min_key_len: int = 10):
        """
        Args:
            keys: 所有数据库键
            min_key_len: 只索引长度>=该值的键（短键子串匹配意义不大）
        """
        self.min_key_len = min_key_len
        self.all_keys = keys
        # 延迟构建：只在需要时构建
        self._index_built = False
        self.contains_map: Dict[str, List[str]] = {}
        self.contained_in_map: Dict[str, List[str]] = {}
    
    def _build_index_if_needed(self):
        """延迟构建索引（仅在首次使用时）"""
        if self._index_built:
            return
        
        # 对于大型数据库，子串索引构建成本太高
        # 改为运行时动态查询
        self._index_built = True
    
    def find_containing_keys(self, query: str, all_keys: List[str]) -> List[str]:
        """找到包含query的所有键（动态查询）"""
        if len(query) < self.min_key_len:
            return []
        
        # 直接线性扫描，但只扫描长键
        # 对于14万条记录，这仍然很快（毫秒级）
        return [k for k in all_keys if len(k) >= self.min_key_len and query in k]
    
    def find_contained_keys(self, query: str, all_keys: List[str]) -> List[str]:
        """找到被query包含的所有键（动态查询）"""
        if len(query) < self.min_key_len:
            return []
        
        # 只扫描长度相近的键（优化）
        min_len = self.min_key_len
        max_len = len(query)
        return [k for k in all_keys if min_len <= len(k) <= max_len and k in query]


class IndexedSearchEngine:
    """
    高性能索引化搜索引擎
    整合多种索引结构和缓存机制
    """
    
    def __init__(self, db_keys: List[str]):
        """
        Args:
            db_keys: 数据库所有键的列表
        """
        self.db_keys = db_keys
        self.key_set = set(db_keys)  # 快速精确匹配
        
        # 构建各类索引
        self._atomic_print(f"[INDEX] 正在构建搜索索引... (总键数: {len(db_keys)})")
        
        self.length_index = LengthBucketIndex(db_keys)
        self.prefix_index = PrefixIndex(db_keys)
        self.substring_index = SubstringIndex(db_keys)
        
        # LRU缓存用于重复查询
        self._search_cache_size = 1000
        self._init_cache()
        
        self._atomic_print(f"[INDEX] 索引构建完成")

    def _atomic_print(self, msg: str) -> None:
        import sys
        try:
            m = msg if msg.endswith("\n") else msg + "\n"
            sys.stdout.write(m)
            sys.stdout.flush()
        except Exception:
            # Best-effort logging: ignore any stdout write/flush errors
            pass
    
    def _init_cache(self):
        """初始化缓存（使用装饰器会有问题，手动管理）"""
        self._exact_cache: Dict[str, bool] = {}
        self._fuzzy_cache: Dict[Tuple[str, int], Tuple[str, float]] = {}
        self._cache_hits = 0
        self._cache_misses = 0
    
    def clear_cache(self):
        """清空缓存"""
        self._exact_cache.clear()
        self._fuzzy_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0
    
    def get_cache_stats(self) -> Dict[str, int]:
        """获取缓存统计"""
        return {
            'hits': self._cache_hits,
            'misses': self._cache_misses,
            'exact_size': len(self._exact_cache),
            'fuzzy_size': len(self._fuzzy_cache),
        }
    
    def exact_match(self, query: str) -> bool:
        """
        精确匹配检查
        
        Returns:
            True if exact match found
        """
        if query in self._exact_cache:
            self._cache_hits += 1
            return self._exact_cache[query]
        
        self._cache_misses += 1
        result = query in self.key_set
        
        # 缓存管理：简单FIFO
        if len(self._exact_cache) >= self._search_cache_size:
            # 删除最早的一半
            keys_to_remove = list(self._exact_cache.keys())[:self._search_cache_size // 2]
            for k in keys_to_remove:
                del self._exact_cache[k]
        
        self._exact_cache[query] = result
        return result
    
    def prefix_search(self, query: str, max_results: int = 10) -> List[str]:
        """
        前缀搜索
        
        Returns:
            匹配的键列表（按长度排序，越短越优先）
        """
        candidates = self.prefix_index.get_by_prefix(query)
        # 过滤并排序
        matches = [k for k in candidates if k.startswith(query)]
        matches.sort(key=len)
        return matches[:max_results]
    
    def substring_search(self, query: str, direction: str = 'both') -> List[str]:
        """
        子串搜索
        
        Args:
            query: 查询字符串
            direction: 'contains' (query包含键), 'in' (键包含query), 'both'
        
        Returns:
            匹配的键列表
        """
        results = []
        
        if direction in ('contains', 'both'):
            # 查找被query包含的键
            results.extend(self.substring_index.find_contained_keys(query, self.db_keys))
        
        if direction in ('in', 'both'):
            # 查找包含query的键
            results.extend(self.substring_index.find_containing_keys(query, self.db_keys))
        
        return list(set(results))  # 去重
    
    def fuzzy_search(self, query: str, top_k: int = 1, score_threshold: float = 0.5) -> List[Tuple[str, float]]:
        """
        模糊搜索（使用索引加速）
        
        Args:
            query: 查询字符串
            top_k: 返回前k个结果
            score_threshold: 最低分数阈值
        
        Returns:
            [(key, score), ...] 按分数降序
        """
        # 缓存检查
        cache_key = (query, top_k)
        if cache_key in self._fuzzy_cache:
            self._cache_hits += 1
            return [self._fuzzy_cache[cache_key]]
        
        self._cache_misses += 1
        
        query_len = len(query)
        
        # 1. 根据长度预筛选候选集（更激进的筛选）
        if query_len < 20:
            # 短查询：严格长度限制
            candidates = self.length_index.get_candidates_by_length(query_len, tolerance=0.3)
        elif query_len < 50:
            # 中等查询：中等限制
            candidates = self.length_index.get_candidates_by_length(query_len, tolerance=0.4)
        else:
            # 长查询：适中限制
            candidates = self.length_index.get_candidates_by_length(query_len, tolerance=0.5)
        
        # 2. 进一步限制候选集大小（关键优化）
        if len(candidates) > 5000:
            # 如果候选太多，使用前缀进一步筛选
            if len(query) >= 3:
                prefix_matches = self.prefix_index.get_by_prefix(query)
                if prefix_matches:
                    # 优先使用前缀匹配的候选
                    candidates = list(set(prefix_matches) & set(candidates))
            
            # 如果还是太多，随机抽样（保持多样性）
            if len(candidates) > 5000:
                import random
                candidates = random.sample(candidates, 5000)
        
        # 3. 如果候选太少，尝试前缀匹配扩展
        if len(candidates) < 50 and len(query) >= 3:
            prefix_matches = self.prefix_index.get_by_prefix(query)
            candidates = list(set(candidates) | set(prefix_matches))
        
        # 4. 执行模糊搜索
        if not candidates:
            return []
        
        if fuzz and process:
            # 使用更快的评分器
            results = process.extract(
                query, 
                candidates, 
                scorer=fuzz.ratio,  # ratio比token_set_ratio快
                limit=top_k,
                score_cutoff=score_threshold * 100  # 提前过滤低分
            )
            matches = [(str(item), float(score) / 100.0) for item, score, _ in results]
        else:
            # 降级为SequenceMatcher
            from difflib import SequenceMatcher
            scores = [(k, SequenceMatcher(None, query, k).ratio()) for k in candidates]
            scores.sort(key=lambda x: x[1], reverse=True)
            matches = [(k, s) for k, s in scores[:top_k] if s >= score_threshold]
        
        # 缓存结果
        if matches and len(self._fuzzy_cache) < self._search_cache_size:
            self._fuzzy_cache[cache_key] = matches[0]
        
        return matches
    
    def smart_search(self, query: str) -> Tuple[str, float]:
        """
        智能搜索：自动选择最优策略
        
        Returns:
            (best_key, score)
        """
        # 1. 精确匹配
        if self.exact_match(query):
            return query, 1.0
        
        query_len = len(query)
        
        # 2. 前缀匹配（针对长查询）
        if query_len >= 10:
            prefix_matches = self.prefix_search(query, max_results=1)
            if prefix_matches:
                best_prefix = prefix_matches[0]
                return best_prefix, 0.99
        
        # 3. 子串匹配
        if query_len >= 10:
            # 查找包含query的键
            containing = self.substring_index.find_containing_keys(query, self.db_keys)
            if containing:
                best_contain = min(containing, key=len)
                length_ratio = len(query) / len(best_contain)
                if length_ratio >= 0.6:
                    score = 0.95 if length_ratio >= 0.85 else 0.90
                    return best_contain, score
            
            # 查找被query包含的键
            contained = self.substring_index.find_contained_keys(query, self.db_keys)
            if contained:
                best_contained = min(contained, key=lambda k: abs(len(k) - len(query)))
                if len(best_contained) <= len(query) * 3:
                    return best_contained, 0.98
        
        # 4. 模糊搜索
        fuzzy_results = self.fuzzy_search(query, top_k=1, score_threshold=0.5)
        if fuzzy_results:
            return fuzzy_results[0]
        
        # 5. 未找到
        return "", 0.0
