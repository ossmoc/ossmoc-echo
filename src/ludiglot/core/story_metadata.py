"""Quest → handbook state → ShowTalk membership, from current game files."""
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3

from ludiglot.core.source_records import decode_config_json, fingerprint

METADATA_KEY = '__ossmoc_story_metadata__'


def _rows(path, table, column, *, embedded_id=False, flow=False, check_cancel=lambda:None):
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        for key, blob in conn.execute(f'SELECT {column},BinData FROM {table}'):
            check_cancel()
            yield str(key), decode_config_json(blob,key,embedded_id=embedded_id,flow=flow)


def _actions(value):
    if isinstance(value, list):
        for child in value:
            yield from _actions(child)
    elif isinstance(value, dict):
        if value.get('Name') == 'ShowTalk':
            yield value.get('Params',{}).get('TalkItems',[])
        for child in value.values():
            yield from _actions(child)


def build_story_metadata(root, check_cancel=lambda:None):
    root = Path(root)
    roots = [p for p in (root,root/'ConfigDB',root/'Client/Content/Aki/ConfigDB') if (p/'db_QuestData.db').is_file()]
    if not roots:
        return {'schema':1,'groups':{},'members':{},'edges':{},'warnings':['Không có metadata nhiệm vụ; nhóm chưa xác định được giữ riêng.']}
    if len(roots) != 1:
        raise ValueError('Có nhiều snapshot ConfigDB nhiệm vụ; không tự chọn một bản.')
    base = roots[0]
    required = ['db_QuestData.db','db_PlotHandBook.db','db_flowState.db']
    if any(not (base/name).is_file() for name in required):
        raise ValueError('Metadata nhiệm vụ chưa đầy đủ; giữ generation cũ.')
    titles = {}
    lang = base/'en/lang_multi_text.db'
    if lang.is_file():
        with sqlite3.connect(lang.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
            conn.execute('PRAGMA query_only=ON'); conn.execute('PRAGMA trusted_schema=OFF')
            titles = dict(conn.execute("SELECT Id,Content FROM MultiText WHERE Id LIKE 'QuestChapter_%'"))
    quests = {}
    groups = {}
    warnings = []
    for quest, item in _rows(base/required[0],'questdata','QuestId',embedded_id=True,check_cancel=check_cancel):
        if not isinstance(item,dict):
            raise ValueError('Schema QuestData không hợp lệ.')
        chapter = item.get('ChapterId')
        if item.get('Type') != 1 or not isinstance(chapter,int) or chapter <= 0:
            continue
        match = re.match(r'^(\d+)[._](\d+)(?!\d)',str(item.get('Key','')))
        version = '.'.join(str(int(n)) for n in match.groups()) if match else ''
        group = f'quest:{chapter}:{version or "unknown"}'
        title = titles.get(f'QuestChapter_{chapter}_ActName','')
        if not title or set(title.strip())=={'*'}:
            title = f'Chương {chapter} (chưa có tên)'
        groups.setdefault(group, {'label':(version+' · ' if version else '')+str(title),
                                  'version':version,'chapter':chapter,'quests':[]})['quests'].append(quest)
        quests[quest] = group
    # Several quests can share one chapter. A missing version on one quest
    # may inherit only the unique version explicitly recorded for that SAME
    # chapter, never from Act numbers or neighbouring chapter IDs.
    chapter_versions=defaultdict(set)
    for value in groups.values():
        if value['version']:
            chapter_versions[value['chapter']].add(value['version'])
    for group,value in list(groups.items()):
        versions=chapter_versions[value['chapter']]
        if not value['version'] and len(versions)==1:
            target=f'quest:{value["chapter"]}:{next(iter(versions))}'
            groups[target]['quests'].extend(value['quests'])
            for quest in value['quests']:
                quests[quest]=target
            del groups[group]
    flow_groups = defaultdict(set)
    for quest, items in _rows(base/required[1],'plothandbookconfig','QuestId',check_cancel=check_cancel):
        if not isinstance(items,list):
            raise ValueError('Schema PlotHandBook không hợp lệ.')
        for item in items:
            ref = item.get('Flow',{}) if isinstance(item,dict) else {}
            if quest in quests and ref.get('FlowListName') and isinstance(ref.get('FlowId'),int) and isinstance(ref.get('StateId'),int):
                state = f'{ref["FlowListName"]}_{ref["FlowId"]}_{ref["StateId"]}'
                flow_groups[state].add(quests[quest])
    members = {}
    edges = defaultdict(set)
    seen_states = set()
    unsupported = 0
    for state, actions in _rows(base/required[2],'flowstate','StateKey',flow=True,check_cancel=check_cancel):
        if not isinstance(actions,list):
            raise ValueError('Schema flowState không hợp lệ.')
        seen_states.add(state)
        for items in _actions(actions):
            if not isinstance(items,list):
                raise ValueError('Schema ShowTalk không hợp lệ.')
            previous = None
            for item in items:
                if not isinstance(item,dict):
                    previous = None; unsupported += 1; continue
                key = item.get('TidTalk')
                options = item.get('Options',[])
                if key and isinstance(key,str):
                    value = members.setdefault(key,{'kind':'dialogue','groups':set(),'flows':set()})
                    value['groups'].update(flow_groups.get(state,()))
                    value['flows'].add(state)
                    # Only a plain sequential run is an edge. A choice or
                    # explicit jump ends it; do not predict across that fork.
                    if previous:
                        edges[previous].add(key)
                    previous = key if not options and not any('next' in k.casefold() or 'jump' in k.casefold() for k in item) else None
                else:
                    previous = None
                for option in options if isinstance(options,list) else []:
                    key_option = option.get('TidTalkOption') if isinstance(option,dict) else None
                    if isinstance(key_option,str) and key_option:
                        value = members.setdefault(key_option,{'kind':'choices','groups':set(),'flows':set()})
                        value['groups'].update(flow_groups.get(state,()))
                        value['flows'].add(state)
                        if key:
                            edges[key].add(key_option)
    missing = len(set(flow_groups)-seen_states)
    if missing:
        warnings.append(f'{missing} state nhiệm vụ không có trong nguồn hiện tại; không đoán nhánh.')
    if unsupported:
        warnings.append(f'{unsupported} mục ShowTalk chưa hỗ trợ.')
    return {'schema':1,'groups':groups,
            'members':{k:{**v,'groups':sorted(v['groups']),'flows':sorted(v['flows'])} for k,v in members.items()},
            'edges':{k:sorted(v) for k,v in edges.items()},'warnings':warnings}


def attach_story_metadata(cache, root, check_cancel=lambda:None):
    metadata = build_story_metadata(root,check_cancel)
    cache[METADATA_KEY] = {'matches':[],'story':metadata}
    return metadata


def coverage(cache, index=None):
    from ludiglot.core.staged_merge import name_only_keys
    excluded = name_only_keys(cache)
    metadata = cache.get(METADATA_KEY,{}).get('story',{})
    members = metadata.get('members',{})
    groups = {key:{'source':set(),'target':set(),'merged':set(),'indexed':set(),
                   'excluded_names':set(),'no_english':set()} for key in metadata.get('groups',{})}
    seen = set()
    for payload in cache.values():
        for row in payload.get('matches',[]):
            key = str(row.get('text_key') or '')
            ident = (str(row.get('source_namespace') or row.get('source_json') or '').replace('\\','/').casefold(),
                     row.get('source_table',''),key,fingerprint(str(row.get('official_en') or '')))
            if ident in seen:
                continue
            seen.add(ident)
            for group in members.get(key,{}).get('groups',[]):
                values = groups[group]
                if key in excluded:
                    values['excluded_names'].add(ident)
                    continue
                if not str(row.get('official_en') or '').strip():
                    values['no_english'].add(ident)
                    continue
                values['source'].add(ident)
                if row.get('translation_candidate_present') or row.get('official_vi'):
                    values['target'].add(ident)
                if row.get('official_vi') and row.get('translation_source') in {'wahu_exact_id_reviewed','wahu_verified_source_move'}:
                    values['merged'].add(ident)
                    if index is not None and key in index._by_id:
                        values['indexed'].add(ident)
    return {group:{k:len(v) for k,v in values.items()} for group,values in groups.items()}
