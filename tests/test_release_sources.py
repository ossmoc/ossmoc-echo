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
    ('valid',True),('wrong_vi',False),('same_sibling_target',True),
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


@pytest.mark.parametrize('same_target', [False, True])
def test_story_proof_accepts_reviewed_grammar_only_with_exact_chinese(tmp_path, same_target):
    from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider
    key = 'Main_Mengzhou_3_6_94_1'
    old = 'Got it in my head to learn.'
    current = 'Got it into my head to learn.'
    archive = tmp_path / 'release.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('editor/build_support/TextSource/36_source/3.6_part01.txt',
                        f'@@ lang_multi_text.db||MultiText||{key}\nEN: ****\nCN: ****\n')
        package.writestr('editor/build_support/Wahu/data/translations_v36_story/01_COT_TRUYEN_CHINH_3.6_CHUA_DICH_PART_01.txt',
                        f'@@ lang_multi_text_1sthalf.db||MultiText||{key}\nEN: {old}\nCN: 原文内容\nVI: Tôi muốn học.\n')
    database = rebuild_from_release_sources(archive, tmp_path / 'derived.db', {
        ('lang_multi_text.db', key): 'Tôi muốn học.',
        ('lang_multi_text_1sthalf.db', key): 'Tôi muốn học.' if same_target else 'Bản khác.',
    }, lambda: None)
    provider = WahuTranslationProvider(database, require_source_english=True)
    def sample(en=current, cn='原文内容', table='MultiText'):
        return {'line': {'matches': [{'source_json': 'lang_multi_text.db', 'text_key': key,
            'official_en': en, 'official_zh': cn, 'source_table': table}]}}
    good = sample()
    assert provider.enrich(good).vietnamese_matches == 1
    assert good['line']['matches'][0]['official_vi'] == 'Tôi muốn học.'
    # Repeated updates must read original Chinese, never mirrored Vietnamese.
    assert provider.enrich(good).vietnamese_matches == 1
    for bad in (sample(cn='不同原文'), sample(cn=''), sample(table='Other'),
                sample(en='Got it into my head not to learn.'),
                sample(en='Got it into my head to fight.'),
                sample(en='Got it into my head to learn 2 things.')):
        assert provider.enrich(bad).vietnamese_matches == 0


def test_grammar_variants_without_story_proof_remain_rejected(tmp_path):
    from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider
    database = tmp_path / 'normal.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE entries(dbfile TEXT,key TEXT,en TEXT,vi TEXT,status TEXT)')
        connection.execute('INSERT INTO entries VALUES (?,?,?,?,?)',
            ('lang_multi_text.db', 'Main_Test_1_1', 'Got it in my head to learn.', 'Tôi muốn học.', 'final'))
    data = {'line': {'matches': [{'source_json': 'lang_multi_text.db', 'text_key': 'Main_Test_1_1',
                                 'official_en': 'Got it into my head to learn.', 'official_zh': '原文内容'}]}}
    assert WahuTranslationProvider(database, require_source_english=True).enrich(data).vietnamese_matches == 0
