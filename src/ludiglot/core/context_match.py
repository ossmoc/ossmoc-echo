"""Ordered word evidence within a supplied, verified forward dialogue window.

This is not semantic translation or a global approximate search. An unread
critical word is uncertainty, never evidence that a competing branch is false.
"""
from collections import Counter
from dataclasses import dataclass
from math import log
import re
import unicodedata

from rapidfuzz.distance import Levenshtein


_STOP = frozenset('a an the i you he she it we they me him her us them my your his its our their '
    'this that these those is am are was were be been being do does did have has had '
    'will would shall should can could may might must to of in on at by for with from '
    'and or but as if then than so what which who whom whose when where how'.split())
_WEAK = frozenset('want wants know knows think see look get got come go make take like need '
    'said say well very really just still here there now'.split())
_NEGATIVE = frozenset('not no never neither nor none nothing nobody without'.split())
_NUMBERS = {word: str(number) for number, word in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen '
    'fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split())}
_NUMBERS.update(thirty='30', forty='40', fifty='50', sixty='60', seventy='70',
                eighty='80', ninety='90', hundred='100', thousand='1000')
_NEG_CONTRACTIONS = {
    "can't": 'can not', 'cannot': 'can not', "won't": 'will not',
    "isn't": 'is not', "aren't": 'are not', "wasn't": 'was not', "weren't": 'were not',
    "don't": 'do not', "doesn't": 'does not', "didn't": 'did not',
    "haven't": 'have not', "hasn't": 'has not', "hadn't": 'had not',
    "couldn't": 'could not', "wouldn't": 'would not', "shouldn't": 'should not',
    "mustn't": 'must not', "needn't": 'need not',
}
_NEG_PATTERN = re.compile(r"\b(?:"+'|'.join(map(re.escape, _NEG_CONTRACTIONS))+r")\b")


def evidence_words(text: str) -> tuple[str, ...]:
    text = unicodedata.normalize('NFKC', text).casefold().replace('’', "'")
    text = re.sub(r'<[^>]+>', ' ', text)
    text = _NEG_PATTERN.sub(lambda match: _NEG_CONTRACTIONS[match[0]], text)
    # Missing apostrophes are common OCR noise; only negative forms are admitted.
    for word in ('dont', 'doesnt', 'didnt', 'isnt', 'arent', 'wasnt', 'werent',
                 'havent', 'hasnt', 'hadnt', 'couldnt', 'wouldnt', 'shouldnt', 'mustnt'):
        text = re.sub(r'\b'+word+r'\b', _NEG_CONTRACTIONS[word[:-1]+"'t"], text)
    text = re.sub(r"\bi'm\b", 'i am', text)
    text = re.sub(r"\b(you|we|they)'re\b", r'\1 are', text)
    text = re.sub(r"\b(i|you|we|they)'ve\b", r'\1 have', text)
    text = re.sub(r"\b(i|you|we|they|he|she|it)'ll\b", r'\1 will', text)
    # General 's / 'd remain ambiguous (is/has and had/would).
    return tuple(_NUMBERS.get(word, word) for word in re.findall(
        r'(?<!\w)\d+(?:\.\d+)?(?!\w)|[^\W_]+', text))


def critical_words(words):
    return (tuple(word for word in words if word in _NEGATIVE),
            tuple(word for word in words if re.fullmatch(r'\d+(?:\.\d+)?', word)))


def observed_conflict(query: str, target: str) -> bool:
    """Reject explicitly read contradictory negation/numbers, not absent ink."""
    q, t = critical_words(evidence_words(query)), critical_words(evidence_words(target))
    return any(visible and visible != expected for visible, expected in zip(q, t))


def _subsequence(small, large):
    iterator = iter(large)
    return all(any(value == item for item in iterator) for value in small)


def _content(word):
    return word not in _STOP and word not in _NEGATIVE and word.isalpha()


def _strong(word):
    return _content(word) and word not in _WEAK and len(word) >= 4


def _ordered_pairs(query, target, weights):
    """Weighted exact LCS; repeated words cannot be reused or reordered."""
    table = [[0.0]*(len(target)+1) for _ in range(len(query)+1)]
    for i, word in enumerate(query, 1):
        for j, other in enumerate(target, 1):
            table[i][j] = max(table[i-1][j], table[i][j-1],
                table[i-1][j-1]+weights[word] if word == other else 0.0)
    i, j = len(query), len(target)
    pairs = []
    while i and j:
        if query[i-1] == target[j-1] and table[i][j] == table[i-1][j-1]+weights[query[i-1]]:
            pairs.append((i-1, j-1)); i -= 1; j -= 1
        elif table[i-1][j] >= table[i][j-1]:
            i -= 1
        else:
            j -= 1
    return list(reversed(pairs))


@dataclass(frozen=True)
class WordEvidence:
    score: float
    query_coverage: float
    exact_anchors: tuple[str, ...]
    eligible: bool


def _rank_forward(query: str, candidates):
    query_words = evidence_words(query)
    if not 2 <= len(query_words) <= 64 or not candidates or len(candidates) > 7:
        return []
    targets = [evidence_words(text) for text in candidates]
    frequency = Counter(word for words in targets for word in set(words))
    weights = {word: (.15 if word in _STOP else .45 if word in _WEAK else
        1.0+log((len(targets)+1)/(frequency[word]+1)))
        for word in set(query_words).union(*(set(words) for words in targets))}
    qcritical = critical_words(query_words)
    scored = []
    for offset, words in enumerate(targets):
        if not words or len(words) > 160 or '{' in candidates[offset]:
            continue  # Variable-name templates have a separate, stricter parser.
        tcritical = critical_words(words)
        if not all(_subsequence(q, t) for q, t in zip(qcritical, tcritical)):
            continue  # Explicitly contradicted competitor, not merely unread.
        pairs = _ordered_pairs(query_words, words, weights)
        anchors = tuple(sorted({query_words[i] for i, _ in pairs if _strong(query_words[i])}))
        credit = sum(weights[query_words[i]] for i, _ in pairs)
        # One typo may supplement two exact anchors, never replace them.
        if len(anchors) >= 2:
            used = {i for i, _ in pairs}
            bonus = 0.0
            for i, word in enumerate(query_words):
                if i in used or not _strong(word) or len(word) < 5:
                    continue
                left = max((j for qi, j in pairs if qi < i), default=-1)
                right = min((j for qi, j in pairs if qi > i), default=len(words))
                if any(_strong(other) and len(other) >= 5 and word[0] == other[0]
                       and Levenshtein.distance(word, other, score_cutoff=1) <= 1
                       for other in words[left+1:right]):
                    bonus = max(bonus, .7*weights[word])
            credit += bonus
        coverage = credit / sum(weights[word] for word in query_words)
        target_coverage = credit / sum(weights[word] for word in words)
        dense = (len(pairs) >= 5 and coverage >= .9 and len(pairs)/len(words) >= .6
                 and any(_content(query_words[i]) for i, _ in pairs))
        distinctive = len(anchors) >= 2 and sum(map(len, anchors)) >= 12
        eligible = coverage >= .82 and qcritical == tcritical and (distinctive or dense)
        # Coverage of the visible query dominates; target length cannot pick
        # a branch sharing the same evidence. Keep unqualified runners-up too.
        scored.append((offset, WordEvidence(.85*coverage+.15*min(1.0, target_coverage),
                                            coverage, anchors, eligible)))
    return scored


def has_close_forward_competitor(query: str, winner: int, candidates) -> bool:
    scored = _rank_forward(query, candidates)
    evidence = next((e for offset, e in scored if offset == winner), None)
    return bool(evidence and any(offset != winner and (
                                evidence.query_coverage-other.query_coverage < .12
                                or evidence.score-other.score < .12)
                                for offset, other in scored))


def has_forward_word_evidence(query: str, candidates) -> bool:
    """An unresolved query has enough anchors to invalidate an old caption."""
    return any(len(evidence.exact_anchors) >= 2 and evidence.query_coverage >= .65
               for _, evidence in _rank_forward(query, candidates))


def choose_forward(query: str, candidates) -> int | None:
    """Return a unique candidate offset, or wait for more visible text.

    Candidates must already be current + at most six real forward records.
    Length/nearest-ID is never a tie-breaker; unresolved competitors count even
    if their critical words have not yet appeared on screen.
    """
    scored = _rank_forward(query, candidates)
    winners = [(offset, evidence) for offset, evidence in scored if evidence.eligible]
    if not winners:
        return None
    winner, best = max(winners, key=lambda item: item[1].score)
    if best.score < .78 or any(offset != winner and (
                              best.query_coverage-other.query_coverage < .12
                              or best.score-other.score < .12)
                              for offset, other in scored):
        return None
    return winner
