"""Prevent a partial importer from replacing working unchanged-source data."""
from collections import Counter
from pathlib import PurePosixPath
import re

from ludiglot.core.source_records import fingerprint


def inventory(cache):
    result={}
    ambiguous=set()
    for payload in cache.values():
        for row in payload.get('matches',[]):
            source=str(row.get('source_namespace') or row.get('source_json') or '').replace('\\','/').casefold()
            # The legacy view is allowed only when there is a unique table.
            key=(PurePosixPath(source).name,str(row.get('text_key') or ''),
                 fingerprint(str(row.get('official_en') or '')),
                 fingerprint(str(row.get('official_zh') or (row.get('official_cn') if not row.get('translation_source') else '') or '')))
            value=(source,row.get('source_table',''))
            translated=bool(row.get('official_vi')) and row.get('translation_source') in {'wahu_exact_id_reviewed','wahu_verified_source_move'}
            if key in result and result[key][0] != value:
                ambiguous.add(key)
            else:
                result[key]=(value,translated)
    return {k:v[1] for k,v in result.items() if k not in ambiguous}


def regression_report(before, after):
    loss=Counter()
    unchanged=0
    for identity,translated in before.items():
        if translated and identity in after:
            unchanged+=1
            if not after[identity]:
                key=identity[1]
                match=re.match(r'((?:Main|Side|GNNPC)_.+)_\d+_\d+$',key)
                loss[match[1] if match else identity[0]]+=1
    return {'unchanged_previously_translated':unchanged,'lost':sum(loss.values()),'lost_by_group':dict(sorted(loss.items()))}
