from ludiglot.core.capture import CaptureRegion
from ludiglot.core.selection_geometry import (
    MonitorGeometry,
    Rect,
    ScreenGeometry,
    crop_box_for_snapshot_region,
    expand_region_within_monitor,
    map_selection_to_capture_region,
    normalize_monitors_to_image_size,
)


def test_maps_single_screen_at_100_percent_dpi():
    mapping = map_selection_to_capture_region(
        Rect(10, 20, 300, 100),
        [ScreenGeometry(index=0, x=0, y=0, width=1920, height=1080, dpr=1.0)],
        use_monitor_scale=False,
    )

    assert mapping.region == CaptureRegion(10, 20, 300, 100)
    assert mapping.screen_index == 0
    assert mapping.scale_x == 1.0
    assert mapping.source == "dpr-absolute"


def test_maps_single_screen_at_150_percent_dpi():
    mapping = map_selection_to_capture_region(
        Rect(10, 20, 300, 100),
        [ScreenGeometry(index=0, x=0, y=0, width=1920, height=1080, dpr=1.5)],
        use_monitor_scale=False,
    )

    assert mapping.region == CaptureRegion(15, 30, 450, 150)
    assert mapping.scale_x == 1.5
    assert mapping.source == "dpr-absolute"


def test_maps_negative_coordinate_secondary_monitor_with_dpr_monitor_origin():
    screens = [
        ScreenGeometry(index=0, x=0, y=0, width=1920, height=1080, dpr=1.0),
        ScreenGeometry(index=1, x=-1707, y=0, width=1707, height=960, dpr=1.5),
    ]
    monitors = [
        MonitorGeometry(left=-2560, top=0, width=5440, height=1620),
        MonitorGeometry(left=0, top=0, width=2880, height=1620),
        MonitorGeometry(left=-2560, top=0, width=2560, height=1440),
    ]

    mapping = map_selection_to_capture_region(
        Rect(-1607, 20, 300, 100),
        screens,
        monitors=monitors,
        use_monitor_scale=False,
    )

    assert mapping.region == CaptureRegion(-2410, 30, 450, 150)
    assert mapping.screen_index == 1
    assert mapping.source == "dpr-monitor"


def test_snapshot_monitor_mapping_matches_monitor_by_geometry_not_index():
    screens = [
        ScreenGeometry(index=0, x=0, y=0, width=1920, height=1080, dpr=1.0),
        ScreenGeometry(index=1, x=-1280, y=0, width=1280, height=720, dpr=1.0),
    ]
    monitors = [
        MonitorGeometry(left=-1280, top=0, width=3200, height=1080),
        MonitorGeometry(left=0, top=0, width=1920, height=1080),
        MonitorGeometry(left=-1280, top=0, width=1280, height=720),
    ]

    mapping = map_selection_to_capture_region(Rect(-1180, 20, 100, 50), screens, monitors=monitors)

    assert mapping.region == CaptureRegion(-1180, 20, 100, 50)
    assert mapping.monitor == monitors[2]


    mapping = map_selection_to_capture_region(
        Rect(100, 50, 300, 100),
        [ScreenGeometry(index=0, x=0, y=0, width=1920, height=1080, dpr=1.25)],
        monitors=[
            MonitorGeometry(left=0, top=0, width=3840, height=2160),
            MonitorGeometry(left=0, top=0, width=3840, height=2160),
        ],
    )

    assert mapping.region == CaptureRegion(200, 100, 600, 200)
    assert mapping.scale_x == 2.0
    assert mapping.scale_y == 2.0
    assert mapping.source == "snapshot-monitor"


def test_crop_box_for_snapshot_region_clamps_to_image_bounds():
    box = crop_box_for_snapshot_region(
        snapshot_left=-100,
        snapshot_top=-50,
        snapshot_width=500,
        snapshot_height=300,
        region=CaptureRegion(left=-150, top=-60, width=200, height=100),
    )

    assert box == (0, 0, 150, 90)


def test_normalize_monitors_to_image_size_scales_monitor_coordinates():
    monitors = normalize_monitors_to_image_size(
        [
            MonitorGeometry(left=-100, top=0, width=200, height=100),
            MonitorGeometry(left=-100, top=0, width=100, height=100),
            MonitorGeometry(left=0, top=0, width=100, height=100),
        ],
        image_width=400,
        image_height=200,
    )

    assert monitors == [
        MonitorGeometry(left=-200, top=0, width=400, height=200),
        MonitorGeometry(left=-200, top=0, width=200, height=200),
        MonitorGeometry(left=0, top=0, width=200, height=200),
    ]


def test_expand_region_within_monitor_clamps_to_bounds_and_min_size():
    expanded = expand_region_within_monitor(
        CaptureRegion(left=100, top=50, width=80, height=30),
        MonitorGeometry(left=0, top=0, width=500, height=200),
        margin_x=40,
        margin_y=30,
        min_width=200,
        min_height=100,
    )

    assert expanded == CaptureRegion(left=40, top=15, width=200, height=100)
