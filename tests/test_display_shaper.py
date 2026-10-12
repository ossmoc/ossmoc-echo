from ludiglot.core.display_shaper import (
    DisplayPreferences,
    convert_game_html,
    extract_numeric_values_from_context,
    resolve_display_placeholders,
    shape_translation_display,
)


class FakeParamResolver:
    def resolve_values(self, text_key, placeholder_count=0):
        if text_key == "Skill_123_Text":
            return ["12%", "1h 2m"][:placeholder_count]
        return []


class FakeVoiceEventIndex:
    def find_candidates(self, *, text_key, voice_event, limit=1):
        return [voice_event] if text_key == "Audio_Text" else []


def prefs(**kwargs):
    data = {
        "gender_preference": "female",
        "font_en": "ENFont",
        "font_cn": "CNFont",
        "font_size": 14,
        "font_weight_css": "600",
        "line_spacing": 1.3,
        "letter_spacing": 0.5,
    }
    data.update(kwargs)
    return DisplayPreferences(**data)


def test_vietnamese_target_preserves_english_rover_and_time_units():
    result = {"matches": [{"text_key": "Test", "official_en": "Hello {PlayerName}", "official_cn": "Chào {PlayerName} {0}"}], "_ocr_text": "Hello Rover 2h"}
    model = shape_translation_display(result, preferences=prefs(target_language="vi"))
    assert "Rover" in model.target.display_text
    assert "漂泊者" not in model.target.display_text
    assert "小时" not in model.target.display_text


def test_extract_numeric_values_from_context_normalizes_tokens():
    assert extract_numeric_values_from_context("CD 1h  2m, +12%, v1.2.3, 10 MB") == [
        "1h 2m",
        "12%",
        "1.2.3",
        "10MB",
    ]


def test_resolve_display_placeholders_handles_player_input_gender_and_unknowns():
    text = "{PlayerName} {Cus:Var, VarType=Global Key=main_team_name} {Cus:Ipt,Touch=tap,Gamepad=a} {male=he;female=she} {TA} {9}"

    en = resolve_display_placeholders(text, lang="en", gender_preference="female")
    cn = resolve_display_placeholders(text, lang="cn", gender_preference="male")

    assert en == "Rover Rover Tap she {TA} <9>"
    assert cn == "漂泊者 漂泊者 Tap he 他 <9>"


def test_resolve_display_placeholders_prefers_skill_params_and_avoids_double_percent():
    text = "Boost {0}% for {1}"

    en = resolve_display_placeholders(
        text,
        lang="en",
        ocr_context="99% 9m",
        text_key="Skill_123_Text",
        param_resolver=FakeParamResolver(),
    )
    cn = resolve_display_placeholders(
        text,
        lang="cn",
        ocr_context="99% 9m",
        text_key="Skill_123_Text",
        param_resolver=FakeParamResolver(),
    )

    assert en == "Boost 12% for 1h 2m"
    assert cn == "Boost 12% for 1小时2分钟"


def test_convert_game_html_converts_game_markup_and_wraps_preferences():
    html = convert_game_html(
        "<color=red>Danger</color> <te href=1>term</te> <size=20>big</size> 【Key】\nNext",
        lang="en",
        preferences=prefs(),
    )

    assert 'font-family: "ENFont"' in html
    assert "font-size: 14pt" in html
    assert "line-height: 130%" in html
    assert "letter-spacing: 0.5px" in html
    assert '<span style="color: #ef4444">Danger</span>' in html
    assert 'text-decoration: underline;' in html
    assert '<span style="font-size: 20pt">big</span>' in html
    assert '<span style="color: #fbbf24; font-weight: bold;">【Key】</span><br>Next' in html


def test_convert_game_html_preserves_internal_safe_span_styles():
    html = convert_game_html(
        "<span style='color: #d4af37; font-weight: bold;'>思考</span>\n大致的情况我了解了。",
        lang="cn",
        preferences=prefs(),
    )

    assert "&lt;span" not in html
    assert "&lt;/span&gt;" not in html
    assert '<span style="color: #d4af37; font-weight: bold;">思考</span><br>大致的情况我了解了。' in html


def test_convert_game_html_escapes_unknown_markup_literals():
    html = convert_game_html("A < B & C <unknown>tag</unknown>", lang="en", preferences=prefs())

    assert "A &lt; B &amp; C &lt;unknown&gt;tag&lt;/unknown&gt;" in html
    assert "<unknown>" not in html


def test_shape_single_result_builds_display_model_and_audio_candidate():
    result = {
        "_query_key": "hello",
        "_score": 0.93,
        "_ocr_text": "Hello 88%",
        "_ocr_context": "88%",
        "_first_line": "QuestTitle",
        "matches": [
            {
                "text_key": "Skill_123_Text",
                "official_en": "Damage {0}%",
                "official_cn": "伤害 {0}% {TA}",
                "audio_hash": "123",
                "audio_event": "vo_event",
            }
        ],
    }

    model = shape_translation_display(
        result,
        preferences=prefs(gender_preference="female"),
        param_resolver=FakeParamResolver(),
        title_resolver=lambda title: "任务标题" if title == "QuestTitle" else None,
    )

    assert model.source.is_html is True
    assert "QuestTitle" in model.source.display_text
    assert "Damage 12%" in model.source.display_text
    assert "任务标题" in model.target.display_text
    assert "伤害 12% 她" in model.target.display_text
    assert model.audio_candidate is not None
    assert model.audio_candidate.text_key == "Skill_123_Text"
    assert model.audio_candidate.db_hash == "123"
    assert model.audio_controls_enabled is True
    assert any(line.startswith("[EN] Damage {0}%") for line in model.log_lines)


def test_shape_multi_result_joins_items_and_selects_audio_candidate():
    result = {
        "_multi": True,
        "_has_audio": True,
        "_query_key": "list",
        "_ocr_text": "A\nB",
        "_official_en": "A",
        "_official_cn": "甲",
        "items": [
            {"ocr": "A", "query_key": "a", "score": 0.8, "text_key": "NoAudio", "official_en": "A", "official_cn": "甲"},
            {"ocr": "B", "query_key": "b", "score": 0.91, "text_key": "Audio_Text", "official_en": "B", "official_cn": "乙"},
        ],
    }

    model = shape_translation_display(
        result,
        preferences=prefs(),
        voice_event_index=FakeVoiceEventIndex(),
    )

    assert model.is_multi is True
    assert model.source.display_text == "A\nB"
    assert model.target.display_text == "甲\n乙"
    assert model.audio_candidate is not None
    assert model.audio_candidate.text_key == "Audio_Text"
    assert model.audio_candidate.origin == "multi"
    assert model.audio_controls_enabled is True
    assert "[MATCH] 官方原文: A" in model.log_lines


def test_shape_multi_result_uses_missing_chinese_fallback():
    model = shape_translation_display(
        {"_multi": True, "items": [], "_query_key": "list", "_ocr_text": "list"},
        preferences=prefs(),
    )

    assert model.target.display_text == "（未找到中文匹配）"
    assert model.audio_candidate is None
