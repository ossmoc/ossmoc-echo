"""Screen-only WuWa layout detection and conservative, verified retrieval.

All coordinates are physical pixels relative to the fixed capture frame. The
index admits only translations already validated by the active WAHU provider;
OCR confidence is deliberately not used as a translation confidence score.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import Counter
from difflib import SequenceMatcher
import re
import html
import hashlib
import time
import unicodedata
from typing import Any

from rapidfuzz import fuzz, process
from ludiglot.core.story_catalog import dialogue_scene, story_catalog


SEARCH_BANDS = {
    "title": (.18, .07, .64, .10), "document": (.22, .18, .56, .65),
    "choices": (.62, .38, .32, .34), "dialogue": (.10, .80, .80, .12),
    "transition": (.12, .40, .76, .20),
    "glossary_label": (.18, .10, .20, .09),
    "glossary": (.29, .23, .41, .43),
    "walking_speaker": (.36, .66, .28, .065),
    "walking_dialogue": (.10, .735, .80, .075),
}


def normalized(text: str) -> str:
    text = text.replace("\\n", "\n").replace("\\r", "\r")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


@dataclass(frozen=True)
class VerifiedText:
    identity: tuple[str, str]
    english: str
    vietnamese: str
    partial: bool = False
    speaker: str = ""



@dataclass(frozen=True)
class AutoTextBlock:
    kind: str
    bounds: tuple[int, int, int, int]
    source: str
    target: str
    identity: tuple[str, str]
    order: int = 0
    partial: bool = False
    caption_mode: str = 'cinematic'


@dataclass(frozen=True)
class AutoDocument:
    identity: tuple[str, str]
    title: str
    target: str
    partial: bool = False
    source_title: str = ""
    source_body: str = ""


@dataclass(frozen=True)
class AutoFrameResult:
    blocks: tuple[AutoTextBlock, ...] = ()
    document: AutoDocument | None = None
    document_present: bool = False
    diagnostics: tuple[str, ...] = ()
    detected_kinds: tuple[str, ...] = ()
    # Presence includes recognized-but-untranslated text. It is independent of
    # successful retrieval so transient OCR/matcher failures do not flash masks.
    present_kinds: tuple[str, ...] = ()
    observed_texts: tuple[tuple[str, str], ...] = ()
    # Positive glyph evidence from the last verified dialogue, not OCR guesses.
    stable_kinds: tuple[str, ...] = ()


class AutoTextIndex:
    def __init__(self, database: dict[str, Any]):
        self.exact: dict[str, list[VerifiedText]] = {}
        self.choice_controls: dict[str, list[VerifiedText]] = {}
        self.documents: dict[tuple[str, str], tuple[VerifiedText, VerifiedText]] = {}
        self.glossaries: dict[tuple[str, str], tuple[VerifiedText, VerifiedText]] = {}
        self.context_hint = ""
        self.story_group = ""
        self._scope_signature = None
        self._scope_keys: tuple[str, ...] = ()
        self._templates: list[VerifiedText] = []
        self.speaker_names = set()
        records: dict[tuple[str, str], VerifiedText] = {}
        ambiguous = set()
        for group in database.values():
            for item in group.get("matches", []):
                key = str(item.get('text_key') or '')
                if re.search(r'_Name$', key, re.I):
                    name = normalized(str(item.get('official_en') or ''))
                    if 1 <= len(name.split()) <= 4 and 2 <= len(name) <= 40:
                        self.speaker_names.add(name)
                if item.get("translation_source") not in {"wahu_exact_id_reviewed", "wahu_verified_source_move"}:
                    continue
                en, vi = str(item.get("official_en") or ""), str(item.get("official_vi") or "")
                key = str(item.get("text_key") or "")
                source = str(item.get("source_json") or "").replace("\\", "/").split("/")[-1].casefold()
                identity = (source, key)
                if not en or not vi or not key:
                    continue
                vi = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", vi, flags=re.I)))
                vi = vi.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
                record = VerifiedText(identity, en, vi, speaker=str(item.get('verified_speaker') or ''))
                if identity in records and records[identity] != record:
                    ambiguous.add(identity)
                else:
                    records[identity] = record
        for identity in ambiguous:
            records.pop(identity, None)
        for identity, record in records.items():
            term = re.fullmatch(r"(Term\d+)_Title", identity[1])
            if term:
                body = records.get((identity[0], term[1] + "_Desc"))
                if body:
                    self.glossaries[(identity[0], term[1])] = (record, body)
            title_match = re.fullmatch(r"(InfoDisplay_\d+)_Title", identity[1])
            if title_match:
                body = records.get((identity[0], title_match[1] + "_Text"))
                if body:
                    self.documents[(identity[0], title_match[1])] = (record, body)
            # Explicit source-family gate, rather than broad provider categories
            # (which also tag many UI strings as story).
            key = identity[1]
            control = normalized(record.english)
            if key.startswith("PrefabTextItem_") and control in {"chat", "leave"}:
                self.choice_controls.setdefault(control, []).append(record)
            if re.search(r"(?:_Name|_Title|_Btn|ButtonText|QuestName|Description)$", key, re.I):
                continue
            if re.match(r"(?:Side_|Main_|Huanglong|Rinascita|Chapter|Activity_|Entity_|PrefabTextItem_|Quest_.*InteractContent|.*(?:Plot|Dialogue|Narration))", key, re.I):
                norm = normalized(record.english)
                if norm:
                    self.exact.setdefault(norm, []).append(record)
                    if record.english.count("{PlayerName}") == 1:
                        self._templates.append(record)
        self.records = records
        self._by_id: dict[str, list[VerifiedText]] = {}
        self._scene_keys: dict[tuple[str, str, int], set[str]] = {}
        self._group_keys: dict[str, set[str]] = {}
        for norm, values in self.exact.items():
            for r in values:
                self._by_id.setdefault(r.identity[1], []).append(r)
                scene = dialogue_scene(r.identity[1])
                if scene:
                    self._scene_keys.setdefault((r.identity[0], scene[0], scene[1]), set()).add(norm)
                    self._group_keys.setdefault(scene[0], set()).add(norm)
        self.story_catalog = story_catalog(r for values in self.exact.values() for r in values)
        self.keys = tuple(self.exact)
        self._memo: dict[tuple[Any, ...], VerifiedText | None] = {}
        # Compile variable-name grammar once, and index by a rare mandatory
        # literal word. Never rebuild thousands of regexes on every OCR miss.
        self._compiled_templates = []
        for record in self._templates:
            before, after = record.english.split('{PlayerName}')
            literal_text = normalized(before+after)
            if len(literal_text)<20 or len(literal_text.split())<4:
                continue
            literal = lambda value: r'\s+'.join(re.escape(w) for w in value.split())
            pattern = literal(before.rstrip())+(r'\s+' if before.endswith(' ') else '')
            pattern += r'(?P<player>[\w][\w .\-]{0,39}?)'+literal(after)
            prefix, suffix = normalized(before).split(), normalized(after).split()
            # Embedded names can join boundary words; do not require those
            # boundary tokens to occur separately in the observed sentence.
            if before[-1:].isalnum():
                prefix = prefix[:-1]
            if after[:1].isalnum():
                suffix = suffix[1:]
            required = frozenset(prefix+suffix)
            self._compiled_templates.append((record,re.compile(pattern,re.I),required))
        frequency = Counter(word for _,_,required in self._compiled_templates for word in required)
        self._template_postings = {}
        self._unanchored_templates = []
        for number,(_,_,required) in enumerate(self._compiled_templates):
            if required:
                anchor = min(required,key=lambda word:(frequency[word],word))
                self._template_postings.setdefault(anchor,[]).append(number)
            else:
                self._unanchored_templates.append(number)

    def valid_context(self, key: str) -> bool:
        if not key:
            return True
        return bool(dialogue_scene(key)
                    and len({r.identity[0] for r in self._by_id.get(key, [])}) == 1)

    def set_story_group(self, group=""):
        if group and group not in self._group_keys:
            return False
        if group != self.story_group:
            self.story_group = group
            hint = self.context_hint
            self.context_hint = None  # Force recomputation even for an empty hint.
            self._scope_signature = object()  # Empty group must also clear old scoped keys.
            self.set_context(hint)
        return True

    def set_context(self, key: str = "") -> bool:
        """Prioritize existing nearby scene records, never synthesize next IDs."""
        if self.context_hint == key:
            return True
        if not self.valid_context(key):
            return False
        candidates = self._by_id.get(key, [])
        families = {r.identity[0] for r in candidates}
        scene = dialogue_scene(key)
        if key and (not scene or len(families) != 1):
            return False
        self.context_hint = key
        signature = ((next(iter(families)), scene[0], scene[1], self.story_group) if scene
                     else ('group',self.story_group) if self.story_group else None)
        if signature == self._scope_signature:
            # Answer and dialogue IDs in the same scene often alternate every
            # frame. Their candidate scope is identical; keep memoized lookup.
            return True
        self._scope_signature = signature
        scope = set(self._group_keys.get(self.story_group, ()))
        if scene:
            for number in range(scene[1]-1, scene[1]+2):
                scope.update(self._scene_keys.get((next(iter(families)), scene[0], number), ()))
        self._scope_keys = tuple(scope)
        self._memo.clear()
        return True

    def _scoped(self, records):
        hint = dialogue_scene(self.context_hint)
        sources = {r.identity[0] for r in self._by_id.get(self.context_hint, [])}
        result = []
        for r in records:
            scene = dialogue_scene(r.identity[1])
            if hint and scene and r.identity[0] in sources and hint[0] == scene[0] and abs(hint[1]-scene[1]) <= 1:
                result.append(r)
        return result or [r for r in records if (scene:=dialogue_scene(r.identity[1])) and scene[0]==self.story_group]

    @staticmethod
    def _unique(matches):
        return matches[0] if matches and len({normalized(r.vietnamese) for r in matches}) == 1 else None

    def _player_template(self, source, kind):
        if kind not in {"dialogue", "choices"}:
            return None
        matches = []
        words = set(normalized(source).split())
        candidates = set(self._unanchored_templates)
        for word in words:
            candidates.update(self._template_postings.get(word,()))
        for number in sorted(candidates):
            record,pattern,required = self._compiled_templates[number]
            if not required.issubset(words):
                continue
            found = pattern.fullmatch(source.strip())
            if found and "{" not in found['player']:
                matches.append(replace(record,
                    vietnamese=record.vietnamese.replace("{PlayerName}", found['player'].strip())))
        return self._unique(matches) or self._unique(self._scoped(matches))

    def resolve(self, source: str, kind: str) -> VerifiedText | None:
        query = normalized(source)
        token = (query, kind, source if self._templates else "", self._scope_signature)
        if token in self._memo:
            found = self._memo[token]
            if found and kind in {'dialogue','choices'}:
                self.set_context(found.identity[1])
            return found
        # An exact sentence elsewhere always wins over a stale scene hint.
        # Otherwise try distinctive scene excerpts before a global fuzzy scan.
        found = self._resolve(query, kind, exact_only=True)
        if not found and query not in self.exact:
            found = self._player_template(source,kind)
        if not found and query not in self.exact:
            excerpt = self._resolve_fragment(query, kind) or self._resolve_clipped(query, kind)
            # A unique excerpt must not outrank an almost-complete, stronger
            # sentence elsewhere. High-cutoff best-only validation, not a
            # full top-two fuzzy ranking, preserves that safety guarantee.
            if excerpt and process.extractOne(query, self.keys, scorer=fuzz.ratio, score_cutoff=90) is None:
                found = excerpt
            else:
                found = self._resolve(query, kind) or excerpt
        if found and kind in {"dialogue", "choices"}:
            self.set_context(found.identity[1])
        if len(self._memo) > 1000:
            self._memo.clear()
        self._memo[token] = found
        return found

    def _resolve(self, query: str, kind: str, *, exact_only=False) -> VerifiedText | None:
        if not query or query.startswith("user id"):
            return None
        short = len(query) < 24 or len(query.split()) < 4
        if short and kind != "choices" and (kind != 'dialogue' or len(query)<12 or len(query.split())<3):
            return None
        matches = self.exact.get(query, [])
        if kind == "choices" and query in self.choice_controls:
            # These are generic interaction-label source identities, not NPC
            # story lines saying the same short word with another translation.
            matches = self.choice_controls[query]
        if matches:
            return self._unique(matches) or (self._unique(self._scoped(matches)) if not short else None)
        if exact_only:
            return None
        if not matches and not short:
            scoped = self._resolve_scope(query)
            if scoped:
                # Existing semantics allow nearby context to resolve a tied
                # global ambiguity, but never to beat a stronger global match.
                # A high-cutoff challenger check is cheaper than ranking the
                # entire database at cutoff 82. Exact global lookup was first.
                score = fuzz.ratio(query, normalized(scoped.english))
                challenger = process.extractOne(query, self.keys, scorer=fuzz.ratio,
                                                score_cutoff=score+1e-7)
                if challenger is None:
                    return scoped
            candidates = process.extract(query, self.keys, scorer=fuzz.ratio, limit=2, score_cutoff=82)
            if not candidates or candidates[0][1] < 90:
                return None
            if len(candidates) > 1 and candidates[0][1] - candidates[1][1] < 8:
                return self._resolve_scope(query, minimum=candidates[0][1])
            key = candidates[0][0]
            if min(len(query), len(key)) / max(len(query), len(key)) < .85:
                return None
            matches = self.exact[key]
        targets = {normalized(r.vietnamese) for r in matches}
        if len(targets) != 1:
            return None
        return matches[0] if matches else None

    def _resolve_fragment(self, query, kind):
        """Exact distinctive fragment in known scene; never infer a successor.

        Global uniqueness prevents a stale scene hint from selecting one of
        several sentences sharing a common fragment. Mark partial geometry;
        the UI uses the calibrated bottom caption viewport separately from
        these genuine visible OCR bounds, without inventing detections.
        """
        if kind != 'dialogue' or not self.context_hint or len(query)<30 or len(query.split())<6:
            return None
        scoped = [key for key in self._scope_keys
                  if (' '+query+' ') in (' '+key+' ') and len(query)/len(key)>=.40]
        if len(scoped)!=1:
            return None
        key = scoped[0]
        if any(other!=key and (' '+query+' ') in (' '+other+' ') for other in self.keys):
            return None
        record = self._unique(self._scoped(self.exact[key]))
        return replace(record,partial=True) if record else None

    def _resolve_scope(self, query, *, minimum=90):
        candidates = process.extract(query, self._scope_keys, scorer=fuzz.ratio, limit=2, score_cutoff=90)
        if not candidates or candidates[0][1] < minimum or (len(candidates)>1 and candidates[0][1]-candidates[1][1]<8):
            return None
        key = candidates[0][0]
        if min(len(query),len(key))/max(len(query),len(key)) < .85:
            return None
        return self._unique(self._scoped(self.exact[key]))

    def _resolve_clipped(self, query, kind):
        """Recover a visible, noisy excerpt using independent exact anchors.

        Only known scene candidates are considered. At least six visible words
        in ordered runs of three must support the match and cover 65% of the
        OCR query. The combination must also be unique globally; scene memory
        is a search hint, not permission to invent the next sentence.
        """
        words = query.split()
        if kind != 'dialogue' or not self.context_hint or len(words) < 6:
            return None
        candidates = []
        for key in self._scope_keys:
            runs = [m for m in SequenceMatcher(None, words, key.split(), autojunk=False).get_matching_blocks() if m.size >= 3]
            count = sum(m.size for m in runs)
            if count < 6 or count / len(words) < .65:
                continue
            anchors = [' '.join(words[m.a:m.a+m.size]) for m in runs]
            candidates.append((key, anchors))
        if len(candidates) != 1:
            return None
        key, anchors = candidates[0]
        if any(other != key and all(' '+anchor+' ' in ' '+other+' ' for anchor in anchors)
               for other in self.keys):
            return None
        record = self._unique(self._scoped(self.exact[key]))
        return replace(record, partial=True) if record else None

    def glossary(self, title, lines):
        """Only paired, verified Term title/description records, not dialogue."""
        anchors = list(dict.fromkeys(normalized(t) for t in lines if len(normalized(t)) >= 30))
        if len(anchors) < 2 or not title:
            return None
        found = []
        for heading, body in self.glossaries.values():
            if fuzz.ratio(normalized(title), normalized(heading.english)) < 90:
                continue
            text = normalized(body.english)
            accepted = [a for a in anchors if a in text or fuzz.partial_ratio(a, text) >= 94]
            if len(accepted) >= 2 and len(accepted) / len(anchors) >= .65:
                found.append((heading, body))
        return found[0] if len(found) == 1 else None

    def document(self, title: str, lines: list[str]) -> AutoDocument | None:
        anchors = list(dict.fromkeys(normalized(line) for line in lines if len(normalized(line)) >= 35))
        if len(anchors) < 2:
            return None
        found = []
        for identity, (heading, body) in self.documents.items():
            if title and fuzz.ratio(normalized(title), normalized(heading.english)) < 90:
                continue
            text = normalized(body.english)
            # Separate OCR lines need to anchor different parts of this document.
            accepted = [line for line in anchors if line in text or fuzz.partial_ratio(line, text) >= 94]
            if len(accepted) >= 2:
                found.append(AutoDocument(identity, heading.vietnamese, body.vietnamese,
                                          source_title=heading.english, source_body=body.english))
                if len(found) > 1:
                    return None
        return found[0] if found else None


def box_bounds(box: dict) -> tuple[int, int, int, int] | None:
    points = box.get("box")
    if box.get("geometry_valid") is False or not isinstance(points, (list, tuple)) or len(points) < 4:
        return None
    try:
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
        x, y, right, bottom = round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))
        return (x, y, right - x, bottom - y) if right > x and bottom > y else None
    except (ValueError, TypeError, IndexError, OverflowError):
        return None


def grouped_rows(boxes: list[dict], *, paragraph: bool = False) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Group wrapped lines, never cross a gap separating two response buttons."""
    rows: list[list[tuple[str, tuple[int, int, int, int]]]] = []
    valid = [(str(b.get("text") or "").strip(), box_bounds(b)) for b in boxes]
    valid = sorted(((text, rect) for text, rect in valid if text and rect), key=lambda v: (v[1][1], v[1][0]))
    # Polygon tops can differ by a few pixels within one physical line. Sorting
    # by y alone scrambles split CPU detections (e.g. the rightmost phrase
    # comes before the leftmost). Cluster overlapping baselines, then read x.
    baselines = []
    for item in valid:
        rect = item[1]
        if baselines:
            previous = baselines[-1][0][1]
            overlap = min(rect[1]+rect[3], previous[1]+previous[3])-max(rect[1], previous[1])
            if overlap >= min(rect[3], previous[3])*.5:
                baselines[-1].append(item)
                continue
        baselines.append([item])
    valid = [item for line in baselines for item in sorted(line, key=lambda v: v[1][0])]
    for text, rect in valid:
        if rows:
            last = rows[-1][-1][1]
            gap = rect[1] - last[1] - last[3]
            if paragraph or (gap <= min(last[3], rect[3]) * .9 and abs(rect[0] - last[0]) < max(60, rect[3] * 3)):
                rows[-1].append((text, rect))
                continue
        rows.append([(text, rect)])
    result = []
    for row in rows:
        x = min(r[0] for _, r in row); y = min(r[1] for _, r in row)
        right = max(r[0] + r[2] for _, r in row); bottom = max(r[1] + r[3] for _, r in row)
        result.append((" ".join(text for text, _ in row), (x, y, right - x, bottom - y)))
    return result


def is_black_scene(image) -> bool:
    sample = image.convert("RGB").resize((64, 36))
    pixels = list(sample.get_flattened_data() if hasattr(sample, "get_flattened_data") else sample.getdata())
    return sum(max(pixel) < 28 for pixel in pixels) / len(pixels) > .95


def choice_border(image, bounds) -> bool:
    """Require a light vertical button border to the left of the text/icon."""
    x, y, width, height = bounds
    scale = image.width / 1920
    rgb = image.convert("RGB")
    top = max(0, round(y - 8 * scale)); bottom = min(image.height, round(y + height + 8 * scale))
    for col in range(max(0, round(x - 85 * scale)), max(1, round(x - 18 * scale)), max(1, round(2 * scale))):
        # Unselected buttons have a gray border, not the selected white one.
        # Require a neutral edge with a darker interior, rather than lowering
        # brightness alone (which would admit scenery/HUD stripes).
        count = 0
        for row in range(top, bottom):
            edge = rgb.getpixel((col, row))
            inside = rgb.getpixel((min(image.width-1, col+max(4, round(6*scale))), row))
            count += min(edge)>125 and max(edge)-min(edge)<50 and min(edge)-max(inside)>45
        if count >= (bottom - top) * .40:
            return True
    return False


def dialogue_chrome(image) -> bool:
    """Require WuWa's centered gold speaker label above the dialogue band.

    Database matches alone must not turn a bottom HUD message into dialogue.
    Unknown layouts without this cue deliberately remain untranslated.
    """
    sample = image.convert("RGB").resize((960, 540))
    rows = 0
    for y in range(round(540*.74), round(540*.795)):
        gold = sum(r > 160 and g > 135 and b < 145 and r > b*1.25 and g > b*1.1
                   for r, g, b in (sample.getpixel((x,y)) for x in range(336,624)))
        rows += gold >= 8
    return rows >= 7


def walking_chrome(image) -> bool:
    """A separate gold speaker cue above roaming captions, not the HUD band.

    This cue alone never authorizes translation: process also requires a real
    OCR rectangle and an exact English speaker name in the game database.
    """
    import numpy as np
    sample = image.convert('RGB').resize((960,540))
    left,top=round(960*.36),round(540*.66)
    pixels=np.asarray(sample.crop((left,top,round(960*.64),round(540*.725)))).astype('int16')
    gold=(pixels[:,:,0]>160)&(pixels[:,:,1]>135)&(pixels[:,:,2]<145)&(pixels[:,:,0]>pixels[:,:,2]*1.25)
    # Distinguish small separated letter strokes from gold scenery patches.
    # The ROI is only ~9k pixels; no detector model/OpenCV dependency needed.
    seen=np.zeros(gold.shape,dtype=bool)
    glyphs=[]
    for yy,xx in zip(*np.nonzero(gold)):
        if seen[yy,xx]:
            continue
        seen[yy,xx]=True
        pending=[(int(xx),int(yy))]; points=[]
        while pending:
            x,y=pending.pop(); points.append((x,y))
            for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if 0<=ny<gold.shape[0] and 0<=nx<gold.shape[1] and gold[ny,nx] and not seen[ny,nx]:
                    seen[ny,nx]=True; pending.append((nx,ny))
        x0=min(x for x,y in points); x1=max(x for x,y in points)
        y0=min(y for x,y in points); y1=max(y for x,y in points)
        w,h=x1-x0+1,y1-y0+1
        if 2<=w<=25 and 8<=h<=24 and .20<=len(points)/(w*h)<=.85:
            glyphs.append((x0,y0,w,h))
    for glyph in glyphs:
        aligned=[r for r in glyphs if abs(r[1]-glyph[1])<=7 and abs(r[3]-glyph[3])<=8]
        if len(aligned)<2:
            continue
        x0=min(r[0] for r in aligned); x1=max(r[0]+r[2] for r in aligned)
        if x1-x0<=960*.25 and abs(left+(x0+x1)/2-480)<960*.08:
            return True
    return False


class AutoWuwaProcessor:
    """One shared-worker invocation per frame, with bounded ROI OCR caching."""
    def __init__(self, index: AutoTextIndex, fallback=None):
        self.index = index
        self.fallback = fallback
        self._ocr_cache: dict[str, tuple[tuple, list[dict], float]] = {}
        self._context = None
        self._next_discovery = 0.0
        self._tracked: set[str] = set()
        self._lost: dict[str, int] = {}
        self._was_black = False
        self._configured_hint = None
        self._dialogue_visual = None
        self._next_information = 0.0
        self.last_timings = {}

    def _recognize_geometry(self, engine, crop, backend):
        started = time.perf_counter()
        flags = ("win_ocr_adaptive", "win_ocr_refine", "win_ocr_line_refine", "win_ocr_multiscale", "win_ocr_preprocess")
        previous = {key: getattr(engine, key) for key in flags if hasattr(engine, key)}
        try:
            for key in previous:
                setattr(engine, key, False)
            return engine.recognize_pipeline(crop, backend=backend).boxes
        finally:
            self.last_timings['windows_ocr_ms'] = self.last_timings.get('windows_ocr_ms', 0)+1000*(time.perf_counter()-started)
            self.last_timings['windows_calls'] = self.last_timings.get('windows_calls', 0)+1
            for key, value in previous.items():
                setattr(engine, key, value)

    def _lookup(self, method, *args):
        started = time.perf_counter()
        try:
            return method(*args)
        finally:
            self.last_timings['lookup_ms'] = self.last_timings.get('lookup_ms', 0)+1000*(time.perf_counter()-started)

    def _rescue_dialogue(self, image, boxes, engine, backend, band):
        """At most two contrast retries; map actual OCR rectangles to pixels.

        WuWa's pale glyphs on bright scenery can disappear in raw WinRT OCR.
        A color mask removes the dark outline/scenery without reconstructing or
        predicting letters. Retrieval must still validate the resulting text.
        """
        import numpy as np
        from PIL import Image
        if boxes:
            rects = [box_bounds(b) for b in boxes]
            pad = max(8, round(image.height/1080*18))
            # Failed OCR may only detect the last few words. Retry the complete
            # horizontal dialogue band, not that truncated OCR rectangle.
            left=band[0]
            top=max(band[1],min(r[1] for r in rects)-pad)
            right=band[2]
            bottom=min(band[3],max(r[1]+r[3] for r in rects)+pad)
        else:
            left,top,right,bottom=band
        crop=image.crop((left,top,right,bottom)).convert('RGB')
        pixels=np.asarray(crop).astype('int16')
        for threshold in (210, 190):
            self.last_timings['contrast_calls'] = self.last_timings.get('contrast_calls', 0)+1
            pale=(pixels[:,:,0]>threshold)&(pixels[:,:,1]>threshold-10)&(pixels[:,:,2]>threshold-40)&(pixels.max(2)-pixels.min(2)<75)
            prepared=Image.fromarray(np.where(pale,0,255).astype('uint8'))
            scale=min(2.,2500/max(prepared.size))
            prepared=prepared.resize((round(prepared.width*scale),round(prepared.height*scale)),Image.Resampling.LANCZOS)
            sx,sy=prepared.width/crop.width,prepared.height/crop.height
            output=[]
            for raw in self._recognize_geometry(engine,prepared,backend):
                rect=box_bounds(raw)
                if not rect:
                    continue
                x,y,w,h=rect
                if x<0 or y<0 or x+w>prepared.width+2 or y+h>prepared.height+2:
                    continue
                x,y,w,h=round(x/sx+left),round(y/sy+top),round(w/sx),round(h/sy)
                output.append({'text':raw.get('text'),'box':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]})
            rows=grouped_rows(output,paragraph=True)
            if any(self._lookup(self.index.resolve,text,'dialogue') for text,_ in rows):
                return output
        return boxes

    @staticmethod
    def _glyph_mask(image, bounds):
        """Pale ink near a dark edge, excluding uniform bright scenery."""
        import numpy as np
        from PIL import ImageFilter
        x,y,w,h = bounds
        crop = image.crop((x,y,x+w,y+h)).convert('RGB')
        pixels = np.asarray(crop).astype('int16')
        gray = np.asarray(crop.convert('L')).astype('int16')
        edge = gray - np.asarray(crop.convert('L').filter(ImageFilter.MinFilter(5))).astype('int16')
        return ((pixels[:,:,0]>190)&(pixels[:,:,1]>180)&(pixels[:,:,2]>150)
                &(pixels.max(2)-pixels.min(2)<75)&(edge>35))

    def _stabilize_dialogue(self, result, image):
        """Hold only verified text with matching visible ink and WuWa chrome.

        The reference is never refreshed by failed OCR, so a changing caption
        cannot drift indefinitely while retaining an old translation.
        """
        import numpy as np
        current = tuple(b for b in result.blocks if b.kind == 'dialogue')
        previous = self._dialogue_visual[0] if self._dialogue_visual else ()
        caption_mode = (current or previous)[0].caption_mode if current or previous else 'cinematic'
        chrome = walking_chrome(image) if caption_mode == 'walking' else dialogue_chrome(image)
        if result.document_present or 'glossary' in result.present_kinds or not chrome:
            self._dialogue_visual = None
            return result
        if current:
            old = self._dialogue_visual
            if old and len(current)==len(old[0]):
                tolerance = max(1, round(image.height/1080*3))
                current = tuple(replace(b, bounds=previous.bounds)
                    if b.identity==previous.identity and b.target==previous.target and b.caption_mode==previous.caption_mode
                    and all(abs(a-c)<=tolerance for a,c in zip(b.bounds,previous.bounds)) else b
                    for b,previous in zip(current,old[0]))
                result = replace(result, blocks=tuple(b for b in result.blocks if b.kind!='dialogue')+current)
            self._dialogue_visual = (current, tuple(self._glyph_mask(image,b.bounds) for b in current))
        elif self._dialogue_visual:
            old, masks = self._dialogue_visual
            same = True
            for block, reference in zip(old, masks):
                ink = self._glyph_mask(image, block.bounds)
                union = np.count_nonzero(ink | reference)
                same &= (np.count_nonzero(reference)>=max(30, reference.size*.01)
                         and union>0 and np.count_nonzero(ink & reference)/union>=.78)
            if same:
                result = replace(result, stable_kinds=('dialogue',))
            else:
                self._dialogue_visual = None
        return result

    def _track(self, result: AutoFrameResult, image=None) -> AutoFrameResult:
        if image is not None:
            result = self._stabilize_dialogue(result, image)
        previously_tracked = bool(self._tracked)
        current = set(result.present_kinds) | set(result.stable_kinds)
        if result.document_present:
            current.add("document")
        for kind in self._tracked | current:
            self._lost[kind] = 0 if kind in current else self._lost.get(kind, 0) + 1
        self._tracked |= current
        self._tracked = {k for k in self._tracked if self._lost.get(k, 0) < 2}
        if previously_tracked and not self._tracked:
            self._next_discovery = 0
        return result

    def _fallback_dialogue(self, image, band, boxes):
        crop=image.crop(band)
        output=[]
        started = time.perf_counter()
        try:
            recognized = self.fallback.recognize(crop)
        finally:
            self.last_timings['cpu_ocr_ms'] = self.last_timings.get('cpu_ocr_ms', 0)+1000*(time.perf_counter()-started)
            self.last_timings['cpu_calls'] = self.last_timings.get('cpu_calls', 0)+1
        for raw in recognized:
            bounds=box_bounds(raw)
            if not bounds:
                continue
            x,y,w,h=bounds
            if x<0 or y<0 or x+w>crop.width+2 or y+h>crop.height+2:
                continue
            x+=band[0]; y+=band[1]
            output.append({'text':raw.get('text'),'box':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]})
        rows=grouped_rows(output,paragraph=True)
        return output if any(self._lookup(self.index.resolve,text,'dialogue') for text,_ in rows) else boxes

    def _glossary_frame(self, boxes, width):
        valid = sorted((b for b in boxes if box_bounds(b)), key=lambda b: box_bounds(b)[1])
        blocks = []
        evidence = []
        if valid:
            heading = valid[0]
            hx, hy, hw, hh = box_bounds(heading)
            body = [b for b in valid[1:] if box_bounds(b)[1] >= hy+hh
                    and abs(box_bounds(b)[0]-hx) <= width*.04]
            title = str(heading.get('text') or '')
            evidence = [('glossary_title', title)] + [('glossary', str(b.get('text') or '')) for b in body]
            found = self._lookup(self.index.glossary,title, [str(b.get('text') or '') for b in body])
            if found:
                title_record, body_record = found
                rows = grouped_rows(body, paragraph=True)
                if rows:
                    blocks = [AutoTextBlock('glossary', (hx,hy,hw,hh), title, title_record.vietnamese, title_record.identity, 0),
                              AutoTextBlock('glossary', rows[0][1], rows[0][0], body_record.vietnamese, body_record.identity, 1)]
        diagnostic = () if blocks else ('Glossary chưa xác nhận được cặp tiêu đề/nội dung; không che chữ gốc.',)
        return AutoFrameResult(blocks=tuple(blocks), diagnostics=diagnostic,
                               detected_kinds=('glossary',), present_kinds=('glossary',), observed_texts=tuple(evidence))

    def process(self, image, engine, settings: dict, backend="windows", *, force=False) -> AutoFrameResult:
        started = time.perf_counter()
        self.last_timings = {'windows_ocr_ms':0.0, 'cpu_ocr_ms':0.0, 'lookup_ms':0.0,
                             'windows_calls':0, 'contrast_calls':0, 'cpu_calls':0}
        try:
            return self._process_frame(image, engine, settings, backend, force=force)
        finally:
            self.last_timings['process_ms'] = 1000*(time.perf_counter()-started)

    def _process_frame(self, image, engine, settings: dict, backend="windows", *, force=False) -> AutoFrameResult:
        if isinstance(image, tuple) and len(image) == 3:
            from PIL import Image
            raw, raw_width, raw_height = image
            image = Image.frombytes("RGB", (raw_width, raw_height), raw, "raw", "BGRX")
        width, height = image.size
        observed: dict[str, list[dict]] = {}
        now = time.monotonic()
        black = is_black_scene(image)
        context = (image.size, settings.get("generation"), repr(settings.get("bounds")),
                   tuple(bool(settings.get(k, True)) for k in ("document", "glossary", "dialogue", "walking", "choices", "transition", "light_ocr")))
        if context != self._context:
            self._context = context; self._next_discovery = 0; self._tracked.clear(); self._lost.clear(); self._ocr_cache.clear()
            self._dialogue_visual = None
            self._next_information = 0.0
            self.index.set_context("")
            self._configured_hint = None
        hint = str(settings.get("context_hint") or "")
        self.index.set_story_group(str(settings.get('story_group') or ''))
        if hint != self._configured_hint:
            self.index.set_context(hint)
            self._configured_hint = hint
        wide = force or now >= self._next_discovery or black != self._was_black
        self._was_black = black
        if wide:
            self._next_discovery = now + 1.5
        cinematic = not black and settings.get('dialogue', True) and dialogue_chrome(image)
        walking = not black and not cinematic and settings.get('dialogue',True) and settings.get('walking',True) and walking_chrome(image)
        walking_confirmed = False
        walking_named = False
        speaker = cinematic or walking
        # During a known dialogue, expensive document discovery runs less
        # often. Losing the speaker immediately unlocks full discovery.
        information = force or 'document' in self._tracked or not (speaker and 'dialogue' in self._tracked) or now >= self._next_information
        if information and wide:
            self._next_information = now + 3.0
        if not speaker and 'dialogue' in self._tracked:
            wide = True
        kinds = ["transition"] if black else ["glossary_label", "glossary", "title", "document", "choices", "walking_speaker", "walking_dialogue", "dialogue"]
        for kind in kinds:
            control = 'dialogue' if kind.startswith('walking_') else 'glossary' if kind == 'glossary_label' else 'document' if kind == 'title' else kind
            if kind.startswith('walking_') and not walking:
                continue
            if kind == 'dialogue' and walking:
                continue
            urgent = speaker and kind in {'dialogue','choices','glossary_label','glossary','walking_speaker','walking_dialogue'}
            if not force and kind in {'title','document'} and not information:
                continue
            if not wide and not urgent and control not in self._tracked:
                continue
            if not settings.get(control, True):
                continue
            if kind == 'glossary' and not any(fuzz.ratio(normalized(str(b.get('text') or '')), 'glossary') >= 92
                                             for b in observed.get('glossary_label', [])):
                continue
            fx, fy, fw, fh = SEARCH_BANDS[kind]
            crop_rect = (round(fx * width), round(fy * height), round((fx + fw) * width), round((fy + fh) * height))
            crop = image.crop(crop_rect)
            signature = (crop.size, hashlib.blake2b(crop.tobytes(), digest_size=16).digest())
            cached = self._ocr_cache.get(kind)
            if not force and cached and cached[0] == signature:
                boxes = cached[1]
            else:
                # Legacy text refinement may replace a line's geometry with a
                # crop-local rectangle. Inline translation needs original,
                # unmodified WinRT word rectangles, not that text-only rescue.
                boxes = []
                for raw in self._recognize_geometry(engine, crop, backend):
                    bounds = box_bounds(raw)
                    if not bounds:
                        continue
                    x, y, w, h = bounds
                    if x < 0 or y < 0 or x + w > crop.width + 2 or y + h > crop.height + 2:
                        continue
                    x += crop_rect[0]; y += crop_rect[1]
                    boxes.append({"text": raw.get("text"), "box": [[x,y],[x+w,y],[x+w,y+h],[x,y+h]]})
                if kind == 'walking_speaker' and backend == 'windows' and not any(
                        normalized(text) in self.index.speaker_names for text,_ in grouped_rows(boxes,paragraph=True)):
                    # Roaming speaker labels are gold on moving scenery. Read
                    # actual gold pixels, not scenery characters near the name.
                    import numpy as np
                    from PIL import Image
                    pixels=np.asarray(crop.convert('RGB')).astype('int16')
                    gold=(pixels[:,:,0]>160)&(pixels[:,:,1]>135)&(pixels[:,:,2]<145)&(pixels[:,:,0]>pixels[:,:,2]*1.25)
                    prepared=Image.fromarray(np.where(gold,0,255).astype('uint8')).resize((crop.width*2,crop.height*2))
                    output=[]
                    for raw in self._recognize_geometry(engine,prepared,backend):
                        rect=box_bounds(raw)
                        if not rect:
                            continue
                        x,y,w,h=rect
                        if x<0 or y<0 or x+w>prepared.width+2 or y+h>prepared.height+2:
                            continue
                        x,y,w,h=round(x/2+crop_rect[0]),round(y/2+crop_rect[1]),round(w/2),round(h/2)
                        output.append({'text':raw.get('text'),'box':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]})
                    if any(normalized(text) in self.index.speaker_names for text,_ in grouped_rows(output,paragraph=True)):
                        boxes=output
                if kind in {'dialogue','walking_dialogue'} and backend == 'windows' and speaker:
                    rows=grouped_rows(boxes,paragraph=True)
                    if not any(self._lookup(self.index.resolve,text,'dialogue') for text,_ in rows):
                        try:
                            boxes=self._rescue_dialogue(image,boxes,engine,backend,crop_rect)
                        except Exception:
                            # This optional contrast pass must not discard a
                            # successful primary capture or stop the scheduler.
                            pass
                    if self.fallback and settings.get('light_ocr',True):
                        resolved=[self._lookup(self.index.resolve,text,'dialogue') for text,_ in grouped_rows(boxes,paragraph=True)]
                        if not any(resolved):
                            try:
                                boxes=self._fallback_dialogue(image,crop_rect,boxes)
                            except Exception:
                                pass
                self._ocr_cache[kind] = (signature, boxes, now)
            observed[kind] = boxes
            if kind == 'walking_speaker':
                walking_named = any(normalized(text) in self.index.speaker_names
                    and abs(rect[0]+rect[2]/2-width/2)<width*.08
                    and rect[2]<width*.25 for text,rect in grouped_rows(boxes,paragraph=True))
                walking_confirmed = walking_named
            elif kind == 'walking_dialogue':
                observed['dialogue'] = boxes
                # A custom player name need not OCR as the database's "Rover".
                # Gold speaker-layout evidence permits reading this band, not
                # translating it blindly. Require a verified player-role line
                # and a complete match; context-only fragments cannot bypass
                # the unknown-speaker gate or turn HUD labels into dialogue.
                if not walking_confirmed:
                    walking_confirmed = any(record and not record.partial
                        and normalized(record.speaker) == 'rover'
                        for text,_ in grouped_rows(boxes,paragraph=True)
                        for record in [self._lookup(self.index.resolve,text,'dialogue')])
            if kind == 'choices':
                # A verified response in this very frame can establish scene
                # context before contrast/CPU retries of the clipped dialogue.
                for text, bounds in grouped_rows(boxes):
                    if choice_border(image, bounds):
                        self._lookup(self.index.resolve,text, 'choices')
            if kind == 'glossary':
                # Modal priority even if its translation is unavailable: never
                # paint old dialogue/buttons across the information popup.
                return self._track(self._glossary_frame(boxes, width), image)
        diagnostics = []
        document_boxes = observed.get("document", [])
        title = " ".join(t for t, _ in grouped_rows(observed.get("title", []), paragraph=True))
        # A document-shaped central body must have several substantial lines.
        document_present = len([b for b in document_boxes if len(normalized(str(b.get("text")))) >= 35]) >= 3
        document = self._lookup(self.index.document,title, [str(b["text"]) for b in document_boxes]) if document_present else None
        document_evidence = (("document_title", title),) + tuple(("document", str(b["text"])) for b in document_boxes)
        if document:
            return self._track(AutoFrameResult(document=document, document_present=True, detected_kinds=("document",), present_kinds=("document",), observed_texts=document_evidence), image)
        if document_present:
            diagnostics.append("Bảng thông tin chưa xác nhận được tiêu đề/nội dung; không che chữ gốc.")
        blocks = []; detected = []; present = []; texts = []
        for kind in ("choices", "dialogue", "transition"):
            if kind == "transition" and not black:
                continue
            if kind == "dialogue" and not (cinematic or walking_confirmed):
                if observed.get(kind):
                    diagnostics.append("Có chữ trong dải hội thoại nhưng chưa thấy nhãn người nói của WuWa; kiểm tra nguồn ảnh/khung game.")
                    texts.extend(("dialogue_unconfirmed", str(b.get("text", ""))) for b in observed[kind])
                continue
            rows = grouped_rows(observed.get(kind, []), paragraph=kind != "choices")
            for order, (text, bounds) in enumerate(rows, 1):
                if kind == "choices" and not choice_border(image, bounds):
                    continue
                if kind != "choices" and (len(normalized(text)) < (12 if kind=='dialogue' else 24) or abs(bounds[0] + bounds[2] / 2 - width / 2) > width * .22):
                    continue
                present.append(kind)
                detected.append(kind)
                texts.append((kind, text))
                record = self._lookup(self.index.resolve,text, kind)
                if (record and kind=='dialogue' and walking
                        and not walking_named
                        and (record.partial or normalized(record.speaker) != 'rover')):
                    record = None
                if record:
                    blocks.append(AutoTextBlock(kind, bounds, text, record.vietnamese, record.identity, order, record.partial,
                                                'walking' if kind=='dialogue' and walking_confirmed else 'cinematic'))
                else:
                    diagnostics.append(f"{kind}: không có bản Việt khớp chắc chắn cho “{text[:90]}”.")
        return self._track(AutoFrameResult(tuple(blocks), document, document_present, tuple(diagnostics), tuple(sorted(set(detected))), tuple(sorted(set(present))), tuple(texts) + document_evidence), image)


class AutoPresenceTracker:
    """Expire vanished content after two observations; never expire on errors."""
    def __init__(self):
        self.blocks: dict[str, tuple[AutoTextBlock, ...]] = {}
        self.misses: dict[str, int] = {}
        self.document: AutoDocument | None = None
        self.document_misses = 0

    def reset(self):
        self.blocks.clear(); self.misses.clear(); self.document = None; self.document_misses = 0

    def update(self, result: AutoFrameResult) -> AutoFrameResult:
        if 'glossary' in result.present_kinds:
            self.blocks = {k:v for k,v in self.blocks.items() if k == 'glossary'}
            self.document = None
        elif result.present_kinds or result.document_present:
            self.blocks.pop('glossary', None)
        for kind in ("choices", "dialogue", "transition", "glossary"):
            current = tuple(b for b in result.blocks if b.kind == kind)
            if current:
                self.blocks[kind] = current; self.misses[kind] = 0
            elif kind in result.stable_kinds:
                self.misses[kind] = 0
            elif kind in result.present_kinds:
                # A different unresolved sentence must not retain a previous
                # translation. Only preserve if recognized text is unchanged.
                old = self.blocks.get(kind, ())
                same = old and all(any(k == kind and fuzz.ratio(normalized(block.source), normalized(text)) >= 85
                                       for k, text in result.observed_texts) for block in old)
                if not same:
                    self.blocks.pop(kind, None)
                self.misses[kind] = 0
            else:
                self.misses[kind] = self.misses.get(kind, 0) + 1
                if self.misses[kind] >= 2:
                    self.blocks.pop(kind, None)
        if result.document:
            self.document = result.document; self.document_misses = 0
        elif result.document_present:
            if self.document:
                title = next((text for kind, text in result.observed_texts if kind == "document_title"), "")
                anchors = [normalized(text) for kind, text in result.observed_texts if kind == "document" and len(normalized(text)) >= 35]
                title_changed = title and self.document.source_title and fuzz.ratio(normalized(title), normalized(self.document.source_title)) < 85
                body_changed = len(anchors) >= 2 and self.document.source_body and not any(fuzz.partial_ratio(anchor, normalized(self.document.source_body)) >= 85 for anchor in anchors)
                if title_changed or body_changed:
                    self.document = None
            self.document_misses = 0
        else:
            self.document_misses += 1
            if self.document_misses >= 2:
                self.document = None
        return AutoFrameResult(tuple(b for group in self.blocks.values() for b in group), self.document,
                               result.document_present or self.document is not None, result.diagnostics,
                               result.detected_kinds, result.present_kinds, result.observed_texts, result.stable_kinds)
