from types import SimpleNamespace

from ludiglot.core.display_shaper import DisplayPreferences
from ludiglot.ui.result_presentation_controller import CurrentDisplayState, ResultPresentationController


class FakeAudio:
    def __init__(self, *, load_result=True, has_current_audio=True):
        self.events = []
        self.load_result = load_result
        self._has_current_audio = has_current_audio

    @property
    def has_current_audio(self):
        return self._has_current_audio

    def stop(self, emit_status=True):
        self.events.append(("stop", emit_status))

    def clear_candidate(self):
        self.events.append(("clear",))

    def load_result_candidate(self, candidate, *, is_multi):
        self.events.append(("load", candidate, is_multi))
        return self.load_result

    def play_current(self):
        self.events.append(("play",))


class FakeView:
    def __init__(self, *, fail_apply=False):
        self.events = []
        self.fail_apply = fail_apply

    def apply_display_state(self, state, preferences):
        self.events.append(("apply", state, preferences))
        if self.fail_apply:
            raise RuntimeError("view failed")

    def refresh_font_settings(self, state, preferences):
        self.events.append(("refresh", state, preferences))

    def activate_for_result(self, *, is_multi):
        self.events.append(("activate", is_multi))


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        self.value += 0.01
        return self.value


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


def make_single_result():
    return {
        "_query_key": "hello",
        "_score": 0.93,
        "_ocr_text": "Hello",
        "matches": [
            {
                "text_key": "Audio_Text",
                "official_en": "<color=red>Hello</color>",
                "official_cn": "你好",
                "audio_hash": "123",
                "audio_event": "vo_event",
            }
        ],
    }


def make_multi_result():
    return {
        "_multi": True,
        "_query_key": "list",
        "_ocr_text": "A\nB",
        "items": [
            {"ocr": "A", "query_key": "a", "score": 0.8, "text_key": "A_Text", "official_en": "A", "official_cn": "甲"},
            {"ocr": "B", "query_key": "b", "score": 0.91, "text_key": "B_Text", "official_en": "B", "official_cn": "乙"},
        ],
    }


def make_controller(*, config=None, audio=None, view=None, preferences=None):
    logs = []
    errors = []
    config = config or SimpleNamespace(play_audio=True)
    audio = audio or FakeAudio()
    view = view or FakeView()
    preferences = preferences or prefs()
    controller = ResultPresentationController(
        config_provider=lambda: config,
        preferences_provider=lambda: preferences,
        param_resolver_provider=lambda: None,
        title_resolver=lambda title: None,
        voice_map_provider=lambda: {},
        voice_event_index_provider=lambda: None,
        audio=audio,
        view=view,
        log=logs.append,
        error=errors.append,
        clock=FakeClock(),
    )
    return controller, audio, view, logs, errors


def test_present_single_result_renders_loads_audio_and_autoplays():
    controller, audio, view, logs, errors = make_controller()

    controller.present_result(make_single_result())

    assert errors == []
    assert audio.events[0] == ("stop", False)
    assert audio.events[1] == ("clear",)
    load_event = audio.events[2]
    assert load_event[0] == "load"
    assert load_event[1].text_key == "Audio_Text"
    assert load_event[2] is False
    assert audio.events[3] == ("play",)
    assert view.events[0][0] == "apply"
    state = view.events[0][1]
    assert state.source_text == "<color=red>Hello</color>"
    assert state.target_text == "你好"
    assert state.source_is_html is True
    assert view.events[1] == ("activate", False)
    assert any(line.startswith("[EN]") for line in logs)
    assert controller.current_display_state == state


def test_present_multi_result_activates_multi_and_passes_multi_flag():
    controller, audio, view, _, _ = make_controller()

    controller.present_result(make_multi_result())

    load_event = next(event for event in audio.events if event[0] == "load")
    assert load_event[2] is True
    assert ("activate", True) in view.events


def test_present_without_audio_or_autoplay_policy_does_not_play():
    audio = FakeAudio(load_result=False, has_current_audio=True)
    controller, audio, _, _, _ = make_controller(audio=audio)

    controller.present_result(make_single_result())

    assert ("play",) not in audio.events

    audio = FakeAudio(load_result=True, has_current_audio=True)
    controller, audio, _, _, _ = make_controller(config=SimpleNamespace(play_audio=False), audio=audio)

    controller.present_result(make_single_result())

    assert ("play",) not in audio.events

    audio = FakeAudio(load_result=True, has_current_audio=False)
    controller, audio, _, _, _ = make_controller(audio=audio)

    controller.present_result(make_single_result())

    assert ("play",) not in audio.events


def test_refresh_font_settings_reuses_current_display_state_with_fresh_preferences():
    controller, _, view, _, _ = make_controller(preferences=prefs(font_size=14))
    controller.present_result(make_single_result())
    original_state = controller.current_display_state

    new_preferences = prefs(font_size=20, font_en="NewEN")
    controller._preferences_provider = lambda: new_preferences
    controller.refresh_font_settings()

    assert view.events[-1] == ("refresh", original_state, new_preferences)


def test_refresh_before_result_uses_empty_state():
    controller, _, view, _, _ = make_controller()

    controller.refresh_font_settings()

    assert view.events == [("refresh", CurrentDisplayState(), prefs())]


def test_present_result_reports_errors_from_view():
    controller, audio, view, logs, errors = make_controller(view=FakeView(fail_apply=True))

    controller.present_result(make_single_result())

    assert errors == ["显示结果失败: view failed"]
    assert any("RuntimeError: view failed" in line for line in logs)
    assert ("play",) not in audio.events
