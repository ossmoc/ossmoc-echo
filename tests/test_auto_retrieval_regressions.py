"""Private screenshots stay out of tests; reproduce their failure mechanisms."""
from PIL import Image, ImageDraw

from ludiglot.core.auto_wuwa import AutoTextIndex, choice_border
from ludiglot.core.story_catalog import dialogue_scene


def record(key, english, target, source='one.db', speaker=''):
    return {'text_key': key, 'official_en': english, 'official_vi': target,
            'source_json': source, 'translation_source': 'wahu_exact_id_reviewed',
            'verified_speaker': speaker}


def index(*rows):
    return AutoTextIndex({str(n): {'matches': [row]} for n, row in enumerate(rows)})


def seed(line=1, source='one.db'):
    return record(f'Main_Test_3_6_20_{line}', 'A sufficiently distinctive preceding English sentence.', 'Mốc', source)


def test_two_word_dialogue_uses_observed_exact_text_and_forward_context():
    search = index(seed(), record('Main_Test_3_6_20_4', '...Very well.', 'Được.'))
    assert search.resolve('Very well.', 'dialogue') is None
    search.resolve(seed()['official_en'], 'dialogue')
    assert search.resolve('...Very well.', 'dialogue').identity[1] == 'Main_Test_3_6_20_4'
    assert search.resolve('Very well', 'dialogue').vietnamese == 'Được.'  # repeat current line
    assert search.resolve('Very wet', 'dialogue') is None
    assert search.resolve('Different words', 'dialogue') is None


def test_forward_context_can_cross_into_existing_next_scene_not_distant_scene():
    search = index(seed(7), record('Main_Test_3_6_21_1', 'Yes.', 'Có.'),
                   record('Main_Test_3_6_23_1', 'No.', 'Không.'))
    search.set_context('Main_Test_3_6_20_7')
    assert search.resolve('No', 'dialogue') is None
    assert search.resolve('Yes', 'dialogue').identity[1] == 'Main_Test_3_6_21_1'
    assert not search.valid_context('Main_Test_3_6_22_1')


def test_short_line_stale_context_group_only_and_wrong_source_fail_closed():
    search = index(seed(20), record('Main_Test_3_6_20_1', 'Very well.', 'A'),
                   record('Main_Test_3_6_20_23', 'Very well.', 'B', source='other.db'))
    search.set_story_group('Main_Test_3_6')
    assert search.resolve('Very well', 'dialogue') is None
    search.set_context('Main_Test_3_6_20_20')
    assert search.resolve('Very well', 'dialogue') is None


def test_branches_with_same_short_text_remain_ambiguous_even_if_translation_same():
    search = index(seed(), record('Main_Test_3_6_20_2', 'Yes', 'Có'),
                   record('Main_Test_3_6_20_3', 'Yes', 'Có'))
    search.set_context('Main_Test_3_6_20_1')
    assert search.resolve('Yes', 'dialogue') is None
    assert search.context_hint == 'Main_Test_3_6_20_1'


def test_line_limit_counts_real_candidates_and_numeric_gap():
    search = index(seed(), record('Main_Test_3_6_20_8', 'Very well', 'Được'))
    search.set_context('Main_Test_3_6_20_1')
    assert search.resolve('Very well', 'dialogue') is None


def test_short_memo_is_invalidated_by_line_anchor_not_only_scene():
    search = index(seed(), record('Main_Test_3_6_20_3', 'Very well', 'Được'), seed(15))
    search.set_context('Main_Test_3_6_20_1')
    assert search.resolve('Very well', 'dialogue')
    search.set_context('Main_Test_3_6_20_15')
    assert search.resolve('Very well', 'dialogue') is None


def test_short_ambiguous_english_elsewhere_does_not_override_local_unique_record():
    search = index(seed(), record('Main_Test_3_6_20_3', 'Very well', 'Được'),
                   record('Main_Other_3_7_10_1', 'Very well', 'Rất tốt'))
    search.set_context('Main_Test_3_6_20_1')
    assert search.resolve('Very well', 'dialogue').vietnamese == 'Được'


def test_npc_family_is_verified_and_player_template_preserves_observed_name():
    row = record('GNNPC_Test_81_1', 'Paragon Qingxiao, {PlayerName}, I wish you both a safe journey.',
                 'Qingxiao, {PlayerName}, chúc bình an.')
    search = index(row)
    assert dialogue_scene(row['text_key']) == ('GNNPC_Test', 81, 1)
    assert search.resolve('Paragon Qingxiao, Ossmoc, I wish you both a safe journey.', 'dialogue').vietnamese == 'Qingxiao, Ossmoc, chúc bình an.'
    row['translation_source'] = 'official_english_fallback'
    assert not index(row).resolve(row['official_en'], 'dialogue')


def test_dim_unselected_answer_needs_enclosing_edge_and_darker_interior():
    image = Image.new('RGB', (1920, 1080), '#334466')
    draw = ImageDraw.Draw(image)
    draw.rectangle((1226,638,1700,718),fill='#25282d',outline='#66696b',width=2)
    assert choice_border(image, (1289,649,360,58))
    lone = Image.new('RGB', (1920,1080), '#25282d')
    ImageDraw.Draw(lone).line((1226,638,1226,718),fill='#66696b',width=2)
    assert not choice_border(lone,(1289,649,360,58))
    assert not choice_border(Image.new('RGB',(1920,1080),'#66696b'),(1289,649,360,58))


def test_answer_visible_with_short_dialogue_does_not_advance_past_spoken_line(monkeypatch):
    from types import SimpleNamespace
    from ludiglot.core import auto_wuwa
    from ludiglot.core.desktop_models import AUTO_DEFAULTS
    search = index(seed(), record('Main_Test_3_6_20_3', 'Very well.', 'Được'),
                   record('Main_Test_3_6_20_5', 'We should leave after taking another look around.', 'Đáp án'))
    monkeypatch.setattr(auto_wuwa,'dialogue_chrome',lambda _:True)
    monkeypatch.setattr(auto_wuwa,'choice_border',lambda *_:True)
    class Engine:
        def recognize_pipeline(self,crop,**kwargs):
            text = ('We should leave after taking another look around.' if crop.width in (614,615)
                    else 'Very well.' if crop.width == 1536 else '')
            x,y,w,h = (80,170,350,28) if crop.width in (614,615) else (600,28,140,29)
            return SimpleNamespace(boxes=[{'text':text,'box':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]}] if text else [])
    processor=auto_wuwa.AutoWuwaProcessor(search)
    result=processor.process(Image.new('RGB',(1920,1080),'#334466'),Engine(),
                             {**AUTO_DEFAULTS,'context_hint':'Main_Test_3_6_20_1'},force=True)
    assert {b.kind for b in result.blocks} == {'dialogue','choices'}
    assert search.context_hint == 'Main_Test_3_6_20_3'
