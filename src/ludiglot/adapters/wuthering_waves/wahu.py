from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import re
from typing import Any, Mapping
import unicodedata


@dataclass(frozen=True)
class WahuMergeStats:
    official_entries: int = 0
    exact_id_matches: int = 0
    vietnamese_matches: int = 0
    english_fallbacks: int = 0
    missing_entries: int = 0
    incompatible_source_entries: int = 0
    verified_source_moves: int = 0


class WahuTranslationProvider:
    """Read WAHU translations without modifying its SQLite database.

    WAHU's ``entries`` table intentionally exposes only unambiguous
    ``(dbfile, key)`` identities.  Ambiguous rows remain in ``entries_full``
    and are not guessed here.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        canonical_translations: Mapping[tuple[str, str], str] | None = None,
        require_source_english: bool = False,
    ) -> None:
        self.database_path = Path(database_path)
        self.canonical_translations = canonical_translations
        self.require_source_english = require_source_english
        self._source_english: dict[tuple[str, str], str] = {}
        self._source_speakers: dict[tuple[str, str], str] = {}
        self._source_moves: dict[tuple[str, str], tuple[str, str, tuple[str, str]]] = {}
        self._release_source_proofs = {}

    def load_exact_translations(self) -> dict[tuple[str, str], str]:
        if not self.database_path.is_file():
            raise FileNotFoundError(f"WAHU database not found: {self.database_path}")

        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(entries)")}
            source_column = "en" if "en" in columns else "''"
            speaker_column = "speaker" if "speaker" in columns else "''"
            rows = connection.execute(
                f"SELECT dbfile, key, vi, {source_column}, {speaker_column} FROM entries "
                "WHERE trim(coalesce(vi, '')) <> '' "
                "AND lower(trim(coalesce(status, ''))) IN "
                "('translated', 'final', 'edited', 'reviewed')"
            )
            translations: dict[tuple[str, str], str] = {}
            self._source_english = {}
            self._source_speakers = {}
            duplicate: set[tuple[str, str]] = set()
            for dbfile, text_key, vietnamese, english, speaker in rows:
                identity = (str(dbfile).casefold(), str(text_key))
                if identity in translations or identity in duplicate:
                    duplicate.add(identity)
                    translations.pop(identity, None)
                    self._source_english.pop(identity, None)
                    self._source_speakers.pop(identity, None)
                    continue
                if self.canonical_translations is not None:
                    vietnamese = self.canonical_translations.get(identity, "")
                translations[identity] = str(vietnamese or "")
                self._source_english[identity] = str(english or "")
                self._source_speakers[identity] = str(speaker or "")
            self._source_moves = self._load_source_moves(connection, translations)
            self._release_source_proofs = {}
            proof_columns={r[1] for r in connection.execute('PRAGMA table_info(release_source_proofs)')}
            if {'dbfile','key','source_dbfile','tbl','version','en','cn','vi'}<=proof_columns:
                for dbfile,key,source,table,version,en,cn,vi in connection.execute('SELECT dbfile,key,source_dbfile,tbl,version,en,cn,vi FROM release_source_proofs'):
                    identity=(str(dbfile).casefold(),str(key))
                    if (source in {'lang_multi_text_1sthalf.db','lang_multi_text_2ndhalf.db'}
                            and identity[0]=='lang_multi_text.db' and table=='MultiText'
                            and re.fullmatch(r'\d+\.\d+',str(version))
                            and en and cn and '*' not in en and '*' not in cn
                            and _source_text(translations.get(identity,''))==_source_text(str(vi))
                            and _source_text(self._source_english.get(identity,''))==_source_text(str(en))):
                        self._release_source_proofs[identity]=(str(cn),table,source)
                    else:
                        translations.pop(identity,None)
            for identity, (english, vietnamese, origin) in self._source_moves.items():
                translations[identity] = vietnamese
                self._source_english[identity] = english
                self._source_speakers[identity] = self._source_speakers.get(origin, "")
            return translations

    def _load_source_moves(self, connection, translations):
        """Recover a split-file move only from WAHU's recorded original text.

        Matching IDs or mask lengths are NOT evidence. The same source-sync
        event must record the real English/Chinese before masking, the exact
        current masks and the reviewed translation, and a unique destination
        must retain the original Chinese and equivalent English. The only
        English repairs allowed are explicit grammar variants or a unique
        reviewed short terminology pair from the same translation database.
        """
        if self.canonical_translations is None:
            meta_columns = {r[1] for r in connection.execute("PRAGMA table_info(meta)")}
            if not {"k", "v"} <= meta_columns:
                return {}
            mode = connection.execute("SELECT v FROM meta WHERE k='name_mode'").fetchone()
            if not mode or str(mode[0]).casefold() != "en":
                return {}
        required = {
            "entries_full": {"dbfile", "tbl", "key", "cn", "en", "vi", "status", "conv", "version",
                             "source_pair_count", "legacy_origin", "sync_confidence", "synced_at"},
            "source_sync_changes": {"dbfile", "tbl", "key", "old_cn", "old_en", "new_cn", "new_en", "vi_at_sync", "synced_at"},
        }
        for table, columns in required.items():
            if not columns <= {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}:
                return {}
        aliases = self._terminology_aliases(connection, translations)
        rows = connection.execute("""
            SELECT a.dbfile, a.key, a.vi, a.en, a.cn, a.conv, a.version,
                   s.old_en, s.old_cn, s.new_en, s.new_cn, s.vi_at_sync,
                   b.dbfile, b.en, b.cn, b.vi, b.conv, b.version,
                   a.source_pair_count, b.source_pair_count,
                   a.legacy_origin, b.legacy_origin, a.sync_confidence, b.sync_confidence
            FROM source_sync_changes s
            JOIN entries_full a ON a.dbfile=s.dbfile AND a.tbl=s.tbl AND a.key=s.key AND a.synced_at=s.synced_at
            JOIN entries_full b ON b.key=a.key AND b.tbl=a.tbl
            WHERE a.dbfile='lang_multi_text.db' AND a.tbl='MultiText'
              AND s.new_en LIKE '*%' AND s.old_en<>s.new_en AND s.old_cn<>s.new_cn
              AND b.dbfile IN ('lang_multi_text_1sthalf.db', 'lang_multi_text_2ndhalf.db')
              AND lower(trim(a.status)) IN ('translated','final','edited','reviewed')
              AND trim(coalesce(a.vi,''))<>''
        """)
        candidates = {}
        for row in rows:
            (old_file, key, vi, en, cn, conv, version, old_en, old_cn, new_en, new_cn, sync_vi,
             target_file, target_en, target_cn, target_vi, target_conv, target_version,
             old_count, target_count, old_origin, target_origin, old_conf, target_conf) = row
            origin, destination = (old_file.casefold(), key), (target_file.casefold(), key)
            if destination in translations or str(target_vi or '').strip():
                continue
            if origin not in translations or not re.fullmatch(r'(?:Main|Side|GNNPC)_.+_\d+_\d+', key):
                continue
            if not conv or conv != target_conv or not version or version != target_version:
                continue
            if (old_count, target_count, old_origin, target_origin, old_conf, target_conf) != (
                    1, 1, 'ACTIVE_UNAMBIGUOUS', 'ACTIVE_UNAMBIGUOUS', 'EXACT_PAIR', 'EXACT_PAIR'):
                continue
            if not re.fullmatch(r'\*+', str(en or '')) or not re.fullmatch(r'\*+', str(cn or '')):
                continue
            if (en, cn, vi) != (new_en, new_cn, sync_vi) or not old_en or not old_cn:
                continue
            if '*' in old_en or '*' in old_cn:
                continue
            if (_source_text(old_cn) != _source_text(target_cn or '')
                    or not (_equivalent_english(old_en, target_en or '', aliases)
                            or _reviewed_source_rephrasing(key, old_cn, old_en, target_en or ''))):
                continue
            # Canonical English-name targets, when required, still come from
            # the reviewed source identity, never an unreviewed destination.
            target = translations[origin]
            if target:
                candidates.setdefault(destination, set()).add((target_en, target, origin))
        # Multiple proof rows with identical contents are harmless; competing
        # translations/sources are not. Even the two split files must not
        # offer competing destinations for one original identity.
        origins = {}
        for destination, values in candidates.items():
            for _, _, origin in values:
                origins.setdefault(origin, set()).add(destination)
        return {k: next(iter(v)) for k, v in candidates.items()
                if len(v) == 1 and len(origins[next(iter(v))[2]]) == 1}

    def _terminology_aliases(self, connection, translations):
        """Unique reviewed term names, not sentence translations/fuzzy aliases."""
        choices = {}
        for dbfile, key, en, vi in connection.execute(
                "SELECT dbfile,key,en,vi FROM entries WHERE trim(coalesce(vi,''))<>'' "
                "AND lower(trim(status)) IN ('translated','final','edited','reviewed')"):
            if (dbfile.casefold(), key) not in translations:
                continue
            if not (str(key).startswith('Entity_') or re.search(r'_(?:Name|Title)$', str(key))):
                continue
            en, vi = _source_text(str(en or '')), _source_text(str(vi or ''))
            if (not 2 <= len(en) <= 40 or not 2 <= len(vi) <= 40
                    or len(en.split()) > 4 or len(vi.split()) > 4
                    or not re.fullmatch(r'[A-Za-z]+(?: [A-Za-z]+)*', en)
                    or vi.isascii() or not all(c.isalpha() or c==' ' for c in vi)):
                continue
            # A Vietnamese term accidentally embedded in an English source
            # may retain English plural '-s'. Both forms must stay unique.
            for alias in (vi, vi+'s'):
                choices.setdefault(alias, set()).add(en if alias==vi else en+'s')
        return {alias: next(iter(values)) for alias, values in choices.items() if len(values)==1}

    def enrich(self, database: dict[str, dict[str, Any]], *, source_moves_only: bool = False) -> WahuMergeStats:
        translations = self.load_exact_translations()
        seen: set[int] = set()
        counted: set[tuple[str, str, str, str]] = set()
        official_entries = exact_id_matches = vietnamese_matches = 0
        english_fallbacks = missing_entries = 0
        incompatible_source_entries = 0
        verified_source_moves = 0

        for payload in database.values():
            for match in payload.get("matches", []):
                identity = id(match)
                if identity in seen:
                    continue
                seen.add(identity)
                source_name = Path(str(match.get("source_json") or "")).name.casefold()
                text_key = str(match.get("text_key") or "")
                exact_identity = (source_name, text_key)
                english = str(match.get("official_en") or "")
                match.pop('verified_speaker', None)
                # Role metadata is copied only after the source text was
                # verified; a cache label or arbitrary player name is no proof.
                if (translations.get(exact_identity) and self._source_english.get(exact_identity)
                        and _source_text(self._source_english[exact_identity]) == _source_text(english)):
                    match['verified_speaker'] = self._source_speakers.get(exact_identity, '')
                if source_moves_only and exact_identity not in self._source_moves and exact_identity not in self._release_source_proofs:
                    continue
                vietnamese = translations.get(exact_identity, "")
                english = str(match.get("official_en") or "")
                source_table = str(match.get("source_table") or match.get("tbl") or "")
                count_identity = (source_name, source_table, text_key, _source_text(english))
                count_entry = count_identity not in counted
                if count_entry:
                    counted.add(count_identity)
                    official_entries += 1
                source_english = self._source_english.get(exact_identity, "")
                proof=self._release_source_proofs.get(exact_identity)
                original_chinese=str(match.get('official_zh') or (match.get('official_cn') if not match.get('translation_source') else '') or '')
                if vietnamese and (
                    (source_english and _source_text(source_english) != _source_text(english)
                     and not (proof and _equivalent_english(source_english, english, {})))
                    or (self.require_source_english and not source_english)
                    or (proof and (not original_chinese or _source_text(original_chinese)!=_source_text(proof[0])
                                   or (source_table and source_table!=proof[1])))
                ):
                    vietnamese = ""
                    incompatible_source_entries += int(count_entry)

                # A saved cache already mirrors Vietnamese into official_cn. Never
                # mistake that target for original Chinese on the next update.
                if "official_zh" not in match:
                    match["official_zh"] = (
                        "" if match.get("translation_source") else str(match.get("official_cn") or "")
                    )
                if vietnamese:
                    exact_id_matches += int(count_entry)
                    vietnamese_matches += int(count_entry)
                    match["official_vi"] = vietnamese
                    match["official_cn"] = vietnamese
                    moved = exact_identity in self._source_moves or proof is not None
                    match["translation_source"] = "wahu_verified_source_move" if moved else "wahu_exact_id_reviewed"
                    if moved:
                        verified_source_moves += int(count_entry)
                        match["translation_origin"] = ([proof[2],text_key] if proof
                                                       else list(self._source_moves[exact_identity][2]))
                elif english:
                    english_fallbacks += int(count_entry)
                    match["official_vi"] = ""
                    match["official_cn"] = english
                    match["translation_source"] = "official_english_fallback"
                else:
                    missing_entries += int(count_entry)
                    match["official_vi"] = ""
                    match["official_cn"] = ""
                    match["translation_source"] = "missing"

        return WahuMergeStats(
            official_entries=official_entries,
            exact_id_matches=exact_id_matches,
            vietnamese_matches=vietnamese_matches,
            english_fallbacks=english_fallbacks,
            missing_entries=missing_entries,
            incompatible_source_entries=incompatible_source_entries,
            verified_source_moves=verified_source_moves,
        )


def _source_text(text: str) -> str:
    """Ignore whitespace presentation only, not punctuation/placeholders or case."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def _equivalent_english(old: str, current: str, aliases: Mapping[str, str]) -> bool:
    """No fuzzy semantic changes, negation removal, new numbers or ID guessing.

    This is used only *after* exact Chinese, sync-event, version, conversation
    and reviewed-target proof. It does not relax live OCR matching thresholds.
    """
    def canonical(text):
        text = _source_text(text).replace('’', "'")
        # Only unambiguous expansions; 's / 'd in general could mean is/has or
        # had/would and must not be guessed. Preserve negation and all nouns.
        for contraction, expanded in (("I'm", 'I am'), ("won't", 'will not'),
                ("can't", 'cannot'), ("Whoever's playing", 'Whoever is playing'),
                ("isn't", 'is not'), ("aren't", 'are not'), ("wasn't", 'was not'),
                ("weren't", 'were not'), ("don't", 'do not'), ("doesn't", 'does not'),
                ("didn't", 'did not'), ("haven't", 'have not'), ("hasn't", 'has not'),
                ("hadn't", 'had not'), ("couldn't", 'could not'), ("wouldn't", 'would not'),
                ("shouldn't", 'should not'), ("mustn't", 'must not'), ("needn't", 'need not')):
            def expand(match):
                return expanded[0].upper()+expanded[1:] if match[0][0].isupper() else expanded
            text = re.sub(r'\b'+re.escape(contraction)+r'\b', expand, text, flags=re.I)
        text = re.sub(r"\b(I|you|we|they|he|she|it)'ll\b", r'\1 will', text, flags=re.I)
        text = re.sub(r"\b(you|we|they)'re\b", r'\1 are', text, flags=re.I)
        text = re.sub(r"\b(I|you|we|they)'ve\b", r'\1 have', text, flags=re.I)
        text = re.sub(r"\b(I|you|we|they|he|she|it) would've\b", r'\1 would have', text, flags=re.I)
        text = re.sub(r'\bGot it in my head\b', 'Got it into my head', text)
        if aliases and not text.isascii():
            pattern = r'(?<!\w)(' + '|'.join(re.escape(a) for a in sorted(aliases,key=len,reverse=True)) + r')(?!\w)'
            text = re.sub(pattern, lambda m: aliases[m.group()], text)
        return text
    return canonical(old) == canonical(current)


def _reviewed_source_rephrasing(key: str, chinese: str, old: str, current: str) -> bool:
    """A reviewed pair, not a fuzzy exception for arbitrary source changes.

    The recorded Chinese explicitly mentions reinforcements. All other sync,
    reviewed-target, version and unique-destination checks still apply before
    this identity-specific English localization revision can be admitted.
    """
    return (key, _source_text(chinese), _source_text(old), _source_text(current)) == (
        'Main_Mengzhou_3_6_170_8', '又有夜归军的支援了啊。',
        'So the Midnight Rangers came.', 'So the Midnight Rangers showed up with backup.')


def apply_configured_wahu_provider(
    database: dict[str, dict[str, Any]],
    config: Any,
) -> WahuMergeStats | None:
    if str(getattr(config, "translation_provider", "")).casefold() != "wahu":
        return None
    database_path = getattr(config, "wahu_db_path", None)
    if not database_path:
        raise ValueError("translation_provider is 'wahu' but wahu_db_path is not configured")
    return WahuTranslationProvider(Path(database_path)).enrich(database)
