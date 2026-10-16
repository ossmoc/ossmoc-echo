import json
import sqlite3
import pytest

from ludiglot.core.auto_wuwa import AutoTextIndex
from ludiglot.core.coverage_gate import inventory,regression_report
from ludiglot.core.story_metadata import METADATA_KEY,attach_story_metadata,build_story_metadata,coverage


def fixtures(root):
    base=root/'ConfigDB'; base.mkdir()
    def create(file,table,col,rows):
        with sqlite3.connect(base/file) as c:
            c.execute(f'CREATE TABLE {table}({col},BinData BLOB)')
            c.executemany(f'INSERT INTO {table} VALUES (?,?)',[(k,json.dumps(v).encode()) for k,v in rows])
    create('db_QuestData.db','questdata','QuestId',[(1,{'Id':1,'Type':1,'ChapterId':35,'Key':'3.3 main'}),
             (2,{'Id':2,'Type':1,'ChapterId':39,'Key':'3.4 main'}),
             (3,{'Id':3,'Type':1,'ChapterId':40,'Key':'3.5 main'}),
             (4,{'Id':4,'Type':1,'ChapterId':45,'Key':'3.7 main'})])
    create('db_PlotHandBook.db','plothandbookconfig','QuestId',[(1,[{'Flow':{'FlowListName':'Story','FlowId':1,'StateId':1}}]),
             (2,[{'Flow':{'FlowListName':'Next','FlowId':1,'StateId':1}}])])
    create('db_flowState.db','flowstate','StateKey',[('Story_1_1',[{'Name':'ShowTalk','Params':{'TalkItems':[
        {'Id':7,'TidTalk':'Zuoyequnxing_1_7'}, {'Id':9,'TidTalk':'Zuoyequnxing_1_9','Options':[
        {'TidTalkOption':'Response_A'},{'TidTalkOption':'Response_B'}]}, {'Id':55,'TidTalk':'Unreachable_After_Choice'}]}}]),
        ('Next_1_1',[{'Name':'ShowTalk','Params':{'TalkItems':[{'TidTalk':'NewFamily_1_1'}]}}])])
    return base


def row(key,en,vi='Bản Việt'):
    return {'source_json':'lang_multi_text.db','source_table':'MultiText','source_namespace':'lang_multi_text.db',
        'text_key':key,'official_en':en,'official_zh':'原文','official_vi':vi,'translation_source':'wahu_exact_id_reviewed'}


def test_metadata_admits_unknown_dialogue_family_but_not_hud_and_counts_actual_index(tmp_path):
    fixtures(tmp_path)
    data={str(n):{'matches':[r]} for n,r in enumerate([
        row('Zuoyequnxing_1_7','This is a distinctive English line for the quest.'),
        row('Zuoyequnxing_1_9','Very well.'),row('NewFamily_1_1','Another unique sentence for another quest.'),
        row('UnrelatedHUD_1_1','This is not a dialogue.'),row('Response_A','A choice'),row('Response_B','B choice')])}
    attach_story_metadata(data,tmp_path)
    index=AutoTextIndex(data)
    assert index.resolve('This is a distinctive English line for the quest.','dialogue')
    assert not index.resolve('This is not a dialogue.','dialogue')
    assert {g.version for g in index.story_catalog}=={'3.3','3.4','3.5','3.7'}
    empty=next(g for g in index.story_catalog if g.version=='3.7')
    assert not empty.seed and empty.count==0 and empty.source_count==0
    group=next(g for g in index.story_catalog if g.version=='3.3')
    assert group.count==group.source_count==group.indexed_count==4
    assert index.set_story_group(group.key)
    assert index.valid_context('Zuoyequnxing_1_7')
    assert index.set_context('Zuoyequnxing_1_7',observed=True)
    assert index.resolve('Very well.','dialogue').identity[1]=='Zuoyequnxing_1_9'
    assert index.resolve('Another unique sentence for another quest.','dialogue').identity[1]=='NewFamily_1_1'


def test_graph_fork_does_not_link_past_choice_and_sparse_branches_remain_ambiguous(tmp_path):
    fixtures(tmp_path)
    meta=build_story_metadata(tmp_path)
    assert set(meta['edges']['Zuoyequnxing_1_9'])=={'Response_A','Response_B'}
    assert 'Unreachable_After_Choice' not in meta['edges']['Zuoyequnxing_1_9']
    data={str(n):{'matches':[r]} for n,r in enumerate([
        row('Zuoyequnxing_1_9','Anchor sentence with enough distinctive words.'),
        row('Response_A','Yes.','Đồng ý'),row('Response_B','Yes.','Vâng'),
        row('Unreachable_After_Choice','Goodbye.','Tạm biệt')])}
    data[METADATA_KEY]={'matches':[],'story':meta}
    index=AutoTextIndex(data)
    assert index.resolve('Anchor sentence with enough distinctive words.','dialogue')
    assert not index.resolve('Yes.','dialogue')
    assert not index.resolve('Goodbye.','dialogue')


def test_regression_gate_checks_unchanged_source_not_just_total():
    def cache(*rows):
        return {str(n):{'matches':[r]} for n,r in enumerate(rows)}
    old=row('Main_A_1_1','English sentence A.')
    before=inventory(cache(old))
    lost={**old,'official_vi':'','translation_source':'official_english_fallback'}
    after=inventory(cache(lost,row('Main_B_1_1','Added B.'),row('Main_C_1_1','Added C.')))
    assert regression_report(before,after)['lost']==1
    # A reused ID with genuinely changed English must not keep the stale VI.
    changed={**lost,'official_en':'Changed English sentence.'}
    assert regression_report(before,inventory(cache(changed)))['lost']==0
    # A revised valid target is permitted.
    assert regression_report(before,inventory(cache({**old,'official_vi':'Bản Việt mới'})))['lost']==0


def test_atomic_update_rejects_partial_provider_even_when_total_is_positive(tmp_path):
    from ludiglot.core.translation_update import TranslationUpdater,TranslationUpdateError
    import pytest
    root=tmp_path/'translations'; generation=root/'generations/new'; generation.mkdir(parents=True)
    old_cache=root/'generations/old/cache.json'; old_cache.parent.mkdir()
    old=row('Main_A_1_1','English sentence A.')
    old_cache.write_text(json.dumps({'a':{'matches':[old]}}),encoding='utf-8')
    source=tmp_path/'source.json'
    source.write_text(json.dumps({'a':{'matches':[old]},'b':{'matches':[row('Main_B_1_1','English sentence B.')]}}),encoding='utf-8')
    database=generation/'provider.db'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE entries(dbfile TEXT,key TEXT,en TEXT,vi TEXT,status TEXT)')
        c.execute('INSERT INTO entries VALUES (?,?,?,?,?)',('lang_multi_text.db','Main_B_1_1','English sentence B.','Câu B','final'))
    updater=TranslationUpdater(root)
    updater._write_manifest({'schema_version':1,'cache_path':str(old_cache),'database_path':None})
    before=(root/'active.json').read_bytes()
    with pytest.raises(TranslationUpdateError,match='chặn kích hoạt'):
        updater._merge_and_activate(generation,database,source,{},None,None,None)
    assert (root/'active.json').read_bytes()==before
    assert json.loads((generation/'coverage.json').read_text())['regression']['lost']==1


@pytest.mark.parametrize('version',['3.8','4.12'])
def test_future_patch_and_entirely_new_id_family_need_no_code_mapping(tmp_path,version):
    base=fixtures(tmp_path)
    with sqlite3.connect(base/'db_QuestData.db') as c:
        value={'Id':1,'Type':1,'ChapterId':200,'Key':version+' new main quest'}
        c.execute('UPDATE questdata SET BinData=? WHERE QuestId=1',(json.dumps(value).encode(),))
    # Even the old prefix + numeric scene/line convention is absent.
    with sqlite3.connect(base/'db_flowState.db') as c:
        actions=[{'Name':'ShowTalk','Params':{'TalkItems':[{'TidTalk':'NovelFamily_Alpha'}, {'TidTalk':'NovelFamily_Beta'}]}}]
        c.execute('UPDATE flowstate SET BinData=? WHERE StateKey=?',(json.dumps(actions).encode(),'Story_1_1'))
    data={'a':{'matches':[row('NovelFamily_Alpha','A distinctive visible sentence from a future release.')]},
          'b':{'matches':[row('NovelFamily_Beta','Very well.','Được thôi.')]}}
    attach_story_metadata(data,tmp_path)
    index=AutoTextIndex(data)
    group=next(g for g in index.story_catalog if g.version==version)
    assert group.count==group.indexed_count==group.source_count==2
    assert index.set_story_group(group.key)
    assert index.set_context(group.seed)
    assert not index.resolve('Very well.','dialogue')
    assert index.resolve('A distinctive visible sentence from a future release.','dialogue')
    assert index.resolve('Very well.','dialogue').vietnamese=='Được thôi.'


def test_changed_metadata_schema_fails_instead_of_silently_claiming_complete(tmp_path):
    base=fixtures(tmp_path)
    with sqlite3.connect(base/'db_flowState.db') as c:
        c.execute('UPDATE flowstate SET BinData=? WHERE StateKey=?',(b'new unknown binary schema','Story_1_1'))
    with pytest.raises(ValueError,match='Schema BinData'):
        build_story_metadata(tmp_path)


def test_old_cache_is_rebuilt_from_retained_sources_inside_staging_not_pak(tmp_path,monkeypatch):
    from ludiglot.core.translation_update import TranslationUpdater
    from ludiglot.core import text_builder
    source_root=tmp_path/'source'; source_root.mkdir(); base=fixtures(source_root); (base/'en').mkdir()
    root=tmp_path/'translations'; generation=root/'generations/new'; generation.mkdir(parents=True)
    source=tmp_path/'legacy.json'
    first=row('Zuoyequnxing_1_7','A distinctive source sentence.')
    source.write_text(json.dumps({'a':{'matches':[first]}}),encoding='utf-8')
    database=generation/'provider.db'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE entries(dbfile TEXT,key TEXT,en TEXT,vi TEXT,status TEXT)')
        c.execute('INSERT INTO entries VALUES (?,?,?,?,?)',('lang_multi_text.db','Zuoyequnxing_1_7','A distinctive source sentence.','Bản Việt','final'))
    called=[]
    def rebuild(path,*,voice_cache_path,check_cancel):
        assert path==source_root
        assert voice_cache_path.parent==generation
        check_cancel(); called.append(True)
        data={'a':{'matches':[first]},'__ossmoc_source_schema__':{'matches':[],'version':1}}
        attach_story_metadata(data,path)
        return data
    monkeypatch.setattr(text_builder,'build_text_db_from_root_all',rebuild)
    updater=TranslationUpdater(root,source_data_root=source_root)
    result=updater._merge_and_activate(generation,database,source,{},None,None,None)
    assert called and result.manifest['coverage']['vietnamese_matches']==1
    assert json.loads(result.cache_path.read_text(encoding='utf-8'))['__ossmoc_source_schema__']['version']==1
