"""Ordered recovery of translation links; never a live OCR prediction.

Candidates are reviewed targets with source identity, not arbitrary Vietnamese
strings. A unique target resolves ambiguity, but does not create evidence.
"""
from collections import defaultdict
from dataclasses import dataclass
import re
import unicodedata

from rapidfuzz.fuzz import ratio


def text(value):
    return ' '.join(unicodedata.normalize('NFC', str(value or '')).split())


def visible(value):
    return re.sub(r'<[^>]*>', '', text(value))


def words(value):
    return ' '.join(re.findall(r"\w+", visible(value).replace('’', "'").casefold()))


def similarity(a, b):
    a, b = words(a), words(b)
    return ratio(a, b) / 100 if a and b else 0.0


def compatible_facts(a, b):
    """Do not silently change explicit numbers, placeholders or negation."""
    def facts(value):
        value = visible(value).casefold().replace('’', "'")
        value = re.sub(r"\b(can't|cannot|won't|\w+n't)\b", ' not ', value)
        return (tuple(sorted(re.findall(r'\d+(?:[.,]\d+)*', value))),
                tuple(sorted(re.findall(r'\{[^{}]+\}', value))),
                bool(re.search(r'\b(?:not|never|no|neither|without)\b', value)))
    return facts(a) == facts(b)


def family(namespace):
    parent, _, leaf = namespace.replace('\\', '/').casefold().rpartition('/')
    if leaf in {'lang_multi_text.db', 'lang_multi_text_1sthalf.db', 'lang_multi_text_2ndhalf.db'}:
        return parent, 'multi_text'
    return parent, leaf


@dataclass(frozen=True)
class Candidate:
    namespace: str
    table: str
    key: str
    english: str
    chinese: str
    vietnamese: str


class StagedMatcher:
    def __init__(self, candidates, members=None):
        self.by_id = defaultdict(list)
        self.by_en = defaultdict(list)
        self.by_cn = defaultdict(list)
        self.by_flow = defaultdict(list)
        self.members = members or {}
        for c in set(candidates):
            if not all((c.namespace, c.table, c.key, text(c.english), text(c.vietnamese))):
                continue
            if '*' in c.english or (c.chinese and set(c.chinese.strip()) == {'*'}):
                continue
            self.by_id[c.table, c.key].append(c)
            self.by_en[c.namespace, c.table, text(c.english)].append(c)
            if text(c.chinese):
                self.by_cn[c.namespace, c.table, text(c.chinese)].append(c)
            for flow in self.members.get(c.key, {}).get('flows', []):
                self.by_flow[c.namespace, c.table, flow].append(c)

    @staticmethod
    def choose(candidates):
        candidates = set(candidates)
        if not candidates:
            return None, ''
        if len({text(c.vietnamese) for c in candidates}) != 1:
            return None, 'ambiguous_candidates'
        # Stable choice across aliases, SQL order and process hash randomization.
        return sorted(candidates, key=lambda c: (c.namespace, c.table, c.key, c.english, c.chinese))[0], ''

    def resolve(self, namespace, table, key, english, chinese):
        ns = namespace.replace('\\', '/').casefold()
        en, cn = text(english), text(chinese)
        if not ns or not table or not en or '*' in en:
            return None, '', 0.0, 'missing_source_identity'
        same_id = self.by_id.get((table, key), ())
        scoped = [c for c in same_id if c.namespace == ns]
        related = [c for c in same_id if family(c.namespace) == family(ns)]
        cn_ok = lambda c: bool(cn and text(c.chinese) == cn)
        cn_conflict = lambda c: bool(cn and text(c.chinese) and text(c.chinese) != cn)
        safe = lambda c: compatible_facts(en, c.english)
        # Short repeated strings require their ID; never borrow a global "Yes".
        distinctive = len(words(en).split()) >= 3 and len(words(en)) >= 12
        exact = list(self.by_en.get((ns, table, en), ())) if distinctive else [c for c in scoped if text(c.english) == en]
        exact = [c for c in exact if not cn_conflict(c)]
        stages = [
            ('exact_text', exact),
            ('id', [c for c in scoped if not cn_conflict(c) and safe(c)
                    and (cn_ok(c) or similarity(en, c.english) >= .70)]),
            ('chinese', [c for c in self.by_cn.get((ns, table, cn), ())
                         if safe(c) and (c.key == key or distinctive)] if cn else []),
            ('unique_target', [c for c in related if cn_ok(c) and safe(c)]),
            ('database_relation', [c for c in related if not cn_conflict(c) and safe(c)
                                   and similarity(en, c.english) >= .70]),
        ]
        for method, choices in stages:
            picked, reason = self.choose(choices)
            if reason:
                return None, '', 0.0, reason
            if picked:
                return picked, method, similarity(en, picked.english), ''
        stages = []
        # Fuzzy candidates stay within the same source identity or an actual
        # ShowTalk flow. No whole-database 70% search, especially for short text.
        pool = set(scoped)
        if distinctive:
            for flow in self.members.get(key, {}).get('flows', []):
                values = self.by_flow.get((ns, table, flow), ())
                if len(values) <= 500:
                    pool.update(values)
        scored = sorted(((similarity(en, c.english), c) for c in pool
                         if safe(c) and not cn_conflict(c)), key=lambda x: -x[0])
        for threshold in (.90, .80, .70):
            eligible = [(score, c) for score, c in scored if score >= threshold]
            if eligible:
                best = eligible[0][0]
                # Competing translations within five points remain ambiguous,
                # including a runner-up just below the current tier boundary.
                choices = [c for score, c in scored if score >= .70 and best-score <= .05]
            else:
                choices = []
            stages.append((f'fuzzy_{int(threshold*100)}', choices))
        for method, choices in stages:
            picked, reason = self.choose(choices)
            if reason:
                return None, '', 0.0, reason
            if picked:
                return picked, method, similarity(en, picked.english), ''
        return None, '', 0.0, 'no_supported_match'


def name_only_keys(cache):
    """Use source character-name records, not capitalization/word count guesses."""
    # Search aliases repeat the same row several times. Normalize each source
    # name/text once, including when loading a JSON cache without shared objects.
    rows = {}
    for p in cache.values():
        for r in p.get('matches', []):
            rows.setdefault((str(r.get('text_key', '')), str(r.get('official_en', '')),
                             str(r.get('source_table', ''))), r)
    names = set()
    for r in rows.values():
        table = str(r.get('source_table', ''))
        key = str(r.get('text_key', ''))
        if table == 'Speaker' or (table == 'MultiText' and re.match(
                r'^(?:Speaker|RoleInfo|OccupationConfig|CharacterDisplayStyle)_.*_(?:Name|NickName)$', key)):
            name = words(r.get('official_en', ''))
            if name:
                names.add(name)
    excluded = set()
    other = set()
    for r in rows.values():
        en = visible(r.get('official_en', ''))
        parts = [part for part in re.split(r'[!?…。,，;；]+', en) if words(part)]
        player = lambda part: bool(re.fullmatch(r'[\W_]*\{(?:PlayerName|player-name|PlayerNickname)\}[\W_]*', part, re.I))
        if (words(en) in names or player(en)
                or (parts and all(words(part) in names or player(part) for part in parts))):
            excluded.add(str(r.get('text_key', '')))
        else:
            other.add(str(r.get('text_key', '')))
    return excluded - other
