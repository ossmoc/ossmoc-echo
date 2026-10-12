from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QSize, Qt, QRect
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap

from ludiglot.core.capture import CaptureRegion
from ludiglot.core.capture_input import (
    CaptureInputAdapters,
    capture_input_to_memory,
    capture_options_from_config,
)
from ludiglot.core.selection_geometry import (
    MonitorGeometry,
    Rect,
    ScreenGeometry,
    crop_box_for_snapshot_region,
    map_selection_to_capture_region,
    normalize_monitors_to_image_size,
)
from ludiglot.ui.screen_selection import ScreenSelector


@dataclass
class DesktopSnapshot:
    image: Any
    left: int
    top: int
    monitors: list[MonitorGeometry]
    screen_pixmaps: list[QPixmap]


class QtCaptureAdapter:
    def __init__(self, config: Any, log: Callable[[str], None]) -> None:
        self.config = config
        self.log = log
        from ludiglot.core.window_capture import WindowCapture
        self.window_capture = WindowCapture()
        self.window_settings = {"mode": "desktop"}

    def configure_window(self, settings, target=None):
        from ludiglot.core.window_capture import find_saved_window
        self.window_settings = dict(settings)
        self.window_capture.close()
        if settings.get("mode") == "window":
            target = target or find_saved_window(settings.get("target", {}))
            if target:
                self.window_capture.bind(target)

    def capture_auto_frame(self, selected_region, snapshot=None):
        if self.window_settings.get("mode") == "window":
            return self.window_capture.image()
        return self.capture_image_to_memory(selected_region, snapshot)

    def capture_image_to_memory(self, selected_region: CaptureRegion | None, snapshot: DesktopSnapshot | None = None) -> Any:
        if self.window_settings.get("mode") == "window":
            from ludiglot.core.window_capture import CaptureUnavailable, local_crop_box
            image = self.window_capture.image()
            anchor = self.window_settings.get("anchor")
            if not anchor or image.size != (anchor["width"], anchor["height"]):
                raise CaptureUnavailable("Kích thước cửa sổ game đã đổi. Chọn lại ứng dụng và kiểm tra vùng OCR.")
            if selected_region is None:
                raise CaptureUnavailable("Chưa khoanh vùng OCR trong cửa sổ game.")
            return image.crop(local_crop_box(selected_region, anchor, image.size))
        return capture_input_to_memory(
            capture_options_from_config(self.config),
            selected_region=selected_region,
            snapshot=snapshot,
            adapters=CaptureInputAdapters(
                select_region=self.select_region,
                crop_snapshot=self.crop_snapshot,
                on_fallback=self.log,
            ),
        )

    def capture_desktop_snapshot(self) -> DesktopSnapshot:
        backend = str(getattr(self.config, "capture_backend", "mss")).lower()
        try:
            import mss
        except Exception as exc:
            raise RuntimeError("缺少 mss，无法截图") from exc
        try:
            from PIL import Image, ImageGrab
        except Exception as exc:
            raise RuntimeError("缺少 Pillow，无法截图") from exc

        with mss.mss() as sct:
            raw_monitors = sct.monitors
            if not raw_monitors:
                raise RuntimeError("未检测到屏幕")
            all_mon = raw_monitors[0]
            if self.window_settings.get("mode") == "window":
                # The selector sees only the selected app on a blank desktop,
                # never the controller, browser, or inline translation windows.
                game = self.window_capture.image()
                bounds = game.info["capture_bounds"]
                img = Image.new("RGB", (all_mon["width"], all_mon["height"]))
                img.paste(game, (bounds["left"]-all_mon["left"], bounds["top"]-all_mon["top"]))
            elif backend == "winrt":
                bbox = (
                    all_mon["left"],
                    all_mon["top"],
                    all_mon["left"] + all_mon["width"],
                    all_mon["top"] + all_mon["height"],
                )
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
            else:
                sct_img = sct.grab(all_mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        monitors = normalize_monitors_to_image_size(
            [self._monitor_geometry_from_mapping(mon) for mon in raw_monitors],
            image_width=img.width,
            image_height=img.height,
        )
        all_monitor = monitors[0]
        screen_pixmaps = self._build_screen_pixmaps(img, monitors)
        return DesktopSnapshot(
            image=img,
            left=int(all_monitor.left),
            top=int(all_monitor.top),
            monitors=monitors,
            screen_pixmaps=screen_pixmaps,
        )

    def crop_snapshot(self, snapshot: DesktopSnapshot, region: CaptureRegion):
        box = crop_box_for_snapshot_region(
            snapshot_left=snapshot.left,
            snapshot_top=snapshot.top,
            snapshot_width=snapshot.image.width,
            snapshot_height=snapshot.image.height,
            region=region,
        )
        return snapshot.image.crop(box)

    def select_region(self, snapshot: DesktopSnapshot | None = None) -> CaptureRegion | None:
        """选择屏幕区域并转换为物理像素坐标（适配多屏不同DPI）。"""
        selector = ScreenSelector(snapshot.screen_pixmaps if snapshot else None)
        rect = selector.get_region()

        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None

        print(f"[框选] 逻辑坐标: {rect}")
        rect_model = self._rect_from_qrect(rect)
        screens = self._screen_geometries()

        if snapshot is not None:
            try:
                mapping = map_selection_to_capture_region(rect_model, screens, monitors=snapshot.monitors)
                if mapping.source == "snapshot-monitor":
                    print(f"[框选] 快照映射: MSS Monitor={mapping.monitor}")
                    print(f"[框选] 快照缩放: sx={mapping.scale_x:.3f}, sy={mapping.scale_y:.3f}")
                    print(
                        f"[框选] 最终物理坐标: ({mapping.region.left}, {mapping.region.top}, "
                        f"{mapping.region.width}, {mapping.region.height})"
                    )
                    return mapping.region
            except Exception as e:
                print(f"[框选] 快照映射失败: {e}，回退到实时坐标映射")

        dpr_override = None
        override_raw = getattr(self.config, "capture_force_dpr", None)
        if override_raw is not None:
            try:
                dpr_override = float(override_raw)
            except Exception:
                pass

        monitors = self._current_mss_monitors()
        mapping = map_selection_to_capture_region(
            rect_model,
            screens,
            monitors=monitors,
            dpr_override=dpr_override,
            use_monitor_scale=False,
        )
        screen = next((item for item in screens if item.index == mapping.screen_index), screens[0] if screens else None)
        screen_name = screen.name if screen else ""
        suffix = " (override)" if dpr_override is not None else ""
        print(f"[框选] 命中屏幕: {screen_name} (Index {mapping.screen_index}), DPR: {mapping.scale_x}{suffix}")

        if mapping.source == "dpr-monitor":
            print(f"[框选] MSS Monitor: {mapping.monitor}")
        elif monitors:
            print("[框选] 警告：MSS 屏幕数量不匹配，回退到主屏估算")

        print(
            f"[框选] 最终物理坐标: ({mapping.region.left}, {mapping.region.top}, "
            f"{mapping.region.width}, {mapping.region.height})"
        )
        return mapping.region

    def _pil_to_pixmap(self, img, target_size: QSize | None = None) -> QPixmap:
        try:
            from PIL import Image
        except Exception as exc:
            raise RuntimeError("缺少 Pillow，无法生成截图背景") from exc
        if not isinstance(img, Image.Image):
            raise RuntimeError("无效截图对象，无法生成截图背景")
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        qimage = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
        qimage = qimage.copy()
        pixmap = QPixmap.fromImage(qimage)
        if target_size and (pixmap.width() != target_size.width() or pixmap.height() != target_size.height()):
            pixmap = pixmap.scaled(
                target_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pixmap

    def _monitor_geometry_from_mapping(self, monitor: dict) -> MonitorGeometry:
        return MonitorGeometry(
            left=int(monitor["left"]),
            top=int(monitor["top"]),
            width=int(monitor["width"]),
            height=int(monitor["height"]),
        )

    def _rect_from_qrect(self, rect: QRect) -> Rect:
        return Rect(x=int(rect.x()), y=int(rect.y()), width=int(rect.width()), height=int(rect.height()))

    def _screen_geometry_from_qscreen(self, screen, index: int) -> ScreenGeometry:
        geo = screen.geometry()
        return ScreenGeometry(
            index=index,
            x=int(geo.x()),
            y=int(geo.y()),
            width=int(geo.width()),
            height=int(geo.height()),
            dpr=float(screen.devicePixelRatio()),
            name=str(screen.name()),
        )

    def _screen_geometries(self) -> list[ScreenGeometry]:
        return [self._screen_geometry_from_qscreen(screen, idx) for idx, screen in enumerate(QGuiApplication.screens())]

    def _current_mss_monitors(self) -> list[MonitorGeometry]:
        try:
            import mss
            with mss.mss() as sct:
                return [self._monitor_geometry_from_mapping(mon) for mon in sct.monitors]
        except Exception:
            return []

    def _build_screen_pixmaps(self, desktop_img, monitors: list[MonitorGeometry]) -> list[QPixmap]:
        screens = QGuiApplication.screens()
        if not monitors:
            return []
        all_mon = monitors[0]
        pixmaps: list[QPixmap] = []
        for idx, screen in enumerate(screens):
            if idx + 1 < len(monitors):
                mon = monitors[idx + 1]
                crop_left = mon.left - all_mon.left
                crop_top = mon.top - all_mon.top
                crop_right = crop_left + mon.width
                crop_bottom = crop_top + mon.height
                crop = desktop_img.crop((crop_left, crop_top, crop_right, crop_bottom))
            else:
                crop = desktop_img
            pixmaps.append(self._pil_to_pixmap(crop, screen.geometry().size()))
        return pixmaps
