from types import SimpleNamespace
from ludiglot.core.auto_wuwa import AutoTextIndex
from ludiglot.core.story_catalog import dialogue_scene,story_catalog


def record(key,en='Distinct verified English words for this story.',vi='Bản Việt',verified=True,source='lang.db'):
    return {'text_key':key,'official_en':en,'official_vi':vi,'source_json':source,
            'translation_source':'wahu_exact_id_reviewed' if verified else 'official_english_fallback'}


def index(*records):
    return AutoTextIndex({str(i):{'matches':[r]} for i,r in enumerate(records)})


def test_scene_parses_side_quests_without_fake_version_and_versioned_main():
    assert dialogue_scene('Side_DXTS_4_5')==('Side_DXTS',4,5)
    assert dialogue_scene('Main_Mengzhou_3_6_20_1')==('Main_Mengzhou_3_6',20,1)
    assert dialogue_scene('Main_Mengzhou_3_6_2_20_1')==('Main_Mengzhou_3_6_2',20,1)
    assert dialogue_scene('QuestName') is None


def test_catalog_has_only_verified_main_groups_with_natural_order_and_seed():
    search=index(record('Main_Mengzhou_3_6_20_1'),record('Main_Mengzhou_3_6_2_1'),
                 record('Main_LahaiRoi_3_1_12_1'),record('Side_DXTS_4_5'),
                 record('Main_Fake_9_9_1_1',verified=False))
    groups=search.story_catalog
    assert [g.version for g in groups]==['3.1','3.6']
    assert groups[1].seed=='Main_Mengzhou_3_6_2_1' and groups[1].count==2
    assert groups[0].label=='3.1 · Lahai-Roi'
    assert all('Main_' not in g.label for g in groups)


def test_version_comes_from_first_numeric_pair_not_greedy_trailing_digits():
    groups=story_catalog([SimpleNamespace(identity=('a','Main_Honami_2_8_2_7_1')),
                          SimpleNamespace(identity=('a','Main_Mengzhou_3_6_2_20_1'))])
    assert [g.version for g in groups]==['2.8','3.6']


def test_same_label_is_distinct_parts_without_invented_mission_titles():
    groups=story_catalog([SimpleNamespace(identity=('a','Main_Mengzhou_3_6_20_1')),
                          SimpleNamespace(identity=('a','Main_Mengzhou_3_6_2_20_1'))])
    assert groups[0].label.endswith('phần 1') and groups[1].label.endswith('phần 2')


def test_selected_story_scope_is_advisory_and_exact_elsewhere_always_wins():
    first=record('Main_Mengzhou_3_6_20_1','This is the correct English dialogue in Mengzhou.','Mengzhou')
    other=record('Side_DXTS_4_5','This is the correct English dialogue in another quest.','Nhiệm vụ khác')
    search=index(first,other); assert search.set_story_group('Main_Mengzhou_3_6')
    assert search._scope_keys and search.resolve(other['official_en'],'dialogue').vietnamese=='Nhiệm vụ khác'
    assert search.context_hint==other['text_key']
    assert not search.set_story_group('Main_Missing_99_99')


def test_side_context_supports_nearby_verified_lines_but_not_guessed_ids():
    first=record('Side_DXTS_4_5','Help me find the old forgotten letter in the garden.','Câu đầu')
    second=record('Side_DXTS_5_1','Please bring that old forgotten letter back to my house.','Câu tiếp')
    search=index(first,second); assert search.set_context(first['text_key'])
    assert len(search._scope_keys)==2
    assert not search.valid_context('Side_DXTS_6_1')
    assert search.resolve('Please bring that old forgotten letter back to my house.','dialogue').vietnamese=='Câu tiếp'


def test_changing_selected_group_invalidates_memo_and_can_clear_without_context():
    search=index(record('Main_Mengzhou_3_6_20_1'))
    search._memo[('test',)]=None
    assert search.set_story_group('Main_Mengzhou_3_6') and not search._memo and search._scope_keys
    assert search.set_story_group('') and search._scope_signature is None and not search._scope_keys


def test_ambiguous_source_identity_never_becomes_a_valid_context_seed():
    key='Side_DXTS_4_5'
    search=index(record(key,source='one.db'),record(key,vi='Khác',source='two.db'))
    assert not search.valid_context(key)
