from __future__ import annotations

from ludiglot.core.matcher import TextMatcher
from ludiglot.core.text_builder import build_text_db_from_maps, normalize_en


def _db_entry(text_key: str, official_en: str, official_cn: str = "中文", **extra):
    return {
        "key": normalize_en(official_en),
        "matches": [
            {
                "text_key": text_key,
                "official_en": official_en,
                "official_cn": official_cn,
                **extra,
            }
        ],
    }


def test_text_matcher_exact_match_returns_match_context() -> None:
    db = build_text_db_from_maps(
        {"MAIN_EXACT_001": "Stand still and listen."},
        {"MAIN_EXACT_001": "站住，听我说。"},
        "test.json",
    )

    result = TextMatcher(db).match([("Stand still and listen.", 0.98)])

    assert result is not None
    assert (result.get("matches") or [{}])[0].get("text_key") == "MAIN_EXACT_001"
    assert result.get("_matched_key") == "standstillandlisten"
    assert result.get("_query_key") == "standstillandlisten"
    assert result.get("_ocr_text") == "Stand still and listen"


def test_text_matcher_prefers_body_when_title_is_followed_by_long_text() -> None:
    body = "When the resonance field expands, nearby allies recover energy and gain a shield."
    db = build_text_db_from_maps(
        {
            "TITLE_TEST": "Resonance Field",
            "BODY_TEST": body,
        },
        {
            "TITLE_TEST": "共鸣场",
            "BODY_TEST": "共鸣场展开时，附近队友回复能量并获得护盾。",
        },
        "test.json",
    )

    result = TextMatcher(db).match(
        [
            ("Resonance Field", 0.99),
            ("When the resonance field expands nearby allies", 0.96),
            ("recover energy and gain a shield", 0.95),
        ]
    )

    assert result is not None
    assert (result.get("matches") or [{}])[0].get("text_key") == "BODY_TEST"
    assert result.get("_first_line") == "Resonance Field"


def test_text_matcher_returns_multi_result_for_list_mode() -> None:
    db = build_text_db_from_maps(
        {
            "ITEM_ALPHA": "Alpha Core",
            "ITEM_BETA": "Beta Shell",
            "ITEM_GAMMA": "Gamma Lens",
        },
        {
            "ITEM_ALPHA": "阿尔法核心",
            "ITEM_BETA": "贝塔外壳",
            "ITEM_GAMMA": "伽马透镜",
        },
        "test.json",
    )

    result = TextMatcher(db).match(
        [
            ("Alpha Core", 0.99),
            ("Beta Shell", 0.99),
            ("Gamma Lens", 0.99),
            ("12", 0.99),
        ]
    )

    assert result is not None
    assert result.get("_multi") is True
    assert {item.get("text_key") for item in result.get("items", [])} == {
        "ITEM_ALPHA",
        "ITEM_BETA",
        "ITEM_GAMMA",
    }


def test_text_matcher_keeps_audio_metadata_on_match() -> None:
    key = normalize_en("That is the spirit.")
    db = {
        key: _db_entry(
            "MAIN_AUDIO_001",
            "That is the spirit.",
            "就是这种气势。",
            audio_event="vo_MAIN_AUDIO_001",
            audio_hash=12345,
        )
    }

    result = TextMatcher(db).match([("That is the spirit.", 0.99)])

    assert result is not None
    match = (result.get("matches") or [{}])[0]
    assert match.get("text_key") == "MAIN_AUDIO_001"
    assert match.get("audio_event") == "vo_MAIN_AUDIO_001"
    assert match.get("audio_hash") == 12345


def test_text_matcher_prioritizes_preferred_rover_gender() -> None:
    key = normalize_en("Welcome back Rover.")
    db = {
        key: {
            "key": key,
            "matches": [
                {
                    "text_key": "MAIN_ROVER_M",
                    "official_en": "Welcome back Rover.",
                    "official_cn": "欢迎回来。",
                    "audio_event": "play_vo_roverm_welcome",
                },
                {
                    "text_key": "MAIN_ROVER_F",
                    "official_en": "Welcome back Rover.",
                    "official_cn": "欢迎回来。",
                    "audio_event": "play_vo_roverf_welcome",
                },
            ],
        }
    }

    result = TextMatcher(db, gender_preference="female").match([("Welcome back Rover.", 0.99)])

    assert result is not None
    assert (result.get("matches") or [{}])[0].get("text_key") == "MAIN_ROVER_F"
