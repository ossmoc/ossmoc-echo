from pathlib import Path
from types import SimpleNamespace

from ludiglot.core.audio_playback_orchestrator import AudioPlaybackDecision, AudioPlaybackIdentity
from ludiglot.core.display_shaper import DisplayAudioCandidate
from ludiglot.ui.audio_controls_presenter import AudioControlsPresenter
from ludiglot.ui.audio_playback_ui_controller import AudioPlaybackUiController


class FakeRuntime:
    def __init__(self):
        self.identity = None
        self.decision = None
        self.audio_index = "audio-index"
        self.resolve_calls = []
        self.prepare_calls = []

    def resolve_intent(self, intent):
        self.resolve_calls.append(intent)
        return self.identity

    def prepare_playback(self, identity):
        self.prepare_calls.append(identity)
        return self.decision


class FakePlayer:
    def __init__(self):
        self.playing = False
        self.position = 0.0
        self.duration = 0
        self.ended = False
        self.calls = []

    def play(self, path: str, block: bool = False):
        self.calls.append(("play", path, block))
        self.playing = True

    def stop(self):
        self.calls.append(("stop",))
        self.playing = False

    def pause(self):
        self.calls.append(("pause",))
        self.playing = False

    def resume(self):
        self.calls.append(("resume",))
        self.playing = True

    def seek(self, position: float):
        self.calls.append(("seek", position))
        self.position = position

    def is_playing(self):
        return self.playing

    def get_position(self):
        return self.position

    def get_duration(self):
        return self.duration

    def has_reached_end(self):
        return self.ended


class FakeControls:
    def __init__(self):
        self.states = []
        self.dragging = False

    def apply(self, state):
        self.states.append(state)

    def is_dragging(self):
        return self.dragging


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def make_controller(config=None, runtime=None, player=None, controls=None, clock=None):
    events = []
    config = config or SimpleNamespace(audio_cache_path=Path("cache"), play_audio=True)
    runtime = runtime or FakeRuntime()
    player = player or FakePlayer()
    controls = controls or FakeControls()
    clock = clock or FakeClock()
    controller = AudioPlaybackUiController(
        config_provider=lambda: config,
        runtime_provider=lambda: runtime,
        player=player,
        controls=controls,
        presenter=AudioControlsPresenter(),
        status=lambda value: events.append(("status", value)),
        log=lambda value: events.append(("log", value)),
        error=lambda value: events.append(("error", value)),
        audio_index_updated=lambda value: events.append(("audio_index", value)),
        clock=clock,
    )
    return controller, runtime, player, controls, clock, events


def identity(source_type="cache"):
    return AudioPlaybackIdentity(
        text_key="Text_Key",
        hash_value=123,
        event_name="Event_A",
        source_type=source_type,
    )


def test_stop_with_status_stops_player_and_disables_controls():
    controller, _, player, controls, _, events = make_controller()

    controller.stop(emit_status=True)

    assert player.calls == [("stop",)]
    assert controls.states[-1].enabled is False
    assert ("status", "已停止播放") in events


def test_stop_without_status_stops_player_and_resets_controls():
    controller, _, player, controls, _, events = make_controller()

    controller.stop(emit_status=False)

    assert player.calls == [("stop",)]
    assert controls.states[-1].enabled is False
    assert ("status", "已停止播放") not in events


def test_load_result_candidate_none_clears_identity_and_disables_controls():
    controller, _, _, controls, _, _ = make_controller()
    controller._current_identity = identity()

    has_audio = controller.load_result_candidate(None, is_multi=False)

    assert has_audio is False
    assert controller.has_current_audio is False
    assert controls.states[-1].enabled is False


def test_single_candidate_resolve_success_stores_identity_and_logs():
    runtime = FakeRuntime()
    runtime.identity = identity("db_fallback")
    controller, runtime, _, controls, _, events = make_controller(runtime=runtime)
    candidate = DisplayAudioCandidate(text_key="Text_Key", db_event="Event_A", db_hash=123, origin="single")

    has_audio = controller.load_result_candidate(candidate, is_multi=False)

    assert has_audio is True
    assert controller.current_identity == runtime.identity
    assert runtime.resolve_calls[-1].text_key == "Text_Key"
    assert runtime.resolve_calls[-1].db_hash == 123
    assert controls.states[-1].enabled is True
    assert ("log", "[MATCH] text_key=Text_Key 使用数据库哈希=123") in events


def test_single_candidate_resolve_miss_logs_missing_audio():
    runtime = FakeRuntime()
    controller, _, _, controls, _, events = make_controller(runtime=runtime)
    candidate = DisplayAudioCandidate(text_key="Text_Key", origin="single")

    has_audio = controller.load_result_candidate(candidate, is_multi=False)

    assert has_audio is False
    assert controller.has_current_audio is False
    assert controls.states[-1].enabled is False
    assert ("log", "[MATCH] text_key=Text_Key 未找到对应音频") in events


def test_multi_candidate_uses_multi_audio_intent():
    runtime = FakeRuntime()
    runtime.identity = identity("cache")
    controller, runtime, _, controls, _, events = make_controller(runtime=runtime)
    candidate = DisplayAudioCandidate(text_key="Text_Key", origin="multi")

    has_audio = controller.load_result_candidate(candidate, is_multi=True)

    assert has_audio is True
    assert runtime.resolve_calls[-1].origin == "multi"
    assert controls.states[-1].enabled is True
    assert ("log", "[WINDOW] 多条目模式：检测到高置信度音频，启用音频控件") in events
    assert ("log", "[AUDIO] text_key=Text_Key hash=123 (cache)") in events


def test_play_current_success_prepares_and_plays_path(tmp_path):
    runtime = FakeRuntime()
    runtime.identity = identity("cache")
    audio_path = tmp_path / "voice.wav"
    runtime.decision = AudioPlaybackDecision(enabled=True, path=audio_path, identity=identity("wem"))
    controller, runtime, player, controls, _, events = make_controller(runtime=runtime)
    controller.load_result_candidate(DisplayAudioCandidate(text_key="Text_Key", origin="single"), is_multi=False)

    controller.play_current()

    assert runtime.prepare_calls == [identity("cache")]
    assert player.calls == [("play", str(audio_path), False)]
    assert controls.states[-1].playing is True
    assert controls.states[-1].timer_running is True
    assert controller.current_identity == identity("wem")
    assert ("audio_index", "audio-index") in events
    assert ("status", "正在播放: voice.wav") in events


def test_play_current_missing_path_resets_controls_and_reports_status():
    runtime = FakeRuntime()
    runtime.identity = identity()
    runtime.decision = AudioPlaybackDecision(enabled=False, path=None, identity=identity(), status_message="missing")
    controller, _, player, controls, _, events = make_controller(runtime=runtime)
    controller.load_result_candidate(DisplayAudioCandidate(text_key="Text_Key", origin="single"), is_multi=False)

    controller.play_current()

    assert ("stop",) in player.calls
    assert controls.states[-1].enabled is False
    assert ("status", "missing") in events


def test_toggle_while_playing_pauses_and_reports_status():
    controller, _, player, controls, _, _ = make_controller()
    player.playing = True
    player.position = 0.5
    player.duration = 10_000

    controller.toggle()

    assert ("pause",) in player.calls
    assert controls.states[-1].playing is False
    assert controls.states[-1].status_message == "已暂停"


def test_toggle_when_paused_near_end_seeks_to_start_before_resume():
    controller, _, player, controls, _, _ = make_controller()
    player.playing = False
    player.position = 0.995
    player.duration = 20_000

    controller.toggle()

    assert player.calls[:2] == [("seek", 0.0), ("resume",)]
    assert controls.states[-1].playing is True


def test_seek_finished_updates_preview_and_records_debounce_time():
    controller, _, player, controls, clock, _ = make_controller()
    player.duration = 80_000
    clock.value = 50.0

    controller.seek_finished(0.75)

    assert ("seek", 0.75) in player.calls
    assert controls.states[-1].progress == 0.75
    assert controls.states[-1].time_text == "01:00 / 01:20"


def test_seek_finished_while_playing_keeps_playing_controls():
    controller, _, player, controls, clock, _ = make_controller()
    player.playing = True
    player.duration = 80_000
    clock.value = 50.0

    controller.seek_finished(0.75)

    assert ("seek", 0.75) in player.calls
    assert controls.states[-1].progress == 0.75
    assert controls.states[-1].playing is True
    assert controls.states[-1].timer_running is True


def test_update_progress_within_seek_debounce_does_not_overwrite_progress():
    controller, _, player, controls, clock, _ = make_controller()
    player.duration = 80_000
    clock.value = 50.0
    controller.seek_finished(0.75)
    before = len(controls.states)
    player.position = 0.1
    clock.value = 50.1

    controller.update_progress()

    assert len(controls.states) == before


def test_update_progress_natural_end_stops_and_reports_finished():
    controller, _, player, controls, _, _ = make_controller()
    player.playing = True
    player.ended = True
    player.duration = 65_000

    controller.update_progress()

    assert ("stop",) in player.calls
    assert controls.states[-1].progress == 1.0
    assert controls.states[-1].status_message == "播放已结束"


def test_update_progress_while_dragging_does_not_overwrite_slider_progress():
    controller, _, player, controls, _, _ = make_controller()
    player.playing = True
    player.position = 0.25
    player.duration = 100_000
    controls.dragging = True

    controller.update_progress()

    assert controls.states[-1].time_text == "00:25 / 01:40"
    assert controls.states[-1].update_progress is False
