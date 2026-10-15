import sqlite3
import zipfile

import pytest

from ludiglot.core.release_sources import rebuild_from_release_sources


def write_sources(path, sources):
    with zipfile.ZipFile(path,'w') as archive:
        for name,text in sources.items():
            archive.writestr('editor/build_support/TextSource/'+name,text)
    return path


def test_latest_explicit_source_and_canonical_target_only(tmp_path):
    archive = write_sources(tmp_path/'release.zip', {
        '34_source/3.4_part01.txt':'@@ lang_text.db||Text||1\nEN: Old sentence.\nVI: Do not use this target\n',
        '36_source/3.6_part01.txt':'@@ lang_text.db||Text||1\nEN: Latest sentence.\nSecond line.\n\n',
        '36_translations/3.6_part01.txt':'@@ lang_text.db||Text||1\nEN: Wrong sentence.\n',
    })
    target = rebuild_from_release_sources(archive,tmp_path/'derived.db',
                                          {('lang_text.db','1'):'Bản Việt tên Anh'},lambda:None)
    with sqlite3.connect(target) as connection:
        assert connection.execute('SELECT en,vi FROM entries').fetchall() == [('Latest sentence.\nSecond line.','Bản Việt tên Anh')]


@pytest.mark.parametrize('text',[
    '@@ lang_text.db||Text||1\nEN: First\n@@ lang_text.db||Other||1\nEN: Second',
    '@@ lang_text.db||Text||1\nEN: First\nEN: Duplicate',
    '@@ lang_text.db||Text||1\nVI: Missing English',
    '@@ ../lang_text.db||Text||1\nEN: Unsafe identity',
])
def test_ambiguous_or_missing_source_never_becomes_translation(tmp_path,text):
    archive = write_sources(tmp_path/'release.zip',{'36_source/3.6_part01.txt':text})
    with pytest.raises(ValueError,match='Không có ID'):
        rebuild_from_release_sources(archive,tmp_path/'derived.db',{('lang_text.db','1'):'Bản Việt'},lambda:None)
    assert not (tmp_path/'derived.db').exists()


def test_cancellation_does_not_create_database(tmp_path):
    archive = write_sources(tmp_path/'release.zip',{'36_source/3.6_part01.txt':'@@ lang_text.db||Text||1\nEN: Hi'})
    def cancel():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        rebuild_from_release_sources(archive,tmp_path/'derived.db',{},cancel)
    assert not (tmp_path/'derived.db').exists()


@pytest.mark.parametrize('mutation,accepted',[
    ('valid',True),('wrong_vi',False),('same_sibling_target',False),
    ('wrong_version',False),('no_mask',False),('ambiguous',False),
])
def test_story_redirect_requires_explicit_multi_language_proof(tmp_path,mutation,accepted):
    key='Main_Mengzhou_3_6_90_1'
    archive=tmp_path/'release.zip'
    with zipfile.ZipFile(archive,'w') as package:
        original='Original English sentence.' if mutation=='no_mask' else '********'
        package.writestr('editor/build_support/TextSource/36_source/3.6_part01.txt',
            f'@@ lang_multi_text.db||MultiText||{key}\nEN: {original}\nCN: ****\n')
        story=f'@@ lang_multi_text_1sthalf.db||MultiText||{key}\nCN: 原文内容\nEN: Original English sentence.\nVI: '+('Wrong target' if mutation=='wrong_vi' else 'Bản Việt chính')+'\n'
        if mutation=='ambiguous':
            story+=f'@@ lang_multi_text_1sthalf.db||MultiText||{key}\nCN: 其他内容\nEN: Different English sentence.\nVI: Bản Việt chính\n'
        version='3.5' if mutation=='wrong_version' else '3.6'
        group='35' if mutation=='wrong_version' else '36'
        package.writestr(f'editor/build_support/Wahu/data/translations_v{group}_story/01_COT_TRUYEN_CHINH_{version}_CHUA_DICH_PART_01.txt',story)
    canonical={('lang_multi_text.db',key):'Bản Việt chính',
               ('lang_multi_text_1sthalf.db',key):'Bản Việt chính' if mutation=='same_sibling_target' else 'Bản khác'}
    target=tmp_path/'derived.db'
    if not accepted and mutation!='no_mask':
        with pytest.raises(ValueError,match='Không có ID'):
            rebuild_from_release_sources(archive,target,canonical,lambda:None)
        return
    rebuild_from_release_sources(archive,target,canonical,lambda:None)
    with sqlite3.connect(target) as connection:
        proofs=connection.execute('SELECT * FROM release_source_proofs').fetchall()
    assert bool(proofs)==accepted
    if not accepted:
        return
    from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider
    def source(en='Original English sentence.',cn='原文内容',table='MultiText'):
        return {'line':{'matches':[{'text_key':key,'source_json':'lang_multi_text.db',
            'source_table':table,'official_en':en,'official_cn':cn}]}}
    provider=WahuTranslationProvider(target,require_source_english=True)
    good=source()
    assert provider.enrich(good).vietnamese_matches==1
    assert good['line']['matches'][0]['translation_source']=='wahu_verified_source_move'
    for bad in (source(cn='不同原文'),source(en='Different English'),source(cn=''),source(table='OtherTable')):
        assert provider.enrich(bad).vietnamese_matches==0
