import json
import sqlite3
import struct
import zipfile

import pytest

from ludiglot.core.source_records import (CanonicalTranslations,TextRow,decode_config_json,
                                         read_text_file,resolve_redirect)
from ludiglot.core.release_sources import rebuild_from_release_sources
from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider


def test_flatbuffer_uses_offsets_and_validates_embedded_and_binary_identity():
    payload=json.dumps({'Id':42,'Key':'A brace { is not a start marker'}).encode()
    blob=struct.pack('<IHHHHiIII',12,8,12,8,4,8,8,42,len(payload))+payload+b'\0'
    assert decode_config_json(blob,42,embedded_id=True)['Id']==42
    for value,key in ((blob,43),(b'junk'+blob,42),(blob[:-1],42),(blob[:24]+b'\xff'*4+blob[28:],42)):
        with pytest.raises((ValueError,UnicodeError)):
            decode_config_json(value,key,embedded_id=True)


def test_reader_and_canonical_keep_table_and_namespace(tmp_path):
    path=tmp_path/'text.db'
    with sqlite3.connect(path) as conn:
        for table,text in [('First','First source'),('Second','Second source')]:
            conn.execute(f'CREATE TABLE {table}(Id TEXT,Content TEXT)')
            conn.execute(f'INSERT INTO {table} VALUES (?,?)',('same',text))
    rows=read_text_file(path,'folder/text.db')
    assert len(rows)==2 and {r.table for r in rows}=={'First','Second'}
    canonical=CanonicalTranslations()
    for r in rows:
        canonical.add(r)
    assert ('text.db','same') not in canonical
    assert canonical.target('folder/text.db','First','same')=='First source'
    assert canonical.target('text.db','First','same')==''


def test_unknown_schema_and_duplicate_id_fail_closed(tmp_path):
    path=tmp_path/'unknown.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE data(Guess TEXT,Maybe TEXT)')
        conn.execute("INSERT INTO data VALUES ('key','text')")
    with pytest.raises(ValueError,match='Schema'):
        read_text_file(path)
    path=tmp_path/'duplicates.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE data(Id TEXT,Content TEXT)')
        conn.executemany('INSERT INTO data VALUES (?,?)',[('1','A'),('1','B')])
    with pytest.raises(ValueError,match='trùng'):
        read_text_file(path)


def test_redirect_mapping_is_explicit_bounded_and_cycle_safe():
    first=TextRow('a.db','MultiText','1','***',7)
    second=TextRow('b.db','MultiText','1','Real source',0)
    rows={r.identity:r for r in (first,second)}
    assert resolve_redirect(first.identity,rows,{})[1]=='redirect_mapping_unknown'
    assert resolve_redirect(first.identity,rows,{('a.db',7):'b.db'})[0]==second
    rows[second.identity]=TextRow('b.db','MultiText','1','***',8)
    mapping={('a.db',7):'b.db',('b.db',8):'a.db'}
    assert resolve_redirect(first.identity,rows,mapping)[0] is None
    assert resolve_redirect(first.identity,rows,mapping,max_hops=1)[1]=='redirect_depth_exceeded'


def sqlite_bytes(tmp_path, name, tables):
    path=tmp_path/name
    with sqlite3.connect(path) as conn:
        for table,rows in tables.items():
            conn.execute(f'CREATE TABLE {table}(Id TEXT,Content TEXT,RedirectDbIndex INT)')
            conn.executemany(f'INSERT INTO {table} VALUES (?,?,?)',rows)
    return path.read_bytes()


def raw_archive(tmp_path, extra_cn=None, redirect=0):
    archive=tmp_path/'raw.zip'
    with zipfile.ZipFile(archive,'w') as package:
        for language,tables in [('en',{'MultiText':[('Main_Test_3_7_1_1','A new English sentence.',redirect)]}),
                                ('zh-Hans',{'MultiText':[('Main_Test_3_7_1_1','新句子。',redirect)]})]:
            package.writestr('editor/build_support/Wahu/_dbcfg/Client/Content/Aki/ConfigDB/'+language+'/lang_multi_text.db',
                             sqlite_bytes(tmp_path,language+'.db',tables))
        if extra_cn:
            package.writestr('editor/build_support/Wahu/_dbcfg/Client/Content/Aki/ConfigDB/zh-CN/lang_multi_text.db',
                             sqlite_bytes(tmp_path,'zh-CN.db',{'MultiText':[('Main_Test_3_7_1_1',extra_cn,0)]}))
    return archive


def cache(en='A new English sentence.',cn='新句子。',table='',ns=''):
    return {'line':{'matches':[{'source_json':'lang_multi_text.db','text_key':'Main_Test_3_7_1_1',
        'official_en':en,'official_cn':cn,'source_table':table,'source_namespace':ns}]}}


def test_raw_only_release_requires_exact_bilingual_table_identity_and_tracks_proof(tmp_path):
    archive=raw_archive(tmp_path)
    target=rebuild_from_release_sources(archive,tmp_path/'derived.db',
        {('lang_multi_text.db','Main_Test_3_7_1_1'):'Một câu tiếng Việt mới.'},lambda:None)
    provider=WahuTranslationProvider(target,require_source_english=True)
    good=cache()
    assert provider.enrich(good).vietnamese_matches==1
    row=good['line']['matches'][0]
    assert row['source_table']=='MultiText' and row['source_namespace']=='lang_multi_text.db'
    assert row['translation_proof']['kind']=='raw_bilingual_exact'
    assert len(row['translation_proof']['archive_sha256'])==64
    assert provider.enrich(good).vietnamese_matches==1
    for bad in [cache(cn='Different Chinese'),cache(cn=''),cache(en='A new English sentence not.'),
                cache(table='Other'),cache(ns='other/lang_multi_text.db')]:
        assert provider.enrich(bad).vietnamese_matches==0


@pytest.mark.parametrize('extra_cn,redirect',[('不同内容',0),(None,1)])
def test_conflicting_locales_and_unknown_redirect_do_not_become_translation(tmp_path,extra_cn,redirect):
    archive=raw_archive(tmp_path,extra_cn,redirect)
    with pytest.raises(ValueError,match='Không có ID'):
        rebuild_from_release_sources(archive,tmp_path/'derived.db',
            {('lang_multi_text.db','Main_Test_3_7_1_1'):'Bản Việt'},lambda:None)


def test_raw_full_canonical_disambiguates_tables_without_legacy_basename_guess(tmp_path):
    archive=tmp_path/'raw.zip'; canonical=CanonicalTranslations()
    with zipfile.ZipFile(archive,'w') as package:
        for lang,content in [('en','English source'),('zh-Hans','原文')]:
            package.writestr('editor/build_support/Wahu/_dbcfg/Client/Content/Aki/ConfigDB/'+lang+'/sub/text.db',
                sqlite_bytes(tmp_path,lang+'.db',{table:[('same',content,0)] for table in ('A','B')}))
    for table,target in [('A','Việt A'),('B','Việt B')]:
        canonical.add(TextRow('sub/text.db',table,'same',target))
    database=rebuild_from_release_sources(archive,tmp_path/'derived.db',canonical,lambda:None)
    from ludiglot.core.translation_update import TranslationUpdater
    assert TranslationUpdater._validate_database(database)=='en'
    provider=WahuTranslationProvider(database,canonical_translations=canonical,require_source_english=True)
    def source(table=''):
        return {'x':{'matches':[{'source_json':'text.db','source_namespace':'sub/text.db','source_table':table,
            'text_key':'same','official_en':'English source','official_cn':'原文'}]}}
    assert provider.enrich(source()).vietnamese_matches==0
    good=source('B')
    assert provider.enrich(good).vietnamese_matches==1
    assert good['x']['matches'][0]['official_vi']=='Việt B'


def test_legacy_cache_identity_migration_uses_current_bilingual_files(tmp_path):
    from ludiglot.core.source_records import upgrade_cache_identity
    base=tmp_path/'ConfigDB'; (base/'en/sub').mkdir(parents=True); (base/'zh-Hans/sub').mkdir(parents=True)
    for lang,content in [('en','Source sentence'),('zh-Hans','原文')]:
        with sqlite3.connect(base/lang/'sub/text.db') as conn:
            conn.execute('CREATE TABLE MultiText(Id TEXT,Content TEXT)')
            conn.execute('INSERT INTO MultiText VALUES (?,?)',('key',content))
    data={'x':{'matches':[{'source_json':'text.db','text_key':'key','official_en':'Source sentence','official_zh':'原文'}]}}
    result=upgrade_cache_identity(data,tmp_path)
    assert result['recovered_alias_rows']==1
    assert data['x']['matches'][0]['source_namespace']=='sub/text.db'
    assert data['x']['matches'][0]['source_table']=='MultiText'


def test_ambiguous_legacy_cache_does_not_pick_first_table(tmp_path):
    from ludiglot.core.source_records import upgrade_cache_identity
    base=tmp_path/'ConfigDB'; (base/'en').mkdir(parents=True); (base/'zh-Hans').mkdir()
    for lang,content in [('en','Source sentence'),('zh-Hans','原文')]:
        with sqlite3.connect(base/lang/'text.db') as conn:
            for table in ('First','Second'):
                conn.execute(f'CREATE TABLE {table}(Id TEXT,Content TEXT)')
                conn.execute(f'INSERT INTO {table} VALUES (?,?)',('key',content))
    data={'x':{'matches':[{'source_json':'text.db','text_key':'key','official_en':'Source sentence','official_zh':'原文'}]}}
    assert upgrade_cache_identity(data,tmp_path)['ambiguous_alias_rows']==1
    assert 'source_table' not in data['x']['matches'][0]


def test_alias_interning_and_compact_proof_preserve_evidence_without_duplicate_payloads():
    from ludiglot.core.source_records import intern_match_aliases,pack_provenance
    first={'source_json':'text.db','text_key':'key','official_en':'English source','official_vi':'Bản Việt',
           'translation_proof':{'kind':'raw_bilingual_exact','en_sha256':'source'}}
    data={'a':{'matches':[first]},'b':{'matches':[dict(first)]}}
    intern_match_aliases(data)
    assert data['a']['matches'][0] is data['b']['matches'][0]
    pack_provenance(data)
    assert 'translation_proof' not in first
    identifier=first['translation_proof_id']
    assert data['__ossmoc_provenance__']['proofs'][identifier]['kind']=='raw_bilingual_exact'
    before=json.dumps(data,sort_keys=True)
    pack_provenance(data)
    assert json.dumps(data,sort_keys=True)==before


def test_language_root_sub_namespace_does_not_collapse_same_leaf_filename(tmp_path):
    from ludiglot.core.source_records import database_namespace
    assert database_namespace(tmp_path/'ConfigDB/en','text.db')=='text.db'
    assert database_namespace(tmp_path/'ConfigDB/Extra/en','text.db')=='extra/text.db'
    assert database_namespace(tmp_path/'TextMap/en','text.db')=='textmap/text.db'
