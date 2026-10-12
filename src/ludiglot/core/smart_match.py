"""
智能文本匹配算法 - 支持混合单行标题和多行长文本的场景

优化点：
1. 智能区分单行标题（如"Ms. Voss"）和多行长文本
2. 混合内容时分段处理：第一行单独匹配，后续行拼接匹配
3. 优先返回有语音的条目
4. 保留标题信息用于显示
5. 支持 N.A.N.A. 等缩写人名识别
6. 支持 "SpeakerName: dialogue" 格式的说话者前缀剥离
"""

from __future__ import annotations
import re
from typing import Dict, List, Tuple, Any

# 缩写人名模式：N.A.N.A., U.S.A., etc. (两个以上大写字母+点组合)
_ABBREVIATION_RE = re.compile(r'^(?:[A-Z]+\.){2,}[A-Z]*\.?$')

# 说话者前缀模式：匹配 "Name: dialogue" 或 "N.A.N.A.: dialogue" 或 "First Last: dialogue"
_SPEAKER_PREFIX_RE = re.compile(
    r'^('
    r'(?:[A-Z]+\.)*[A-Z][A-Za-z0-9\'-]*\.?'   # 首词（末尾可带点，如 N.A.N.A. 或 Luuk）
    r'(?:\s+[A-Z][A-Za-z0-9\'-]*\.?){0,3}'    # 可选后续大写词（如 " Herssen"）
    r')\s*:\s+(.+)$',
    re.DOTALL,
)


def strip_speaker_prefix(text: str):
    """检测并剥离 'SpeakerName: Dialogue content' 格式的说话者前缀。
    返回 (speaker, content) 或 None。
    内容必须至少 10 个字符且比 speaker 长。
    """
    text = text.strip()
    m = _SPEAKER_PREFIX_RE.match(text)
    if not m:
        return None
    speaker = m.group(1).strip()
    content = m.group(2).strip()
    if len(content) >= 10 and len(content) > len(speaker):
        return speaker, content
    return None


def analyze_line_characteristics(text: str, conf: float) -> Dict[str, Any]:
    """分析单行文本的特征"""
    cleaned = text.strip()
    word_count = len(cleaned.split())
    char_count = len(cleaned)
    
    # 判断是否为短标题（排除缩写中的句号，如 Ms., Dr., Mr.）
    is_short = word_count <= 3 and char_count <= 25
    # 移除常见缩写后再检查标点
    test_text = cleaned.replace('Ms.', 'Ms').replace('Mr.', 'Mr').replace('Dr.', 'Dr')
    test_text = test_text.replace('Mrs.', 'Mrs').replace('Prof.', 'Prof').replace('St.', 'St')
    # 缩写人名（如 N.A.N.A.）即使含点号也算 title-like
    is_abbreviation_name = bool(_ABBREVIATION_RE.match(cleaned))
    is_title_like = (is_short and not any(ch in test_text for ch in [',', '.', '!', '?', ';'])) or is_abbreviation_name
    
    # 判断是否为长描述
    is_long = word_count >= 6 or char_count >= 40
    has_punctuation = any(ch in cleaned for ch in [',', '.', '!', '?'])
    
    return {
        'text': text,
        'cleaned': cleaned,
        'word_count': word_count,
        'char_count': char_count,
        'is_short': is_short,
        'is_title_like': is_title_like,
        'is_abbreviation_name': is_abbreviation_name,
        'is_long': is_long,
        'has_punctuation': has_punctuation,
        'conf': conf,
    }


def detect_mixed_content(lines_info: List[Dict[str, Any]]) -> Dict[str, Any]:
    """检测是否为混合内容（标题 + 长文本）"""
    if len(lines_info) < 2:
        return {'is_mixed': False}
    
    first = lines_info[0]
    rest = lines_info[1:]
    
    # 判断：第一行是标题，后续行构成长文本
    if first['is_title_like']:
        rest_word_count = sum(l['word_count'] for l in rest)
        rest_has_long = any(l['is_long'] or l['has_punctuation'] for l in rest)
        
        if rest_word_count >= 6 or rest_has_long:
            return {
                'is_mixed': True,
                'title_line': first,
                'content_lines': rest,
                'combined_words': rest_word_count,
            }
    
    return {'is_mixed': False}


def build_smart_candidates(
    lines: List[Tuple[str, float]], 
    clean_func=None,
    normalize_func=None
) -> Dict[str, Any]:
    res = _build_smart_candidates_raw(lines, clean_func, normalize_func)
    candidates = res.get('candidates', [])
    if not candidates:
        return res
        
    if normalize_func is None:
        normalize_func = lambda x: x.lower().replace(' ', '')
        
    sub_sentence_candidates = []
    for text, conf in candidates:
        if not text:
            continue
        # 英文/中文标点分句
        parts = re.split(r'(?<=[.!?。！？])\s+', text)
        if len(parts) >= 2:
            for p in parts:
                p_clean = p.strip()
                if p_clean and len(p_clean.split()) >= 2 and len(p_clean) >= 6:
                    sub_sentence_candidates.append((p_clean, conf * 0.98))
                    
    existing_keys = {normalize_func(t) for t, _ in candidates}
    for text, conf in sub_sentence_candidates:
        key = normalize_func(text)
        if key and key not in existing_keys:
            candidates.append((text, conf))
            existing_keys.add(key)
            
    # 按照置信度降序排序，确保高质量候选排在前面
    candidates.sort(key=lambda x: x[1], reverse=True)
    res['candidates'] = candidates
    return res


def _build_smart_candidates_raw(
    lines: List[Tuple[str, float]], 
    clean_func=None,
    normalize_func=None
) -> Dict[str, Any]:
    """构建智能候选集合
    
    返回:
        {
            'is_mixed': bool,
            'title_text': str,  # 如果是混合内容
            'content_text': str,  # 如果是混合内容
            'full_text': str,  # 完整文本
            'candidates': [(text, conf), ...],  # 待匹配的候选
            'strategy': 'mixed' | 'list' | 'long' | 'single'
        }
    """
    if clean_func is None:
        clean_func = lambda x: x.strip()
    if normalize_func is None:
        normalize_func = lambda x: x.lower().replace(' ', '')
    
    # 分析每行特征
    lines_info = [analyze_line_characteristics(text, conf) for text, conf in lines]
    
    # 检测混合内容
    mixed_info = detect_mixed_content(lines_info)
    
    if mixed_info['is_mixed']:
        # 混合内容：标题 + 描述
        title_line = mixed_info['title_line']
        content_lines = mixed_info['content_lines']
        
        title_text = title_line['cleaned']
        content_text = ' '.join(l['cleaned'] for l in content_lines)
        full_text = f"{title_text} {content_text}"
        avg_content_conf = sum(l['conf'] for l in content_lines) / len(content_lines)
        
        # 候选：1. 内容单独 2. 标题单独 3. 完整文本
        candidates = [
            (content_text, avg_content_conf),
            (title_text, title_line['conf']),
            (full_text, sum(l['conf'] for l in lines_info) / len(lines_info)),
        ]

        # 新增：如果 content_text 以 "LastName: dialogue" 开头（分割人名场景）
        # 例如：Line1="Luuk", Line2="Herssen: The infirmary is..."
        content_speaker_strip = strip_speaker_prefix(content_text)
        if content_speaker_strip:
            _, actual_dialogue = content_speaker_strip
            candidates.insert(0, (actual_dialogue, avg_content_conf))
        
        return {
            'is_mixed': True,
            'title_text': title_text,
            'content_text': content_text,
            'full_text': full_text,
            'candidates': candidates,
            'strategy': 'mixed',
        }
    
    # 检测列表模式
    all_short = all(l['is_short'] for l in lines_info)
    if len(lines_info) >= 3 and all_short:
        candidates = [(l['cleaned'], l['conf']) for l in lines_info]
        return {
            'is_mixed': False,
            'full_text': ' '.join(l['cleaned'] for l in lines_info),
            'candidates': candidates,
            'strategy': 'list',
        }
    
    # 长文本模式
    if len(lines_info) >= 2:
        full_text = ' '.join(l['cleaned'] for l in lines_info)
        avg_conf = sum(l['conf'] for l in lines_info) / len(lines_info)
        candidates = [(full_text, avg_conf)]

        # 将每个独立的、长度足够的行也作为候选评估，防止硬拼接导致整体失配
        for l in lines_info:
            cleaned_line = l['cleaned'].strip()
            if len(cleaned_line.split()) >= 2:
                candidates.append((cleaned_line, l['conf']))

        # 新增：检测分割人名场景
        # 场景1: Line1=缩写人名(N.A.N.A.), Line2=正文  → 直接取 Line2 以后的文本
        # 场景2: Line1=人名前半(Luuk), Line2=LastName: dialogue → 在 mixed 模式已处理
        first_info = lines_info[0]
        if first_info['is_title_like'] or first_info['is_abbreviation_name']:
            rest_text = ' '.join(l['cleaned'] for l in lines_info[1:])
            if rest_text and len(rest_text.split()) >= 4:
                candidates.insert(0, (rest_text, avg_conf))
                # 如果 rest_text 也以 LastName: 开头，再次剥离
                rest_stripped = strip_speaker_prefix(rest_text)
                if rest_stripped:
                    _, rest_dialogue = rest_stripped
                    candidates.insert(0, (rest_dialogue, avg_conf))
        else:
            # 原有逻辑：尝试剥离首词（仅 isalpha 的情况）
            first_line_cleaned = first_info['cleaned']
            words = first_line_cleaned.split()
            if words:
                first_word = words[0]
                if len(first_word) < 10 and first_word[0].isupper() and first_word.isalpha():
                    full_words = full_text.split()
                    if len(full_words) > 3:
                        stripped_text = " ".join(full_words[1:])
                        candidates.append((stripped_text, 0.85))

        # 添加滑动窗口候选（如果文本很长）
        if len(full_text.split()) >= 10:
            words = full_text.split()
            for start in range(0, len(words) - 5, 3):
                segment = ' '.join(words[start:start+10])
                candidates.append((segment, 0.8))
        return {
            'is_mixed': False,
            'full_text': full_text,
            'candidates': candidates,
            'strategy': 'long',
        }
    
    # 单行模式
    if lines_info:
        l = lines_info[0]
        base_candidates = [(l['cleaned'], l['conf'])]

        # 优先尝试：说话者前缀剥离（"N.A.N.A.: dialogue..." 或 "Name: dialogue..."）
        speaker_strip = strip_speaker_prefix(l['cleaned'])
        if speaker_strip:
            _, dialogue = speaker_strip
            base_candidates.insert(0, (dialogue, l['conf']))

        # 额外候选：短文本拆分 n-gram（解决 'Weapon Wildfire Mark' 一类带前后缀情况）
        words = l['cleaned'].split()
        if 2 <= len(words) <= 6:
            max_n = min(4, len(words))
            for n in range(2, max_n + 1):
                for i in range(0, len(words) - n + 1):
                    seg = " ".join(words[i:i + n])
                    if seg != l['cleaned']:
                        base_candidates.append((seg, l['conf'] * 0.95))

        # 原有逻辑：尝试剥离首词人名（"Name Dialogue..."格式，无冒号）
        if len(words) > 3 and not speaker_strip:
            first_word = words[0]
            if len(first_word) < 10 and first_word[0].isupper() and first_word.isalpha():
                rest_text = " ".join(words[1:])
                if len(rest_text) > 10:
                    base_candidates.append((rest_text, l['conf']))

        return {
            'is_mixed': False,
            'full_text': l['cleaned'],
            'candidates': base_candidates,
            'strategy': 'single',
        }
    
    return {
        'is_mixed': False,
        'full_text': '',
        'candidates': [],
        'strategy': 'empty',
    }


# 示例用法
if __name__ == '__main__':
    # 测试用例1: 混合内容（标题 + 长文本）
    test_lines_1 = [
        ("Ms. Voss", 0.92),
        ("We've collected enough Exo Strider components", 0.91),
        ("to build exclusive simulator cockpits for Synchronists.", 0.90),
    ]
    
    result = build_smart_candidates(test_lines_1)
    print("测试1 - 混合内容:")
    print(f"  策略: {result['strategy']}")
    print(f"  是否混合: {result['is_mixed']}")
    if result['is_mixed']:
        print(f"  标题: {result['title_text']}")
        print(f"  内容: {result['content_text']}")
    print(f"  候选数: {len(result['candidates'])}")
    print()
    
    # 测试用例2: 列表模式
    test_lines_2 = [
        ("HP", 0.95),
        ("ATK", 0.94),
        ("DEF", 0.93),
    ]
    
    result = build_smart_candidates(test_lines_2)
    print("测试2 - 列表模式:")
    print(f"  策略: {result['strategy']}")
    print(f"  候选数: {len(result['candidates'])}")
    print()
    
    # 测试用例3: 长文本
    test_lines_3 = [
        ("This is a very long sentence that", 0.88),
        ("spans multiple lines and contains", 0.87),
        ("important information about the game.", 0.86),
    ]
    
    result = build_smart_candidates(test_lines_3)
    print("测试3 - 长文本:")
    print(f"  策略: {result['strategy']}")
    print(f"  完整文本: {result['full_text']}")
    print(f"  候选数: {len(result['candidates'])}")
