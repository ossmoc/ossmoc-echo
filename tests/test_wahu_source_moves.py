"""Split-file repairs require recorded original text, never ID-only guesses."""
import sqlite3
import pytest
from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider


def fixture_db(path, *, original=None, current=None):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)")
        c.execute("INSERT INTO meta VALUES('name_mode','en')")
        c.execute("""CREATE TABLE entries_full(dbfile TEXT,tbl TEXT,key TEXT,cn TEXT,en TEXT,vi TEXT,status TEXT,
            conv TEXT,version TEXT,source_pair_count INTEGER,legacy_origin TEXT,sync_confidence TEXT,synced_at TEXT)""")
        c.execute("""CREATE TABLE source_sync_changes(dbfile TEXT,tbl TEXT,key TEXT,old_cn TEXT,old_en TEXT,
            new_cn TEXT,new_en TEXT,vi_at_sync TEXT,synced_at TEXT)""")
        en = original or "Suisui... are you sure you won't stay a few more days?"
        cn = "姐姐……真的不再多留几天吗？"
        vi = "Chị… thật sự không ở lại thêm vài ngày nữa sao?"
        key = "Main_Mengzhou_3_6_20_1"
        common = ('MultiText',key)
        for db,en_value,cn_value,vi_value,status in [
                ('lang_multi_text.db','*'*len(en),'*'*len(cn),vi,'translated'),
                ('lang_multi_text_1sthalf.db',current or en,cn,'','untranslated')]:
            c.execute("INSERT INTO entries_full VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                db,*common,cn_value,en_value,vi_value,status,'Main_Mengzhou_3_6_20','3.6',1,
                'ACTIVE_UNAMBIGUOUS','EXACT_PAIR','batch-time'))
        c.execute("CREATE VIEW entries AS SELECT * FROM entries_full")
        c.execute("INSERT INTO source_sync_changes VALUES(?,?,?,?,?,?,?,?,?)", (
            'lang_multi_text.db',*common,cn,en,'*'*len(cn),'*'*len(en),vi,'batch-time'))
    row = {'source_json':'lang_multi_text_1sthalf.db','text_key':key,'official_en':current or en,
           'official_cn':en,'official_vi':'','official_zh':cn,'translation_source':'official_english_fallback'}
    return row


@pytest.mark.parametrize('old,current',[
    ('Got it in my head to roam the mountains.', 'Got it into my head to roam the mountains.'),
    ("I would've prepared a pot of tea.", 'I would have prepared a pot of tea.'),
    ('But Nhận Ngẫus alone could not hold back this tide.', 'But Bladepuppets alone could not hold back this tide.'),
])
def test_proven_move_accepts_bounded_grammar_or_reviewed_terminology(tmp_path,old,current):
    path=tmp_path/'project.db'; row=fixture_db(path,original=old,current=current)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO entries_full VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(
            'lang_multi_text.db','MultiText','Entity_term','词','Bladepuppet','Nhận Ngẫu','reviewed','', '3.6',1,'ACTIVE_UNAMBIGUOUS','EXACT_PAIR','batch-time'))
    WahuTranslationProvider(path,require_source_english=True).enrich({'a':{'matches':[row]}},source_moves_only=True)
    assert row['translation_source']=='wahu_verified_source_move'
    assert row['official_vi']


@pytest.mark.parametrize('old,current',[
    ('It will be safe.', 'It will not be safe.'),
    ('Bring 2 pieces.', 'Bring 3 pieces.'),
    ('Yangyang has left.', 'Suisui has left.'),
    ('The guard leaves.', 'The guard stays.'),
    ('But Nhận Ngẫus alone could not hold back this tide.', 'But Bladepuppets alone could not hold back this tide.'),
])
def test_same_chinese_and_id_cannot_justify_arbitrary_english_change(tmp_path,old,current):
    path=tmp_path/'project.db'; row=fixture_db(path,original=old,current=current)
    WahuTranslationProvider(path).enrich({'a':{'matches':[row]}},source_moves_only=True)
    assert row['official_vi']==''


def test_conflicting_reviewed_term_alias_is_rejected(tmp_path):
    path=tmp_path/'project.db'; row=fixture_db(path,
        original='But Nhận Ngẫus alone could not hold back this tide.',
        current='But Bladepuppets alone could not hold back this tide.')
    with sqlite3.connect(path) as c:
        for key,en in [('Entity_one','Bladepuppet'),('Entity_two','Different term')]:
            c.execute("INSERT INTO entries_full VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                'lang_multi_text.db','MultiText',key,'词',en,'Nhận Ngẫu','reviewed','', '3.6',1,'ACTIVE_UNAMBIGUOUS','EXACT_PAIR','batch-time'))
    WahuTranslationProvider(path).enrich({'a':{'matches':[row]}},source_moves_only=True)
    assert row['official_vi']==''


def test_player_role_propagates_only_with_verified_source_move(tmp_path):
    path=tmp_path/'project.db'; row=fixture_db(path)
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE entries_full ADD COLUMN speaker TEXT DEFAULT ''")
        c.execute("UPDATE entries_full SET speaker='Rover' WHERE dbfile='lang_multi_text.db'")
    WahuTranslationProvider(path,require_source_english=True).enrich({'a':{'matches':[row]}},source_moves_only=True)
    assert row['verified_speaker']=='Rover'
    row['official_en']='Unsupported other English'
    WahuTranslationProvider(path,require_source_english=True).enrich({'a':{'matches':[row]}},source_moves_only=True)
    assert 'verified_speaker' not in row


def test_proven_move_repairs_loaded_cache_and_preserves_originals(tmp_path):
    path=tmp_path/'project.db'; row=fixture_db(path)
    stats=WahuTranslationProvider(path,require_source_english=True).enrich({'a':{'matches':[row]}},source_moves_only=True)
    assert stats.verified_source_moves == 1
    assert row['translation_source']=='wahu_verified_source_move'
    assert row['translation_origin']==['lang_multi_text.db',row['text_key']]
    assert row['official_vi'].startswith('Chị')
    assert row['official_zh']=='姐姐……真的不再多留几天吗？'
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT vi FROM entries WHERE dbfile='lang_multi_text_1sthalf.db'").fetchone()[0]==''


@pytest.mark.parametrize('change',[
    "DELETE FROM source_sync_changes",
    "UPDATE source_sync_changes SET old_en='Other English text'",
    "UPDATE source_sync_changes SET old_cn='Other Chinese text'",
    "UPDATE source_sync_changes SET vi_at_sync='Different translation'",
    "UPDATE source_sync_changes SET new_en='Not the current mask'",
    "UPDATE source_sync_changes SET synced_at='other-batch'",
    "UPDATE entries_full SET version='3.7' WHERE dbfile LIKE '%1sthalf%'",
    "UPDATE entries_full SET conv='Main_Other' WHERE dbfile LIKE '%1sthalf%'",
    "UPDATE entries_full SET source_pair_count=2",
    "UPDATE entries_full SET sync_confidence='GUESSED'",
    "UPDATE entries_full SET status='untranslated' WHERE dbfile='lang_multi_text.db'",
    "UPDATE meta SET v='hv'",
    "INSERT INTO entries_full SELECT 'lang_multi_text_2ndhalf.db',tbl,key,cn,en,vi,status,conv,version,source_pair_count,legacy_origin,sync_confidence,synced_at FROM entries_full WHERE dbfile LIKE '%1sthalf%'",
    "INSERT INTO entries_full SELECT dbfile,tbl,key,cn,en,'Conflicting reviewed target','translated',conv,version,source_pair_count,legacy_origin,sync_confidence,synced_at FROM entries_full WHERE dbfile='lang_multi_text.db'",
])
def test_unproven_or_conflicting_moves_fail_closed(tmp_path,change):
    path=tmp_path/'project.db'; row=fixture_db(path)
    with sqlite3.connect(path) as c: c.execute(change)
    WahuTranslationProvider(path).enrich({'a':{'matches':[row]}},source_moves_only=True)
    assert row['official_vi']==''


def test_changed_official_english_is_not_rebound(tmp_path):
    path=tmp_path/'project.db'; row=fixture_db(path); row['official_en']='Changed game content'
    WahuTranslationProvider(path).enrich({'a':{'matches':[row]}})
    assert row['official_vi']==''


def test_repair_only_leaves_existing_canonical_translations_untouched(tmp_path):
    path=tmp_path/'project.db'; row=fixture_db(path)
    other={'source_json':'unrelated.db','text_key':'existing','official_vi':'Existing English-name target',
           'translation_source':'wahu_exact_id_reviewed'}
    WahuTranslationProvider(path).enrich({'a':{'matches':[row,other]}},source_moves_only=True)
    assert other['official_vi']=='Existing English-name target'
