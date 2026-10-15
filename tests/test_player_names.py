import copy

import pytest

from ludiglot.core.player_names import normalize_player_name, replace_player_name
from ludiglot.core.auto_wuwa import AutoDocument, AutoFrameResult, AutoTextBlock, AutoTextIndex, personalize_frame
from ludiglot.core.desktop_models import migrate_desktop_settings
from ludiglot.core.text_builder import expand_player_name_placeholder, has_player_name_placeholder


@pytest.mark.parametrize('token', ['{PlayerName}', '{player-name}', '{PLAYER_NAME}',
    '{Cus:Var, VarType=Global Key=main_team_name}'])
def test_known_tokens_are_literal_single_pass(token):
    assert replace_player_name('Chào '+token, r'Oss\1') == r'Chào Oss\1'
    assert expand_player_name_placeholder(token, 'Ossmoc') == 'Ossmoc'
    assert has_player_name_placeholder(token)
    assert replace_player_name(token, '{other}') == '{other}'


def test_unknown_tokens_and_literal_rover_are_not_replaced():
    source = 'Rover, {PlayerNames} {player-id} {0} {Cus:Var, VarType=Global Key=other}'
    assert replace_player_name(source, 'Ossmoc') == source


def test_name_normalization_and_settings_round_trip():
    assert normalize_player_name('  Nguyễn\n  A\u202e\x00 ') == 'Nguyễn A'
    assert normalize_player_name(None) == ''
    settings = migrate_desktop_settings({'auto_wuwa': {'player_name': '  Ossmoc  '}, 'keep': 42})
    assert settings['auto_wuwa']['player_name'] == 'Ossmoc'
    assert settings['keep'] == 42
    assert migrate_desktop_settings(settings) == settings
    assert len(normalize_player_name('a'*100)) == 40


def test_render_every_kind_document_and_title_without_changing_source():
    blocks = tuple(AutoTextBlock(kind, (1, 2, 100, 40), 'Source {PlayerName}',
        'Chào {player-name}', ('lang.db', kind))
        for kind in ('dialogue', 'choices', 'transition', 'glossary'))
    raw = AutoFrameResult(blocks, AutoDocument(('lang.db', 'doc'), '{PLAYER_NAME}',
        'Thư gửi {PlayerName}', source_body='Original {PlayerName}'))
    rendered = personalize_frame(raw, 'Ossmoc')
    assert all(b.target == 'Chào Ossmoc' for b in rendered.blocks)
    assert all(b.source == 'Source {PlayerName}' for b in rendered.blocks)
    assert rendered.document.title == 'Ossmoc'
    assert rendered.document.target == 'Thư gửi Ossmoc'
    assert rendered.document.source_body == raw.document.source_body
    assert personalize_frame(raw, 'Tên khác').blocks[0].target == 'Chào Tên khác'
    assert raw.blocks[0].target == 'Chào {player-name}'
    assert personalize_frame(raw).blocks[0].target == 'Chào Rover'


def test_template_observed_name_is_fallback_but_configured_name_wins():
    row = {'source_json': 'lang.db', 'text_key': 'Main_Test_1_1',
        'official_en': 'There you are at last, {player-name}.',
        'official_vi': '{player_name}, cuối cùng cũng đến rồi.',
        'translation_source': 'wahu_exact_id_reviewed'}
    db = {'test': {'matches': [row]}}
    original = copy.deepcopy(db)
    index = AutoTextIndex(db)
    record = index.resolve('There you are at last, Alice.', 'dialogue')
    assert record.vietnamese == 'Alice, cuối cùng cũng đến rồi.'
    raw = AutoFrameResult((AutoTextBlock('dialogue', (0, 0, 100, 40), record.english,
        record.vietnamese, record.identity, target_template=record.target_template),))
    assert personalize_frame(raw).blocks[0].target.startswith('Alice,')
    assert personalize_frame(raw, 'Ossmoc').blocks[0].target.startswith('Ossmoc,')
    assert db == original
