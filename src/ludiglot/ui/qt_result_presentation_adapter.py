from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QTextEdit

from ludiglot.core.display_shaper import DisplayPreferences, convert_game_html
from ludiglot.core.preferences import FONT_SIZE_MAX, FONT_SIZE_MIN
from ludiglot.ui.result_presentation_controller import CurrentDisplayState


class QtResultPresentationAdapter:
    def __init__(
        self,
        *,
        source_editor: QTextEdit,
        target_editor: QTextEdit,
        show_single_result: Callable[[], None],
        show_multi_result: Callable[[], None],
    ) -> None:
        self._source_editor = source_editor
        self._target_editor = target_editor
        self._show_single_result = show_single_result
        self._show_multi_result = show_multi_result

    def apply_display_state(self, state: CurrentDisplayState, preferences: DisplayPreferences) -> None:
        self._render_current_state(state, preferences, render_empty=False)
        self._apply_current_styles(state, preferences)

    def refresh_font_settings(self, state: CurrentDisplayState, preferences: DisplayPreferences) -> None:
        self.apply_display_state(state, preferences)

    def activate_for_result(self, *, is_multi: bool) -> None:
        if is_multi:
            self._show_multi_result()
        else:
            self._show_single_result()

    def _render_current_state(
        self,
        state: CurrentDisplayState,
        preferences: DisplayPreferences,
        *,
        render_empty: bool,
    ) -> None:
        self._render_editor(
            self._source_editor,
            text=state.source_text,
            is_html=state.source_is_html,
            lang="en",
            preferences=preferences,
            render_empty=render_empty,
        )
        self._render_editor(
            self._target_editor,
            text=state.target_text,
            is_html=state.target_is_html,
            lang="cn",
            preferences=preferences,
            render_empty=render_empty,
        )

    def _render_editor(
        self,
        editor: QTextEdit,
        *,
        text: str | None,
        is_html: bool,
        lang: str,
        preferences: DisplayPreferences,
        render_empty: bool,
    ) -> None:
        if text is None:
            if render_empty:
                editor.setPlainText("")
            return
        if is_html:
            editor.setHtml(convert_game_html(text, lang=lang, preferences=preferences))
        else:
            editor.setPlainText(text)

    def _apply_current_styles(self, state: CurrentDisplayState, preferences: DisplayPreferences) -> None:
        en_font, cn_font = self._build_content_fonts(preferences)
        self._source_editor.setFont(en_font)
        self._target_editor.setFont(cn_font)
        self._apply_text_document_style(
            self._source_editor,
            en_font,
            line_spacing=preferences.line_spacing,
            force_char_style=not state.source_is_html,
        )
        self._apply_text_document_style(
            self._target_editor,
            cn_font,
            line_spacing=preferences.line_spacing,
            force_char_style=not state.target_is_html,
        )

    def _build_content_fonts(self, preferences: DisplayPreferences) -> tuple[QFont, QFont]:
        try:
            size_val = int(preferences.font_size) if preferences.font_size else 13
        except (ValueError, TypeError):
            size_val = 13
        valid_size = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, size_val))

        weight_map = {
            "300": QFont.Weight.Light,
            "400": QFont.Weight.Normal,
            "600": QFont.Weight.DemiBold,
            "700": QFont.Weight.Bold,
            "900": QFont.Weight.Black,
        }
        weight_val = weight_map.get(str(preferences.font_weight_css), QFont.Weight.DemiBold)

        en_font = QFont()
        en_font.setFamily(preferences.font_en)
        en_font.setPointSize(valid_size)
        en_font.setWeight(weight_val)
        en_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, preferences.letter_spacing)

        cn_font = QFont()
        cn_font.setFamily(preferences.font_cn)
        cn_font.setPointSize(valid_size)
        cn_font.setWeight(weight_val)
        cn_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, preferences.letter_spacing)
        return en_font, cn_font

    def _apply_text_document_style(
        self,
        editor: QTextEdit,
        font: QFont,
        *,
        line_spacing: float,
        force_char_style: bool,
    ) -> None:
        doc = editor.document()
        doc.setDefaultFont(font)

        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)

        if force_char_style:
            char_fmt = QTextCharFormat()
            char_fmt.setFont(font)
            cursor.mergeCharFormat(char_fmt)

        try:
            line_height_ratio = float(line_spacing)
        except (TypeError, ValueError):
            line_height_ratio = 1.2
        line_height_percent = int(max(50, min(400, line_height_ratio * 100)))
        block_fmt = QTextBlockFormat()
        try:
            height_type = int(QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        except Exception:
            height_type = int(getattr(QTextBlockFormat, "ProportionalHeight", 1))
        block_fmt.setLineHeight(float(line_height_percent), height_type)
        cursor.mergeBlockFormat(block_fmt)
