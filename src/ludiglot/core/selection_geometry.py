from __future__ import annotations

from dataclasses import dataclass

from ludiglot.core.capture import CaptureRegion


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ScreenGeometry:
    index: int
    x: int
    y: int
    width: int
    height: int
    dpr: float = 1.0
    name: str = ""


@dataclass(frozen=True)
class MonitorGeometry:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class SelectionMapping:
    region: CaptureRegion
    screen_index: int
    scale_x: float
    scale_y: float
    source: str
    monitor: MonitorGeometry | None = None


def find_screen_index_for_rect_center(rect: Rect, screens: list[ScreenGeometry], default_index: int = 0) -> int:
    center_x = rect.x + rect.width // 2
    center_y = rect.y + rect.height // 2
    for screen in screens:
        if screen.x <= center_x < screen.x + screen.width and screen.y <= center_y < screen.y + screen.height:
            return screen.index
    return default_index


def map_selection_to_capture_region(
    rect: Rect,
    screens: list[ScreenGeometry],
    *,
    monitors: list[MonitorGeometry] | None = None,
    dpr_override: float | None = None,
    use_monitor_scale: bool = True,
) -> SelectionMapping:
    if screens:
        screen_index = find_screen_index_for_rect_center(rect, screens)
        screen = next((item for item in screens if item.index == screen_index), screens[0])
    else:
        screen_index = 0
        screen = ScreenGeometry(index=0, x=0, y=0, width=max(rect.width, 1), height=max(rect.height, 1))

    monitor = _matching_monitor_for_screen(screen, monitors)
    if use_monitor_scale and monitor is not None:
        scale_x = monitor.width / max(screen.width, 1)
        scale_y = monitor.height / max(screen.height, 1)
        rel_x = rect.x - screen.x
        rel_y = rect.y - screen.y
        region = CaptureRegion(
            left=int(monitor.left + int(rel_x * scale_x)),
            top=int(monitor.top + int(rel_y * scale_y)),
            width=int(rect.width * scale_x),
            height=int(rect.height * scale_y),
        )
        return SelectionMapping(region, screen_index, scale_x, scale_y, "snapshot-monitor", monitor)

    dpr = float(dpr_override) if dpr_override is not None else float(screen.dpr or 1.0)
    rel_x = rect.x - screen.x
    rel_y = rect.y - screen.y
    phy_x_local = int(rel_x * dpr)
    phy_y_local = int(rel_y * dpr)
    region = CaptureRegion(
        left=phy_x_local,
        top=phy_y_local,
        width=int(rect.width * dpr),
        height=int(rect.height * dpr),
    )
    source = "dpr-absolute"
    if monitor is not None:
        region = CaptureRegion(
            left=int(monitor.left + phy_x_local),
            top=int(monitor.top + phy_y_local),
            width=region.width,
            height=region.height,
        )
        source = "dpr-monitor"
    else:
        region = CaptureRegion(
            left=int(rect.x * dpr),
            top=int(rect.y * dpr),
            width=region.width,
            height=region.height,
        )
    return SelectionMapping(region, screen_index, dpr, dpr, source, monitor)


def _matching_monitor_for_screen(screen: ScreenGeometry, monitors: list[MonitorGeometry] | None) -> MonitorGeometry | None:
    if not monitors:
        return None
    candidates = monitors[1:] if len(monitors) > 1 else monitors
    if not candidates:
        return None
    expected = MonitorGeometry(
        left=int(screen.x * screen.dpr),
        top=int(screen.y * screen.dpr),
        width=int(screen.width * screen.dpr),
        height=int(screen.height * screen.dpr),
    )
    return max(candidates, key=lambda monitor: _overlap_area(monitor, expected))


def _overlap_area(a: MonitorGeometry, b: MonitorGeometry) -> int:
    left = max(a.left, b.left)
    top = max(a.top, b.top)
    right = min(a.left + a.width, b.left + b.width)
    bottom = min(a.top + a.height, b.top + b.height)
    return max(0, right - left) * max(0, bottom - top)


def normalize_monitors_to_image_size(
    monitors: list[MonitorGeometry],
    *,
    image_width: int,
    image_height: int,
) -> list[MonitorGeometry]:
    if not monitors:
        return []
    all_monitor = monitors[0]
    scale_x = int(image_width) / max(all_monitor.width, 1)
    scale_y = int(image_height) / max(all_monitor.height, 1)
    if abs(scale_x - 1.0) <= 0.01 and abs(scale_y - 1.0) <= 0.01:
        return list(monitors)
    return [
        MonitorGeometry(
            left=int(monitor.left * scale_x),
            top=int(monitor.top * scale_y),
            width=int(monitor.width * scale_x),
            height=int(monitor.height * scale_y),
        )
        for monitor in monitors
    ]


def crop_box_for_snapshot_region(
    *,
    snapshot_left: int,
    snapshot_top: int,
    snapshot_width: int,
    snapshot_height: int,
    region: CaptureRegion,
) -> tuple[int, int, int, int]:
    left = region.left - snapshot_left
    top = region.top - snapshot_top
    right = left + region.width
    bottom = top + region.height
    left = max(0, min(left, snapshot_width))
    top = max(0, min(top, snapshot_height))
    right = max(left, min(right, snapshot_width))
    bottom = max(top, min(bottom, snapshot_height))
    return left, top, right, bottom


def expand_region_within_monitor(
    region: CaptureRegion,
    monitor: MonitorGeometry,
    *,
    margin_x: int = 40,
    margin_y: int = 30,
    min_width: int = 600,
    min_height: int = 120,
) -> CaptureRegion:
    left = max(monitor.left, region.left - margin_x)
    top = max(monitor.top, region.top - margin_y)
    right = min(monitor.left + monitor.width, region.left + region.width + margin_x)
    bottom = min(monitor.top + monitor.height, region.top + region.height + margin_y)
    width = max(right - left, region.width)
    height = max(bottom - top, region.height)
    if width < min_width:
        extra = (min_width - width) // 2
        left = max(monitor.left, left - extra)
        right = min(monitor.left + monitor.width, right + extra)
        width = right - left
    if height < min_height:
        extra = (min_height - height) // 2
        top = max(monitor.top, top - extra)
        bottom = min(monitor.top + monitor.height, bottom + extra)
        height = bottom - top
    return CaptureRegion(left=int(left), top=int(top), width=int(width), height=int(height))
