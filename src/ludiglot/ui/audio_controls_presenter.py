from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AudioControlsViewState:
    enabled: bool
    playing: bool
    progress: float
    duration_ms: int
    time_text: str
    timer_running: bool
    status_message: str | None = None
    update_progress: bool = True


class AudioControlsPresenter:
    def disabled(self, status_message: str | None = None) -> AudioControlsViewState:
        return self._state(status_message=status_message)

    def ready(self) -> AudioControlsViewState:
        return self._state(enabled=True)

    def playing(
        self,
        source_name: str | None,
        progress: float = 0.0,
        duration_ms: int = 0,
    ) -> AudioControlsViewState:
        status = f"正在播放: {source_name}" if source_name else "正在播放"
        return self._state(
            enabled=True,
            playing=True,
            progress=progress,
            duration_ms=duration_ms,
            timer_running=True,
            status_message=status,
        )

    def paused(self, progress: float, duration_ms: int) -> AudioControlsViewState:
        return self._state(
            enabled=True,
            progress=progress,
            duration_ms=duration_ms,
            status_message="已暂停",
        )

    def seeked(self, position: float, duration_ms: int) -> AudioControlsViewState:
        return self._state(enabled=True, progress=position, duration_ms=duration_ms)

    def progress(
        self,
        position: float,
        duration_ms: int,
        *,
        update_progress: bool = True,
    ) -> AudioControlsViewState:
        return self._state(
            enabled=True,
            playing=True,
            progress=position,
            duration_ms=duration_ms,
            timer_running=True,
            update_progress=update_progress,
        )

    def ended(self, duration_ms: int) -> AudioControlsViewState:
        return self._state(
            enabled=True,
            progress=1.0,
            duration_ms=duration_ms,
            status_message="播放已结束",
        )

    def _state(self, **overrides) -> AudioControlsViewState:
        base = AudioControlsViewState(
            enabled=False,
            playing=False,
            progress=0.0,
            duration_ms=0,
            time_text="00:00 / 00:00",
            timer_running=False,
        )
        if "progress" in overrides:
            overrides["progress"] = self._clamp_progress(overrides["progress"])
        if "duration_ms" in overrides:
            overrides["duration_ms"] = max(0, int(overrides["duration_ms"]))
        state = replace(base, **overrides)
        if state.duration_ms > 0:
            state = replace(state, time_text=self._time_text(state.progress, state.duration_ms))
        return state

    def _time_text(self, progress: float, duration_ms: int) -> str:
        duration_ms = max(0, int(duration_ms))
        if duration_ms <= 0:
            return "00:00 / 00:00"
        current_ms = int(self._clamp_progress(progress) * duration_ms)
        return f"{self._format_ms(current_ms)} / {self._format_ms(duration_ms)}"

    def _format_ms(self, milliseconds: int) -> str:
        seconds = max(0, int(milliseconds)) // 1000
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _clamp_progress(self, value: float) -> float:
        return max(0.0, min(1.0, float(value)))
