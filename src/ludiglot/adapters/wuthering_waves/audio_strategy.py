from __future__ import annotations

import re

from ludiglot.core.wwise_hash import WwiseHash


_CAMEL_SPLIT = re.compile(r"([a-z0-9])([A-Z])")


class WutheringAudioStrategy:
    """占位：vo_ 前缀 + Wwise Hash 计算逻辑。"""

    def build_hash(self, text_key: str) -> int:
        name = f"vo_{text_key}"
        return self.hash_name(name)

    def hash_name(self, name: str) -> int:
        return WwiseHash().hash_int(name)

    def build_hashes(self, text_key: str, voice_event: str | None = None) -> list[int]:
        names = self.build_names(text_key, voice_event)
        return [self.hash_name(name) for name in names]

    def build_names(self, text_key: str, voice_event: str | None = None) -> list[str]:
        candidates = []
        
        # 1. 处理显式提供的 voice_event
        event_name = self.parse_event_name(voice_event)
        if event_name:
            candidates.append(event_name)
            # 对于已经有明确事件名的情况，只添加基础的前缀变体，不盲目生成 nosub_xx
            self._add_variants(candidates, event_name)
            self._add_story_variants(candidates, event_name) # 恢复：剧情 ID 变体生成
        
        # 2. 处理基于 text_key 的启发式猜测
        if text_key:
            candidates.append(text_key)
            self._add_variants(candidates, text_key)
            self._add_story_variants(candidates, text_key) # 恢复：剧情 ID 变体生成
            
            # 特殊处理：如果 text_key 形如 Dialog_1001_1，尝试 vo_Dialog_1001_1
            if "_" in text_key:
                 pass # _add_variants already covers many prefix cases
        
        # 3. 为对话类型添加性别后缀变体（FavorWord等）
        gender_candidates = []
        for name in candidates:
            # 如果已有性别后缀，保持原样
            name_lower = name.lower()
            if '_f_' in name_lower or name_lower.endswith('_f') or '_m_' in name_lower or name_lower.endswith('_m'):
                continue
            # 添加女声变体（优先）
            gender_candidates.append(f"{name}_f")
            # 添加男声变体
            gender_candidates.append(f"{name}_m")
        
        candidates.extend(gender_candidates)
        
        # 4. 结果去重并返回
        dedup: list[str] = []
        seen = set()
        for name in candidates:
            # Wwise Hash 对大小写不敏感（通常转低位计算），但这里保留原始名以备后用
            # hash_name 内部会统一 .lower()
            if name in seen:
                continue
            seen.add(name)
            dedup.append(name)
        return dedup

    def _add_story_variants(self, candidates: list[str], base: str) -> None:
        """剧情语音常见命名：play_vo_*_nosub_0X 等。"""
        raw = base.strip()
        if not raw:
            return
        normalized = raw.replace("-", "_").replace(".", "_").replace(" ", "_")
        normalized = _CAMEL_SPLIT.sub(r"\1_\2", normalized).lower()
        normalized = normalized.strip("_")

        # 仅对可能是剧情/支线/主线的 Key 做增强
        if not normalized.startswith(("main_", "side_", "shixifeidu_", "story_")):
            return

        def _emit(prefix: str, core: str) -> None:
            for suffix in ("nosub_01", "nosub_02", "nosub_03", "nosub_04", "nosub_1", "nosub_2", "nosub_3", "nosub_4"):
                candidates.append(f"{prefix}{core}_{suffix}")

        def _merge_name_tokens(tokens: list[str]) -> str:
            if len(tokens) <= 1:
                return "_".join(tokens)
            # 保留首段(main/side/...), 合并后续非数字段
            name_tokens = []
            numeric_index = len(tokens)
            for idx, token in enumerate(tokens):
                if token.isdigit():
                    numeric_index = idx
                    break
                name_tokens.append(token)
            if len(name_tokens) <= 1:
                return "_".join(tokens)
            merged_name = name_tokens[0] + "_" + "".join(name_tokens[1:])
            rest = tokens[numeric_index:]
            if rest:
                return merged_name + "_" + "_".join(rest)
            return merged_name

        # 1) 原样 + play_vo_
        _emit("play_vo_", normalized)

        # 2) 合并角色名段（例如 lahai_roi -> lahairoi）
        tokens = normalized.split("_") if "_" in normalized else [normalized]
        merged = _merge_name_tokens(tokens)
        if merged != normalized:
            _emit("play_vo_", merged)

        # 3) 如果结尾是数字段，尝试去掉最后一段再加 nosub
        # 注意：对于 Main_XX_1_2_3_20 这样的精确行 ID，去掉结尾通常会命中错误的对话集。
        # 只有在特定的、通常较短且看起来不像是大型剧情集的 ID 上才启用此逻辑。
        # 这里的权宜之计是：如果 ID 很长且包含多个数字段，则不进行末尾数字裁剪的 nosub 猜测。
        if tokens and tokens[-1].isdigit():
             # 如果数字很大（>10），通常是具体的句子序号，不应模糊匹配
             try:
                 val = int(tokens[-1])
                 if val > 5:
                     return # 放弃模糊匹配
             except:
                 pass

             trimmed = "_".join(tokens[:-1])
             if trimmed:
                _emit("play_vo_", trimmed)
             # ... 其余逻辑保持 (合并最后两个数字) ...

    def _add_variants(self, candidates: list[str], base: str) -> None:
        """为基础名字添加各种可能的前缀变体。"""
        # 常见前缀集
        prefixes = ["vo_", "play_vo_", "p_vo_", "play_", "p_", "v_", "voice_"]

        # 基础归一化：处理大小写、分隔符与驼峰
        normalized = base.replace("-", "_").replace(".", "_").replace(" ", "_")
        normalized = _CAMEL_SPLIT.sub(r"\1_\2", normalized).lower()
        if normalized and normalized != base:
            candidates.append(normalized)
            for p in prefixes:
                if not normalized.startswith(p):
                    candidates.append(f"{p}{normalized}")
        
        # a. 添加带前缀的变体
        for p in prefixes:
            if not base.startswith(p):
                candidates.append(f"{p}{base}")
        
        # b. 如果已经带了前缀，尝试剥离后重组
        stripped = base
        for p in ["play_vo_", "p_vo_", "play_", "vo_", "p_"]:
            if base.startswith(p):
                stripped = base[len(p):]
                candidates.append(stripped)
                # 剥离后再尝试其它前缀
                for p2 in prefixes:
                    candidates.append(f"{p2}{stripped}")
                break
        
        # c. 针对 鸣潮 的特殊处理：多出来的 _sys_ 或 _toplayer 或同时存在
        if "_sys_" in base or "_toplayer" in base:
            clean = base.replace("_sys_", "").replace("_toplayer", "")
            if clean != base:
                candidates.append(clean)
                for p in prefixes:
                    candidates.append(f"{p}{clean}")
            
            # 同时处理带下划线的变体（如 _sys_ -> _）
            semi_clean = base.replace("_sys_", "_").replace("_toplayer", "")
            if semi_clean != base and semi_clean != clean:
                candidates.append(semi_clean)
                for p in prefixes:
                    candidates.append(f"{p}{semi_clean}")

        # d. toplayer / to_player 互换
        if "toplayer" in base:
            swapped = base.replace("toplayer", "to_player")
            if swapped != base:
                candidates.append(swapped)
                for p in prefixes:
                    candidates.append(f"{p}{swapped}")
        if "to_player" in base:
            swapped = base.replace("to_player", "toplayer")
            if swapped != base:
                candidates.append(swapped)
                for p in prefixes:
                    candidates.append(f"{p}{swapped}")

    def parse_event_name(self, voice_event: str | None) -> str | None:
        if not voice_event:
            return None
        raw = voice_event.strip()
        if not raw:
            return None
        segment = raw.rsplit("/", 1)[-1]
        if "." in segment:
            return segment.split(".")[-1]
        return segment

    def _parse_event_name(self, voice_event: str | None) -> str | None:
        return self.parse_event_name(voice_event)
