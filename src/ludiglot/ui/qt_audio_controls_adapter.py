from __future__ import annotations

from typing import Any, Callable

from ludiglot.ui.audio_controls_presenter import AudioControlsViewState


class QtAudioControlsAdapter:
    def __init__(
        self,
        *,
        play_pause_button: Any,
        slider: Any,
        time_label: Any,
        timer: Any,
        status: Callable[[str], None],
    ) -> None:
        self._play_pause_button = play_pause_button
        self._slider = slider
        self._time_label = time_label
        self._timer = timer
        self._status = status

    def apply(self, state: AudioControlsViewState) -> None:
        self._play_pause_button.setEnabled(state.enabled)
        self._play_pause_button.set_playing(state.playing)
        self._slider.setEnabled(state.enabled)
        if state.update_progress:
            self._slider.set_progress(state.progress, state.duration_ms)
        self._time_label.setText(state.time_text)
        if state.timer_running:
            self._timer.start()
        else:
            self._timer.stop()
        if state.status_message:
            self._status(state.status_message)

    def is_dragging(self) -> bool:
        return bool(self._slider.is_dragging())
