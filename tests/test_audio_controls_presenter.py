from ludiglot.ui.audio_controls_presenter import AudioControlsPresenter


def test_disabled_state_resets_controls():
    state = AudioControlsPresenter().disabled("missing")

    assert state.enabled is False
    assert state.playing is False
    assert state.progress == 0.0
    assert state.duration_ms == 0
    assert state.time_text == "00:00 / 00:00"
    assert state.timer_running is False
    assert state.status_message == "missing"


def test_ready_state_enables_controls_without_playing():
    state = AudioControlsPresenter().ready()

    assert state.enabled is True
    assert state.playing is False
    assert state.progress == 0.0
    assert state.time_text == "00:00 / 00:00"
    assert state.timer_running is False


def test_playing_state_starts_timer_and_reports_source():
    state = AudioControlsPresenter().playing("voice.wav", progress=0.25, duration_ms=120_000)

    assert state.enabled is True
    assert state.playing is True
    assert state.progress == 0.25
    assert state.time_text == "00:30 / 02:00"
    assert state.timer_running is True
    assert state.status_message == "正在播放: voice.wav"


def test_playing_state_without_source_uses_generic_status():
    state = AudioControlsPresenter().playing(None)

    assert state.status_message == "正在播放"


def test_paused_state_stops_timer_and_reports_pause():
    state = AudioControlsPresenter().paused(0.5, 90_000)

    assert state.enabled is True
    assert state.playing is False
    assert state.progress == 0.5
    assert state.time_text == "00:45 / 01:30"
    assert state.timer_running is False
    assert state.status_message == "已暂停"


def test_seeked_state_formats_immediate_preview():
    state = AudioControlsPresenter().seeked(0.75, 80_000)

    assert state.enabled is True
    assert state.progress == 0.75
    assert state.time_text == "01:00 / 01:20"
    assert state.timer_running is False


def test_progress_state_formats_current_and_total_time():
    state = AudioControlsPresenter().progress(0.125, 240_000)

    assert state.progress == 0.125
    assert state.time_text == "00:30 / 04:00"
    assert state.timer_running is True


def test_ended_state_uses_final_progress_and_status():
    state = AudioControlsPresenter().ended(65_000)

    assert state.enabled is True
    assert state.playing is False
    assert state.progress == 1.0
    assert state.time_text == "01:05 / 01:05"
    assert state.timer_running is False
    assert state.status_message == "播放已结束"


def test_duration_formatting_handles_zero_and_clamps_progress():
    presenter = AudioControlsPresenter()

    assert presenter.progress(2.0, 0).time_text == "00:00 / 00:00"
    assert presenter.progress(-1.0, 10_000).time_text == "00:00 / 00:10"
    assert presenter.progress(2.0, 10_000).time_text == "00:10 / 00:10"
