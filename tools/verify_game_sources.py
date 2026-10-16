"""Read retained game files without writing to them; verify new source identities."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ludiglot.core import text_builder
from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider
from ludiglot.core.auto_wuwa import AutoTextIndex


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=False)
    # The product's audio cache is unrelated to this source-identity check.
    # Suppress that optional writer: the retained source tree stays read-only.
    text_builder.build_voice_map_from_configdb=lambda *args,**kwargs:{}
    started=time.monotonic()
    cache=text_builder.build_text_db_from_root_all(a.source_root)
    stats=WahuTranslationProvider(a.database,require_source_english=True).enrich(cache)
    index=AutoTextIndex(cache)
    seen=set(); tables=Counter(); translated=Counter()
    for payload in cache.values():
        for row in payload.get('matches',[]):
            ident=(row.get('source_namespace'),row.get('source_table'),row.get('text_key'))
            if ident in seen:
                continue
            seen.add(ident)
            tables[str(row.get('source_table') or 'legacy_non_language_source')]+=1
            if row.get('official_vi'):
                translated[str(row.get('source_table') or 'legacy_non_language_source')]+=1
    assert stats.vietnamese_matches>270000
    assert {'3.1','3.2','3.3','3.4','3.5','3.6','3.7'}<={g.version for g in index.story_catalog if g.count}
    report={'success':True,'elapsed_seconds':round(time.monotonic()-started,2),'audio_cache_writer_disabled':True,
        'vietnamese':stats.vietnamese_matches,'unique_namespace_table_ids':len(seen),'tables':dict(tables),
        'translated_tables':dict(translated),'groups':[{'label':g.label,'source':g.source_count,'merged':g.count,'indexed':g.indexed_count} for g in index.story_catalog]}
    (a.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='groups'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
