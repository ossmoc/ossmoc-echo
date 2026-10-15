"""Forward context is evidence scope, never permission to invent a sentence."""
import pytest

from ludiglot.core.auto_wuwa import AutoTextIndex
from ludiglot.core.context_match import choose_forward, evidence_words


def row(line, english, *, scene=20, group='Main_Test_3_6', source='one.db', verified=True):
    return {'text_key': f'{group}_{scene}_{line}', 'source_json': source,
        'official_en': english, 'official_vi': f'Bản Việt {scene}/{line}',
        'translation_source': 'wahu_exact_id_reviewed' if verified else 'official_english_fallback'}


def index(*rows):
    anchor = row(1, 'This distinctive sentence establishes our current conversation.')
    search = AutoTextIndex({str(n): {'matches': [r]} for n, r in enumerate((anchor,)+rows)})
    return search, anchor['text_key']


def test_contractions_and_ordered_words_from_user_example():
    full = 'We are what we want to be.'
    assert evidence_words("We're what we want to be.") == evidence_words(full)
    assert choose_forward('we ... what ... want to be', [full]) == 0
    assert choose_forward('we what be', [full]) is None
    assert choose_forward('be want what we', [full]) is None


def test_saved_or_manually_selected_id_needs_a_fresh_observed_anchor():
    search, anchor = index(row(3, 'The burning walls around the library have collapsed.'))
    search.set_context(anchor)
    assert search.resolve('burning library', 'dialogue') is None
    assert search.resolve(search._by_id[anchor][0].english, 'dialogue')
    assert search.resolve('burning library', 'dialogue')
    search.set_context('')
    search.set_context(anchor)
    assert search.resolve('burning library', 'dialogue') is None


@pytest.mark.parametrize('query', ['burning library', 'the burning ... library',
    'burning ... library ... collapsed', 'the burning ... librarx ... collapsed'])
def test_two_distinctive_exact_anchors_allow_gaps_and_one_supplementary_typo(query):
    sentence = 'The burning walls around the ancient library have collapsed.'
    assert choose_forward(query, [sentence, 'Please rest here until the guards arrive.']) == 0


@pytest.mark.parametrize('query', ['', 'we what', 'we want to', 'library library',
    'library burning', 'burning innocent library', 'burnng librarx', '123', 'a'*5000])
def test_insufficient_reordered_noisy_or_repeated_evidence_waits(query):
    assert choose_forward(query, ['The burning walls around the library have collapsed.']) is None


def test_only_one_typo_not_two_and_no_fuzzy_only_anchors():
    full = 'Ancient archives contain forbidden knowledge inside a ruined monastery.'
    assert choose_forward('archives forbiden monastery', [full]) == 0
    assert choose_forward('archives forbiden monasterx', [full]) is None


@pytest.mark.parametrize('query', ['burning library', 'The burning library has collapsed'])
def test_same_evidence_in_two_branches_waits_instead_of_choosing_shorter_or_first(query):
    lines = ['The burning library has collapsed near the eastern gate.',
             'The burning library has collapsed beside the northern tower.']
    assert choose_forward(query, lines) is None
    assert choose_forward(query, lines[::-1]) is None
    assert choose_forward('burning library northern tower', lines) == 1


def test_missing_negation_still_counts_as_competing_branch():
    positive = 'We trust the guardians protecting this ancient monastery.'
    negative = 'We do not trust the guardians protecting this ancient monastery.'
    assert choose_forward('guardians ancient monastery', [positive, negative]) is None
    assert choose_forward('not guardians ancient monastery', [positive, negative]) == 1
    assert choose_forward('never guardians ancient monastery', [positive, negative]) is None
    assert choose_forward('guardians ancient monastery', [negative]) is None


def test_numbers_are_critical_and_word_digit_forms_are_equivalent():
    lines = ['The wardens must protect three gates around the city.',
             'The wardens must protect four gates around the city.']
    assert choose_forward('wardens gates', lines) is None
    assert choose_forward('wardens 3 gates', lines) == 0
    assert choose_forward('wardens five gates', lines) is None
    assert choose_forward('wardens gates', lines[:1]) is None


@pytest.mark.parametrize('source', ["We don't trust the ancient guardians.",
    'We dont trust the ancient guardians.', 'We do not trust the ancient guardians.'])
def test_negative_contractions_preserve_not(source):
    full = 'We do not trust the ancient guardians.'
    assert choose_forward(source, [full]) == 0
    assert choose_forward(source, ['We trust the ancient guardians.']) is None


def test_same_words_at_two_ids_are_still_ambiguous():
    assert choose_forward('burning library', ['The burning library has collapsed.']*2) is None


def test_length_difference_cannot_choose_between_same_visible_anchors():
    lines = ['The burning library has collapsed.',
             'The burning library ' + 'distant unrelated scenery ' * 45]
    assert choose_forward('burning library', lines) is None
    assert choose_forward('burning library', lines[::-1]) is None


def test_no_broad_or_missing_anchor_and_no_unverified_data_admission():
    search, anchor = index(row(3, 'The burning walls around the library have collapsed.'))
    assert search.resolve('burning library', 'dialogue') is None
    search.set_story_group('Main_Test_3_6')
    assert search.resolve('burning library', 'dialogue') is None
    search.set_context(anchor, observed=True)
    result = search.resolve('burning library', 'dialogue')
    assert result and result.identity[1].endswith('_20_3') and result.partial
    assert search.context_hint == result.identity[1]
    invalid, anchor = index(row(3, 'The burning walls around the library have collapsed.', verified=False))
    invalid.set_context(anchor, observed=True)
    assert invalid.resolve('burning library', 'dialogue') is None


@pytest.mark.parametrize('kwargs', [{'source': 'other.db'}, {'group': 'Side_Test'},
    {'scene': 19}, {'scene': 22}, {'scene': 20, 'line': 8}, {'scene': 21, 'line': 7}])
def test_backward_distant_wrong_source_or_group_is_not_sparse_scope(kwargs):
    values = {'line': 3, **kwargs}
    search, anchor = index(row(english='The burning walls around the library have collapsed.', **values))
    search.set_context(anchor, observed=True)
    assert search.resolve('burning library', 'dialogue') is None


def test_next_scene_is_allowed_but_window_has_at_most_six_real_successors():
    search, anchor = index(row(2, 'The burning walls around the library have collapsed.', scene=21))
    search.set_context(anchor, observed=True)
    assert search.resolve('burning library', 'dialogue')
    rows = [row(n, f'Distinctive placeholder sentence number {n}.') for n in range(2, 8)]
    search, anchor = index(*rows, row(1, 'The burning walls around the library have collapsed.', scene=21))
    search.set_context(anchor, observed=True)
    assert len(search._forward_window()) == 7
    assert search.resolve('burning library', 'dialogue') is None


def test_sparse_memo_rechecks_full_anchor_after_moving_within_same_scene():
    search, anchor = index(row(3, 'The burning walls around the library have collapsed.'),
        row(12, 'Another complete verified sentence establishes a later line.'))
    search.set_context(anchor, observed=True)
    assert search.resolve('burning library', 'dialogue')
    search.set_context('Main_Test_3_6_20_12')
    assert search.resolve('burning library', 'dialogue') is None
    assert search.context_hint == 'Main_Test_3_6_20_12'


def test_ambiguous_or_empty_ocr_never_advances_anchor():
    search, anchor = index(row(2, 'The burning library has collapsed near the gate.'),
        row(3, 'The burning library has collapsed beside the tower.'))
    search.set_context(anchor, observed=True)
    assert search.resolve('burning library', 'dialogue') is None
    assert search.resolve('', 'dialogue') is None
    assert search.context_hint == anchor
    assert search.resolve('burning library tower', 'dialogue').identity[1].endswith('_20_3')


def test_new_matching_is_not_applied_to_choices_documents_or_transition():
    search, anchor = index(row(3, 'The burning walls around the library have collapsed.'))
    search.set_context(anchor, observed=True)
    for kind in ('choices', 'document', 'transition'):
        assert search.resolve('burning library', kind) is None


def test_existing_full_exact_global_match_wins_over_stale_hint():
    sentence = 'The burning library has collapsed beside the tower.'
    search, anchor = index(row(3, 'The burning library has collapsed near the gate.'),
        row(2, sentence, group='Main_Other_3_6'))
    search.set_context(anchor, observed=True)
    assert search.resolve(sentence, 'dialogue').identity[1].startswith('Main_Other')


def test_old_fuzzy_path_cannot_bypass_missing_negation_competitor():
    search, anchor = index(row(2, 'We trust the ancient guardians around the monastery.'),
        row(3, 'We do not trust the ancient guardians around the monastery.'))
    search.set_context(anchor, observed=True)
    assert search.resolve('We trust the ancient guardians arond the monastery.', 'dialogue') is None
    assert search.context_hint == anchor


def test_old_fragment_path_cannot_ignore_observed_number_or_negation_conflict():
    full = 'The wardens will protect three ancient gates surrounding the city tonight.'
    search, anchor = index(row(2, full))
    search.set_context(anchor, observed=True)
    assert search.resolve('wardens will protect four ancient gates surrounding the city', 'dialogue') is None
    assert search.resolve('wardens will not protect three ancient gates surrounding the city', 'dialogue') is None


def test_canonical_tokens_are_not_reused_for_a_different_decimal_query():
    search, anchor = index(row(2, 'The wardens must protect 3.7 ancient gates.'))
    search.set_context(anchor, observed=True)
    assert search.resolve('wardens 3.7 ancient gates', 'dialogue', remember=False)
    assert search.resolve('wardens 3 7 ancient gates', 'dialogue', remember=False) is None


def test_exact_normalized_punctuation_cannot_bypass_decimal_conflict():
    full = 'The wardens must protect 3.7 ancient gates.'
    search, anchor = index(row(2, full))
    search.set_context(anchor, observed=True)
    assert search.resolve(full, 'dialogue', remember=False)
    assert search.resolve(full.replace('3.7', '3 7'), 'dialogue', remember=False) is None


def test_sparse_success_prioritizes_forward_window_without_global_top_two(monkeypatch):
    from ludiglot.core import auto_wuwa
    search, anchor = index(row(3, 'The burning walls around the library have collapsed.'))
    search.set_context(anchor, observed=True)
    def forbidden(*args, **kwargs):
        raise AssertionError('Sparse success must not run global top-two ranking')
    monkeypatch.setattr(auto_wuwa.process, 'extract', forbidden)
    assert search.resolve('burning library', 'dialogue')


def test_partial_word_match_still_requires_real_dialogue_geometry_and_chrome(monkeypatch):
    from types import SimpleNamespace
    from PIL import Image
    from ludiglot.core import auto_wuwa
    from ludiglot.core.desktop_models import AUTO_DEFAULTS
    positive = 'We trust the ancient guardians around the monastery.'
    search, anchor = index(row(2, positive), row(3, 'We do not trust the ancient guardians around the monastery.'))
    processor = auto_wuwa.AutoWuwaProcessor(search)
    class Engine:
        text = positive
        def recognize_pipeline(self, crop, **kwargs):
            return SimpleNamespace(boxes=[{'text': self.text, 'box':
                [[500,30],[1100,30],[1100,62],[500,62]]}] if crop.width == 1536 else [])
    engine = Engine()
    settings = {**AUTO_DEFAULTS, 'document': False, 'glossary': False, 'choices': False,
                'walking': False, 'transition': False, 'context_hint': anchor, 'light_ocr': False}
    monkeypatch.setattr(auto_wuwa, 'dialogue_chrome', lambda _: True)
    image = Image.new('RGB', (1920,1080), '#334466')
    assert processor.process(image, engine, settings, force=True).blocks
    engine.text = 'We trust the ancient guardians arond the monastery.'
    waiting = processor.process(image, engine, settings, force=True)
    assert not waiting.blocks
    assert waiting.pending_kinds == ('dialogue',)
    assert not waiting.stable_kinds
    assert any('chờ thêm chữ' in line for line in waiting.diagnostics)
    monkeypatch.setattr(auto_wuwa, 'dialogue_chrome', lambda _: False)
    engine.text = 'ancient guardians monastery'
    assert not processor.process(image, engine, settings, force=True).blocks


def test_explicit_wait_clears_old_mask_even_when_visual_ink_is_unchanged(monkeypatch):
    from PIL import Image, ImageDraw
    from ludiglot.core import auto_wuwa
    from ludiglot.core.auto_wuwa import AutoFrameResult, AutoTextBlock, AutoWuwaProcessor, AutoPresenceTracker
    search, _ = index()
    processor, tracker = AutoWuwaProcessor(search), AutoPresenceTracker()
    monkeypatch.setattr(auto_wuwa, 'dialogue_chrome', lambda _: True)
    image = Image.new('RGB', (1920,1080), '#334466')
    draw = ImageDraw.Draw(image)
    for x in range(650,1050,14):
        draw.rectangle((x,896,x+6,916), fill='#f5ecd9')
    block = AutoTextBlock('dialogue', (640,890,430,32),
        'We trust the ancient guardians around the monastery.', 'Bản cũ', ('one.db','Main_Test_3_6_20_2'))
    first = AutoFrameResult((block,), present_kinds=('dialogue',))
    tracker.update(processor._track(first, image))
    waiting = AutoFrameResult(present_kinds=('dialogue',), pending_kinds=('dialogue',),
        observed_texts=(('dialogue', 'We trust the ancient guardians arond the monastery.'),))
    checked = processor._track(waiting, image)
    assert not checked.stable_kinds
    assert not tracker.update(checked).blocks
    assert processor._dialogue_visual is None


@pytest.mark.parametrize('changed', ['Wardens protect four ancient gates around the city.',
    'Wardens do not protect three ancient gates around the city.'])
def test_tracker_cannot_hold_old_text_when_ocr_observes_critical_conflict(changed):
    from ludiglot.core.auto_wuwa import AutoFrameResult, AutoTextBlock, AutoPresenceTracker
    tracker = AutoPresenceTracker()
    block = AutoTextBlock('dialogue', (640,890,430,32),
        'Wardens protect three ancient gates around the city.', 'Bản cũ', ('one.db','Main_Test_3_6_20_2'))
    tracker.update(AutoFrameResult((block,), present_kinds=('dialogue',)))
    assert not tracker.update(AutoFrameResult(present_kinds=('dialogue',),
        observed_texts=(('dialogue', changed),))).blocks
