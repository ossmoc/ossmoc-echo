import sqlite3

import pytest

from ludiglot.core.staged_merge import Candidate, StagedMatcher, compatible_facts, name_only_keys, similarity
from ludiglot.core.story_metadata import coverage, METADATA_KEY
from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider


def candidate(en='I am the greatest swordman', cn='我是最强的剑客', vi='Tôi là kiếm sĩ vĩ đại nhất.', key='A', ns='lang_multi_text.db'):
    return Candidate(ns, 'MultiText', key, en, cn, vi)


def resolve(matcher, en='I the est swordman', cn='', key='A', ns='lang_multi_text.db'):
    return matcher.resolve(ns, 'MultiText', key, en, cn)


def test_requested_incomplete_sentence_recovers_with_identity():
    c=candidate()
    match, stage, score, error=resolve(StagedMatcher([c]))
    assert match == c and stage=='id' and score>=.70 and not error


@pytest.mark.parametrize('current',['I am the greatest swordman.', 'I am the greatest swordman!'])
def test_punctuation_and_repeated_target_sources_are_not_ambiguity(current):
    a=candidate(); b=candidate(en='I am the greatest swordman.')
    assert resolve(StagedMatcher([a,b]),en=current)[0]


def test_exact_text_then_id_then_chinese_and_related_unique_target():
    c=candidate()
    assert resolve(StagedMatcher([c]),en=c.english,key='B')[1]=='exact_text'
    assert resolve(StagedMatcher([c]),en='An entirely revised localization.',cn=c.chinese)[1]=='id'
    assert resolve(StagedMatcher([c]),en='An entirely revised localization.',cn=c.chinese,key='B')[1]=='chinese'
    moved=candidate(ns='lang_multi_text_1sthalf.db')
    assert resolve(StagedMatcher([moved]),cn=moved.chinese)[1]=='unique_target'
    assert resolve(StagedMatcher([moved]),cn='')[1]=='database_relation'


@pytest.mark.parametrize('old,current,expected',[
    ('I am the greatest swordman','I am the greatest swordman now','fuzzy_90'),
    ('I am the greatest swordman','I am the greatest swordman in town','fuzzy_80'),
    ('I am the greatest swordman','I the st swordman','fuzzy_70'),
])
def test_all_similarity_tiers_are_reachable_in_actual_same_flow(old,current,expected):
    c=candidate(en=old,cn='')
    members={key:{'flows':['Flow_1']} for key in ('A','B')}
    match,stage,_,_=resolve(StagedMatcher([c],members),en=current,key='B')
    assert match==c and stage==expected


def test_ambiguous_targets_do_not_fall_through_to_weaker_guess():
    a=candidate(); b=candidate(vi='Một bản dịch khác.')
    assert resolve(StagedMatcher([a,b]))[3]=='ambiguous_candidates'


@pytest.mark.parametrize('old,current',[
    ('Bring 2 pieces.','Bring 3 pieces.'),
    ('I do not trust him.','I trust him.'),
    ("I won't trust him.",'I trust him.'),
    ('Hello {PlayerName}.','Hello {OtherName}.'),
])
def test_explicit_facts_cannot_be_erased_by_same_id_or_chinese(old,current):
    c=candidate(en=old)
    assert not compatible_facts(old,current)
    assert not resolve(StagedMatcher([c]),en=current,cn=c.chinese)[0]


def test_no_cross_namespace_table_or_global_short_fuzzy_guess():
    c=candidate()
    assert not resolve(StagedMatcher([c]),ns='other/lang_multi_text.db',cn=c.chinese)[0]
    assert not StagedMatcher([c]).resolve(c.namespace,'Other','A',c.english,c.chinese)[0]
    assert not resolve(StagedMatcher([c]),key='B')[0]
    assert not resolve(StagedMatcher([candidate(en='Yes.',cn='')]),en='Yes.',key='B')[0]


def test_near_tie_across_fuzzy_threshold_is_not_silently_accepted():
    a=candidate(en='I am the greatest swordman now',cn='',key='A')
    b=candidate(en='I am the greatest swordman here',cn='',key='C',vi='Bản khác')
    members={k:{'flows':['Flow_1']} for k in ('A','B','C')}
    assert resolve(StagedMatcher([a,b],members),en='I am the greatest swordman',key='B')[3]=='ambiguous_candidates'


def test_name_flag_does_not_remove_same_id_with_an_actual_sentence():
    a={'text_key':'A','official_en':'Mornye?','source_table':'MultiText'}
    b={**a,'official_en':'Mornye is here.'}
    speaker={'text_key':'Speaker_A','official_en':'Mornye','source_table':'Speaker'}
    assert 'A' not in name_only_keys({'a':{'matches':[a,b,speaker]}})


def test_excluded_name_is_not_a_runtime_context_anchor():
    from ludiglot.core.auto_wuwa import AutoTextIndex
    r={'source_json':'lang_multi_text.db','source_table':'MultiText','source_namespace':'lang_multi_text.db',
       'text_key':'Main_Test_1_1','official_en':'Mornye?','official_vi':'Mornye?',
       'translation_source':'wahu_exact_id_reviewed','translation_excluded':'name_only'}
    index=AutoTextIndex({'a':{'matches':[r]}})
    assert 'Main_Test_1_1' not in index._by_id


def test_names_and_player_placeholders_are_excluded_not_short_dialogue_or_cheese():
    def row(key,en,table='MultiText'):
        return {'text_key':key,'official_en':en,'source_table':table,'source_namespace':'lang_multi_text.db'}
    cache={str(n):{'matches':[r]} for n,r in enumerate([
        row('Speaker_1','Mornye','Speaker'),row('A','Mornye?'),row('B','({PlayerName}...)'),
        row('C','Very well.'),row('D','Cheese!'),row('E','Mornye! Mornye!'),row('F','')])}
    assert name_only_keys(cache)=={'Speaker_1','A','B','E'}
    cache[METADATA_KEY]={'story':{'groups':{'g':{}},'members':{k:{'groups':['g']} for k in ('A','B','C','D')}}}
    assert coverage(cache)['g']['source']==2
    cache[METADATA_KEY]['story']['members']['F']={'groups':['g']}
    assert coverage(cache)['g']['source']==2 and coverage(cache)['g']['no_english']==1


def test_provider_recovery_records_provenance_and_keeps_original_identity(tmp_path):
    path=tmp_path/'provider.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE entries(dbfile,key,en,vi,status)')
        c.execute('INSERT INTO entries VALUES (?,?,?,?,?)',('lang_multi_text.db','A','I am the greatest swordman','Bản Việt','final'))
        c.execute('CREATE TABLE source_candidates(namespace,tbl,key,en,cn,vi,proof,en_hash,cn_hash)')
        c.execute('CREATE TABLE reference_identity(dbfile,key,tbl,cn)')
        c.execute('INSERT INTO reference_identity VALUES (?,?,?,?)',('lang_multi_text.db','A','MultiText','我是最强的剑客'))
        c.execute('CREATE TABLE meta(k,v)')
    row={'source_json':'lang_multi_text.db','source_namespace':'lang_multi_text.db','source_table':'MultiText',
         'text_key':'A','official_en':'I the est swordman','official_zh':'我是最强的剑客'}
    cache={'x':{'matches':[row,dict(row)]}}
    provider=WahuTranslationProvider(path,require_source_english=True)
    for _ in range(2):
        assert provider.enrich(cache).vietnamese_matches==1
        assert row['official_vi']=='Bản Việt' and row['merge_method']=='id'
        assert row['translation_proof']['kind']=='staged_source_match'
        assert row['official_zh']=='我是最强的剑客' and row['source_namespace']=='lang_multi_text.db'
