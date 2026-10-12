from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class PlaceholderValueResolver(Protocol):
    def resolve_values(self, text_key: str | None, placeholder_count: int = 0) -> list[str]: ...


@dataclass(frozen=True)
class DisplayPreferences:
    gender_preference: str = "female"
    font_en: str = "Source Han Serif SC, 思源宋体, serif"
    font_cn: str = "Source Han Serif SC, 思源宋体, serif"
    font_size: int = 13
    font_weight_css: str = "600"
    line_spacing: float = 1.2
    letter_spacing: float = 0.0
    target_language: str = "cn"


@dataclass(frozen=True)
class DisplayPane:
    raw_text: str
    display_text: str
    is_html: bool
    rendered_html: str | None = None
    log_raw: str = ""


@dataclass(frozen=True)
class DisplayAudioCandidate:
    text_key: str
    db_event: Any = None
    db_hash: Any = None
    origin: str = "single"


@dataclass(frozen=True)
class TranslationDisplayModel:
    source: DisplayPane
    target: DisplayPane
    query_key: str
    ocr_text: str
    score: Any = None
    audio_candidate: DisplayAudioCandidate | None = None
    audio_controls_enabled: bool = False
    log_lines: tuple[str, ...] = ()
    is_multi: bool = False


NAMED_COLORS = {
    "highlight": "#fbbf24",
    "highlightb": "#f59e0b",
    "title": "#a79969",
    "wind": "#55ffb5",
    "fire": "#ef4444",
    "thunder": "#8b5cf6",
    "ice": "#06b6d4",
    "light": "#fbbf24",
    "dark": "#8b5cf6",
    "blue": "#60a5fa",
    "blued": "#3b82f6",
    "green": "#34d399",
    "greend": "#10b981",
    "yellow": "#fbbf24",
    "yellowd": "#ffd12f",
    "red": "#ef4444",
    "redd": "#e2524c",
    "reda": "#f87171",
    "white": "#f8fafc",
    "rare2": "#60a5fa",
    "rare3": "#a78bfa",
    "rare4": "#f59e0b",
    "rare5": "#fbbf24",
    "threathigh": "#ef4444",
    "purpled": "#8b5cf6",
}


def extract_numeric_values_from_context(ocr_context: str) -> list[str]:
    if not isinstance(ocr_context, str) or not ocr_context:
        return []
    values: list[str] = []
    pattern = re.compile(
        r"\d+(?:\.\d+)?\s?(?:kb|mb|gb|tb)"
        r"|\d+\s*[dhms](?:\s+\d+\s*[dhms])*"
        r"|\d+(?:\.\d+)?%"
        r"|\d+(?:\.\d+)+"
        r"|\d+",
        flags=re.IGNORECASE,
    )
    time_pat = re.compile(r"^\d+\s*[dhms](?:\s+\d+\s*[dhms])*$", flags=re.IGNORECASE)
    for match in pattern.finditer(ocr_context):
        token = match.group(0).strip()
        if not token:
            continue
        if time_pat.fullmatch(token):
            token = re.sub(r"\s+", " ", token)
        else:
            token = re.sub(r"\s+", "", token)
        values.append(token)
    return values


def resolve_display_placeholders(
    text: str,
    *,
    lang: str = "en",
    ocr_context: str | None = None,
    text_key: str | None = None,
    gender_preference: str = "female",
    param_resolver: PlaceholderValueResolver | None = None,
) -> str:
    if not isinstance(text, str) or not text:
        return text

    out = text
    lang_norm = str(lang or "en").strip().lower()
    is_cn = lang_norm in {"cn", "zh", "zh-cn", "zh_hans", "zh-hans"}
    player_name = "漂泊者" if is_cn else "Rover"

    out = out.replace("{PlayerName}", player_name)
    out = re.sub(
        r"\{Cus:Var,\s*VarType=Global\s+Key=main_team_name\}",
        player_name,
        out,
        flags=re.IGNORECASE,
    )

    placeholder_indexes = [int(i) for i in re.findall(r"\{(\d+)\}", out)]
    placeholder_count = (max(placeholder_indexes) + 1) if placeholder_indexes else 0
    numeric_values = extract_numeric_values_from_context(ocr_context or "")

    if placeholder_count > 0 and text_key and param_resolver:
        db_values = param_resolver.resolve_values(text_key, placeholder_count)
        if db_values:
            if len(db_values) >= placeholder_count:
                numeric_values = list(db_values)
            elif not numeric_values:
                numeric_values = list(db_values)
            elif len(numeric_values) < placeholder_count:
                merged = list(db_values)
                for token in numeric_values:
                    if len(merged) >= placeholder_count:
                        break
                    merged.append(token)
                numeric_values = merged

    template_for_numeric = out
    time_pat = re.compile(r"^\d+\s*[dhms](?:\s+\d+\s*[dhms])*$", flags=re.IGNORECASE)

    def render_time_value(token: str) -> str:
        matches = re.findall(r"(\d+)\s*([dhms])", token, flags=re.IGNORECASE)
        if not matches:
            return token
        if is_cn:
            unit_map = {"d": "天", "h": "小时", "m": "分钟", "s": "秒"}
            return "".join(f"{n}{unit_map.get(u.lower(), u)}" for n, u in matches)
        return " ".join(f"{n}{u.lower()}" for n, u in matches)

    def replace_indexed_placeholder(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(numeric_values):
            value = numeric_values[idx]
            if time_pat.fullmatch(value):
                value = render_time_value(value)
            if value.endswith("%"):
                next_ch = template_for_numeric[match.end(): match.end() + 1]
                if next_ch == "%":
                    value = value[:-1]
            return value
        return f"<{idx}>"

    out = re.sub(r"\{(\d+)\}", replace_indexed_placeholder, out)

    def replace_input_token(match: re.Match[str]) -> str:
        body = match.group(1) or ""
        pc = re.search(r"\bPC=([^,\s;{}]+)", body, flags=re.IGNORECASE)
        touch = re.search(r"\bTouch=([^,\s;{}]+)", body, flags=re.IGNORECASE)
        gamepad = re.search(r"\bGamepad=([^,\s;{}]+)", body, flags=re.IGNORECASE)
        token = (pc.group(1) if pc else "") or (touch.group(1) if touch else "") or (gamepad.group(1) if gamepad else "") or "Press"
        token = token.strip()
        if token.islower():
            token = token.capitalize()
        return token or "Press"

    out = re.sub(r"\{Cus:Ipt,([^{}]+)\}", replace_input_token, out, flags=re.IGNORECASE)

    target = "male" if str(gender_preference or "female").strip().lower() == "male" else "female"

    def replace_gender_token(match: re.Match[str]) -> str:
        body = match.group(1) or ""
        body_norm = body.strip().lower()
        if body_norm == "ta":
            if is_cn:
                return "他" if target == "male" else "她"
            return match.group(0)
        parts = [part.strip() for part in body.split(";") if part.strip()]
        values: dict[str, str] = {}
        for part in parts:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip().lower()] = value.strip()
        if "male" in values and "female" in values:
            male_value = values.get("male", "")
            female_value = values.get("female", "")
            if male_value or female_value:
                return male_value if target == "male" else female_value
        return match.group(0)

    return re.sub(r"\{([^{}]{1,120})\}", replace_gender_token, out)


def _normalize_safe_span_style(style: str) -> str | None:
    declarations: dict[str, str] = {}
    for raw_decl in str(style or "").split(";"):
        if ":" not in raw_decl:
            continue
        key, value = raw_decl.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            declarations[key] = value

    color = declarations.get("color")
    font_weight = declarations.get("font-weight")
    if not color or not font_weight:
        return None
    color_match = re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", color)
    if not color_match:
        return None
    font_weight_norm = font_weight.lower()
    if font_weight_norm not in {"bold", "600", "700"}:
        return None
    return f"color: {color_match.group(0)}; font-weight: {font_weight_norm};"


def contains_game_markup(text: str) -> bool:
    return bool(text) and (("<" in text and ">" in text) or "【" in text)


def convert_game_html(text: str, *, lang: str = "cn", preferences: DisplayPreferences | None = None) -> str:
    preferences = preferences or DisplayPreferences()

    def resolve_color_token(token: str) -> str:
        value = str(token or "").strip()
        if not value:
            return "#fbbf24"
        match = re.fullmatch(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value)
        if match:
            return f"#{match.group(1)}"
        key = value.lower()
        if key in NAMED_COLORS:
            return NAMED_COLORS[key]
        if "yellow" in key:
            return "#fbbf24"
        if "red" in key:
            return "#ef4444"
        if "blue" in key:
            return "#60a5fa"
        if "green" in key:
            return "#34d399"
        if "purple" in key:
            return "#8b5cf6"
        if "white" in key:
            return "#f8fafc"
        return "#fbbf24"

    def replace_color_tag(match: re.Match[str]) -> str:
        return f'<span style="color: {resolve_color_token(match.group(1))}">{match.group(2)}</span>'

    def replace_safe_span_tag(match: re.Match[str]) -> str:
        style = _normalize_safe_span_style(match.group(2))
        if style is None:
            return match.group(0)
        return f'<span style="{style}">{match.group(3)}</span>'

    html_body = html.escape(text, quote=False)
    html_body = re.sub(
        r"&lt;span\s+style=(['\"])(.*?)\1&gt;(.*?)&lt;/span&gt;",
        replace_safe_span_tag,
        html_body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html_body = re.sub(r"&lt;color=([^&]+)&gt;(.*?)&lt;/color&gt;", replace_color_tag, html_body, flags=re.DOTALL | re.IGNORECASE)
    html_body = re.sub(
        r"&lt;te\s+href=\d+&gt;(.*?)&lt;/te&gt;",
        r'<span style="color: #fbbf24; text-decoration: underline;">\1</span>',
        html_body,
        flags=re.DOTALL,
    )
    html_body = re.sub(r"&lt;size=(\d+)&gt;(.*?)&lt;/size&gt;", r'<span style="font-size: \1pt">\2</span>', html_body, flags=re.DOTALL)
    html_body = re.sub(
        r"【(.*?)】",
        r'<span style="color: #fbbf24; font-weight: bold;">【\1】</span>',
        html_body,
        flags=re.DOTALL,
    )

    font_family = preferences.font_en if lang == "en" else preferences.font_cn
    font_size_pt = int(preferences.font_size) if preferences.font_size else 13
    line_height_percent = int((float(preferences.line_spacing) if preferences.line_spacing else 1.2) * 100)
    letter_spacing = float(preferences.letter_spacing) if preferences.letter_spacing else 0.0
    font_weight = preferences.font_weight_css

    return f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: "{font_family}";
            color: #e2e8f0;
            line-height: {line_height_percent}%;
            margin: 8px;
            padding: 0;
            font-size: {font_size_pt}pt;
            font-weight: {font_weight};
            letter-spacing: {letter_spacing}px;
        }}
    </style>
</head>
<body>
{html_body.replace(chr(10), '<br>')}
</body>
</html>
'''


def make_display_pane(text: str, *, lang: str, preferences: DisplayPreferences, log_raw: str = "") -> DisplayPane:
    is_html = contains_game_markup(text)
    rendered_html = convert_game_html(text, lang=lang, preferences=preferences) if is_html else None
    return DisplayPane(raw_text=text, display_text=text, is_html=is_html, rendered_html=rendered_html, log_raw=log_raw)


def shape_translation_display(
    result: dict[str, Any],
    *,
    preferences: DisplayPreferences,
    param_resolver: PlaceholderValueResolver | None = None,
    title_resolver: Callable[[str], str | None] | None = None,
    voice_map: dict[str, Any] | None = None,
    voice_event_index: Any = None,
) -> TranslationDisplayModel:
    if result.get("_multi"):
        return _shape_multi_result(
            result,
            preferences=preferences,
            param_resolver=param_resolver,
            voice_map=voice_map,
            voice_event_index=voice_event_index,
        )
    return _shape_single_result(
        result,
        preferences=preferences,
        param_resolver=param_resolver,
        title_resolver=title_resolver,
    )


def _shape_multi_result(
    result: dict[str, Any],
    *,
    preferences: DisplayPreferences,
    param_resolver: PlaceholderValueResolver | None,
    voice_map: dict[str, Any] | None,
    voice_event_index: Any,
) -> TranslationDisplayModel:
    items = result.get("items", [])
    ocr_context = (result.get("_ocr_context") or result.get("_ocr_text")) if isinstance(result, dict) else None
    left: list[str] = []
    right: list[str] = []
    left_raw: list[str] = []
    right_raw: list[str] = []
    log_lines: list[str] = []

    for item in items:
        text_key = item.get("text_key")
        en_raw = item.get("official_en") or item.get("ocr") or ""
        cn_raw = item.get("official_cn") or item.get("text_key") or ""
        if en_raw:
            left_raw.append(en_raw)
            left.append(resolve_display_placeholders(
                en_raw,
                lang="en",
                ocr_context=ocr_context,
                text_key=text_key,
                gender_preference=preferences.gender_preference,
                param_resolver=param_resolver,
            ))
        if cn_raw:
            right_raw.append(cn_raw)
            right.append(resolve_display_placeholders(
                cn_raw,
                lang=preferences.target_language,
                ocr_context=ocr_context,
                text_key=text_key,
                gender_preference=preferences.gender_preference,
                param_resolver=param_resolver,
            ))
        log_lines.append(f"[ITEM] {item.get('ocr')} -> {item.get('text_key')} (score={item.get('score')})")

    en_joined_raw = "\n".join(left_raw)
    cn_joined_raw = "\n".join(right_raw) if right_raw else "（未找到中文匹配）"
    en_joined = "\n".join(left)
    cn_joined = "\n".join(right) if right else "（未找到中文匹配）"

    official_en = result.get("_official_en") or result.get("_ocr_text") or ""
    official_cn = result.get("_official_cn") or ""
    log_lines.extend([
        f"[MATCH] 官方原文: {official_en}",
        f"[MATCH] 官方译文: {official_cn}",
        f"[EN] {en_joined_raw}",
        f"[CN] {cn_joined_raw}",
        f"[QUERY] OCR识别: {result.get('_ocr_text')} -> {result.get('_query_key')}",
    ])

    audio_candidate = _select_multi_audio_candidate(result, voice_map=voice_map, voice_event_index=voice_event_index)
    return TranslationDisplayModel(
        source=make_display_pane(en_joined, lang="en", preferences=preferences, log_raw=en_joined_raw),
        target=make_display_pane(cn_joined, lang=preferences.target_language, preferences=preferences, log_raw=cn_joined_raw),
        query_key=result.get("_query_key", ""),
        ocr_text=result.get("_ocr_text", ""),
        audio_candidate=audio_candidate,
        audio_controls_enabled=audio_candidate is not None,
        log_lines=tuple(log_lines),
        is_multi=True,
    )


def _select_multi_audio_candidate(
    result: dict[str, Any],
    *,
    voice_map: dict[str, Any] | None,
    voice_event_index: Any,
) -> DisplayAudioCandidate | None:
    if not result.get("_has_audio", False):
        return None
    for item in result.get("items", []):
        if item.get("score", 0) < 0.85 or not item.get("text_key"):
            continue
        text_key = item["text_key"]
        event_name = f"vo_{text_key}"
        if voice_map and event_name in voice_map:
            return DisplayAudioCandidate(text_key=text_key, origin="multi")
        if voice_event_index:
            events = voice_event_index.find_candidates(text_key=text_key, voice_event=event_name, limit=1)
            if events:
                return DisplayAudioCandidate(text_key=text_key, origin="multi")
    return None


def _shape_single_result(
    result: dict[str, Any],
    *,
    preferences: DisplayPreferences,
    param_resolver: PlaceholderValueResolver | None,
    title_resolver: Callable[[str], str | None] | None,
) -> TranslationDisplayModel:
    query_key = result.get("_query_key", "")
    score = result.get("_score")
    matches = result.get("matches") or []
    en_text = ""
    cn_text = ""
    en_log_raw = ""
    cn_log_raw = ""
    text_key = None
    audio_hash = None
    audio_event = None
    first_line = result.get("_first_line")
    speaker_name = result.get("_speaker_name", "")
    log_lines: list[str] = []

    if matches:
        first = matches[0]
        en_text = first.get("official_en", "")
        cn_text = first.get("official_cn", "")
        en_log_raw = en_text or ""
        cn_log_raw = cn_text or ""
        text_key = first.get("text_key")
        audio_hash = first.get("audio_hash")
        audio_event = first.get("audio_event")

        if speaker_name:
            speaker_cn = title_resolver(speaker_name) if title_resolver else None
            display_speaker_cn = speaker_cn or speaker_name
            en_text = f"<span style='color: #d4af37; font-weight: bold;'>{speaker_name}:</span>\n{en_text}"
            cn_text = f"<span style='color: #d4af37; font-weight: bold;'>{display_speaker_cn}:</span>\n{cn_text}"
            log_lines.append(f"[DISPLAY] 说话者前缀: {speaker_name} -> {display_speaker_cn}, 内容: {text_key}")
        elif first_line:
            title_cn = title_resolver(first_line) if title_resolver else None
            display_title = title_cn or first_line
            en_text = f"<span style='color: #d4af37; font-weight: bold;'>{first_line}</span>\n{en_text}"
            cn_text = f"<span style='color: #d4af37; font-weight: bold;'>{display_title}</span>\n{cn_text}"
            log_lines.append(f"[DISPLAY] 标题: {first_line} -> {display_title}, 内容: {text_key}")

    ocr_context = (result.get("_ocr_context") or result.get("_ocr_text")) if isinstance(result, dict) else None
    en_text = resolve_display_placeholders(
        en_text,
        lang="en",
        ocr_context=ocr_context,
        text_key=text_key,
        gender_preference=preferences.gender_preference,
        param_resolver=param_resolver,
    )
    cn_text = resolve_display_placeholders(
        cn_text,
        lang=preferences.target_language,
        ocr_context=ocr_context,
        text_key=text_key,
        gender_preference=preferences.gender_preference,
        param_resolver=param_resolver,
    )

    if not en_text:
        en_text = result.get("_ocr_text", query_key)
        if not en_log_raw:
            en_log_raw = en_text
    if not cn_text:
        cn_text = "（未找到中文匹配）"
        if not cn_log_raw:
            cn_log_raw = cn_text

    log_lines.extend([f"[EN] {en_log_raw}", f"[CN] {cn_log_raw}", f"[QUERY] {result.get('_ocr_text')} -> {query_key}"])
    audio_candidate = None
    if text_key:
        audio_candidate = DisplayAudioCandidate(text_key=text_key, db_event=audio_event, db_hash=audio_hash, origin="single")

    return TranslationDisplayModel(
        source=make_display_pane(en_text, lang="en", preferences=preferences, log_raw=en_log_raw),
        target=make_display_pane(cn_text, lang=preferences.target_language, preferences=preferences, log_raw=cn_log_raw),
        query_key=query_key,
        ocr_text=result.get("_ocr_text", ""),
        score=score,
        audio_candidate=audio_candidate,
        audio_controls_enabled=audio_candidate is not None,
        log_lines=tuple(log_lines),
        is_multi=False,
    )
