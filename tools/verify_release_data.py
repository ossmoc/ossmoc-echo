"""Private real-release QA in disposable output; no active manifest is changed."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def translations(cache):
    return {(m.get('source_json'), m.get('text_key'), m.get('official_en')): m['official_vi']
            for group in cache.values() for m in group.get('matches', []) if m.get('official_vi')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--canonical', type=Path, required=True, help='Local canonical-map JSON triples')
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--baseline-database', type=Path, required=True)
    parser.add_argument('--baseline-exe', type=Path, required=True)
    parser.add_argument('--frozen-exe', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = {str(p): digest(p) for p in (args.archive, args.canonical, args.cache, args.baseline_database)}
    from PyInstaller.archive.readers import CArchiveReader
    # Compare with actual previous binary code, not the new provider pretending
    # to be the old version. No downloaded executable is launched by this tool.
    old_archive = CArchiveReader(str(args.baseline_exe)).open_embedded_archive('PYZ.pyz')
    baseline = types.ModuleType('baseline_wahu')
    sys.modules[baseline.__name__] = baseline
    exec(old_archive.extract('ludiglot.adapters.wuthering_waves.wahu'), baseline.__dict__)
    if args.frozen_exe:
        compiled = CArchiveReader(str(args.frozen_exe)).open_embedded_archive('PYZ.pyz')
        for name in ('ludiglot.version', 'ludiglot.core.release_redirects', 'ludiglot.core.release_sources',
                     'ludiglot.adapters.wuthering_waves.wahu', 'ludiglot.core.auto_wuwa'):
            module = types.ModuleType(name)
            module.__file__ = 'embedded/' + name
            module.__package__ = name.rsplit('.', 1)[0]
            sys.modules[name] = module
            exec(compiled.extract(name), module.__dict__)
    from ludiglot.core.release_sources import rebuild_from_release_sources
    from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider
    from ludiglot.core.auto_wuwa import AutoTextIndex
    from ludiglot.version import APP_VERSION
    cache = json.loads(args.cache.read_text(encoding='utf-8-sig'))
    old_stats = baseline.WahuTranslationProvider(args.baseline_database, require_source_english=True).enrich(cache)
    old = translations(cache)
    canonical = {(a, b): v for a, b, v in json.loads(args.canonical.read_text(encoding='utf-8'))}
    database = args.output / 'derived.db'
    rebuild_from_release_sources(args.archive, database, canonical, lambda: None)
    stats = WahuTranslationProvider(database, require_source_english=True).enrich(cache)
    new = translations(cache)
    lost = [key[1] for key, vi in old.items() if new.get(key) != vi]
    if lost:
        raise AssertionError('Previously verified translations lost/changed: ' + repr(lost[:20]))
    index = AutoTextIndex(cache)
    verified = []
    for key in ('Main_Mengzhou_3_6_90_1', 'Main_Mengzhou_3_6_94_1'):
        matches = [m for group in cache.values() for m in group.get('matches', [])
                   if m.get('text_key') == key and Path(m.get('source_json', '')).name == 'lang_multi_text.db']
        assert matches and all(m.get('official_vi') for m in matches), key
        for match in matches:
            found = index.resolve(match['official_en'], 'dialogue')
            assert found and found.vietnamese == match['official_vi'], key
        verified.append({'id': key, 'cache_rows': len(matches), 'resolve': True})
    assert all(digest(Path(p)) == value for p, value in before.items())
    with sqlite3.connect(database) as connection:
        proofs = connection.execute('SELECT count(*) FROM release_source_proofs').fetchone()[0]
    result = {'success': True, 'app_version': APP_VERSION, 'frozen_code': bool(args.frozen_exe),
              'baseline_vietnamese': old_stats.vietnamese_matches, 'vietnamese': stats.vietnamese_matches,
              'verified_source_moves': stats.verified_source_moves, 'proof_count': proofs,
              'previous_translations_lost_or_changed': len(lost), 'reported_ids': verified,
              'inputs_unchanged': True, 'input_hashes': before}
    if args.frozen_exe:
        result['exe_sha256'] = digest(args.frozen_exe)
    (args.output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'input_hashes'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
