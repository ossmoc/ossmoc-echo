"""Exercise migration, coverage gate and atomic activation in an isolated root."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ludiglot.core.translation_update import TranslationUpdater,SOURCE_BUILD_REVISION
from ludiglot.core.auto_wuwa import AutoTextIndex


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--official-cache',type=Path,required=True)
    parser.add_argument('--active-cache',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    inputs={str(p):sha(p) for p in (args.official_cache,args.active_cache,args.database)}
    updater=TranslationUpdater(args.output/'translations',source_data_root=args.source_root)
    updater._write_manifest({'schema_version':1,'cache_path':str(args.active_cache.resolve()),'database_path':None,
                             'release_tag':'v3.0.17','source_build_revision':4})
    generation=updater._new_generation('qa-story')
    started=time.monotonic()
    result=updater._merge_and_activate(generation,args.database,args.official_cache,
        {'release_tag':'v3.0.17','database_name_mode':'packaged_seed','source_build_revision':SOURCE_BUILD_REVISION},None,print,None)
    active=updater.active_manifest()
    assert active['source_build_revision']==SOURCE_BUILD_REVISION and not active['coverage_gate']['lost']
    assert active['previous']['cache_path']==str(args.active_cache.resolve())
    assert all(sha(Path(path))==digest for path,digest in inputs.items())
    cache=json.loads(result.cache_path.read_text(encoding='utf-8'))
    index=AutoTextIndex(cache)
    versions={g.version for g in index.story_catalog if g.count}
    assert {'3.1','3.2','3.3','3.4','3.5','3.6','3.7'}<=versions
    report={'success':True,'elapsed_seconds':round(time.monotonic()-started,2),'input_files_unchanged':True,
        'source_build_revision':SOURCE_BUILD_REVISION,'coverage':active['coverage'],'gate':active['coverage_gate'],
        'versions':sorted(versions),'cache':str(result.cache_path),'groups':active['story_coverage']}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='groups'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
