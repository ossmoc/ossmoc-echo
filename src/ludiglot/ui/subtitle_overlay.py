"""Small, non-activating subtitle window, independent of the control panel."""
from __future__ import annotations

import ctypes
import math
import sys
from typing import Any

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QTextLayout, QTextOption
from PyQt6.QtWidgets import QApplication, QWidget


DEFAULT_OVERLAY_SETTINGS = {
    "font_family": "Segoe UI", "font_size": 24, "text_color": "#ffffff",
    "outline_color": "#14181f", "outline_width": 2, "line_spacing": 1.15,
    "alignment": "center", "background": "none", "background_opacity": 32,
    "show_source": False, "width": 1000, "height": 240,
}


class SubtitleOverlay(QWidget):
    geometry_changed = pyqtSignal(dict)
    capture_exclusion_changed = pyqtSignal(bool)

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.settings = dict(DEFAULT_OVERLAY_SETTINGS)
        self.blocks: list[dict[str, str]] = []
        self.user_hidden = False
        self.capture_excluded = False
        self.content_truncated = False
        self._edit_mode = False
        self._drag_origin: QPoint | None = None
        self._start_geometry = None
        self._resizing = False
        self._affinity_requested = False
        self._capture_tested = False
        self._capture_test_pending = False
        self._probe = False
        self._settings_geometry = False
        self._set_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 100)
        self.setWindowTitle("Ossmoc Echo — Phụ đề")
        screen = QApplication.primaryScreen()
        if screen:
            bounds = screen.availableGeometry()
            self.settings.update(x=bounds.x() + (bounds.width() - 1000) // 2,
                                 y=bounds.bottom() - 270)
        else:
            self.settings.update(x=100, y=500)
        self.apply_settings(settings or {})

    @property
    def edit_mode(self) -> bool:
        return self._edit_mode

    def _set_flags(self) -> None:
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        if not self._edit_mode:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)

    @staticmethod
    def _number(value, default, minimum, maximum) -> float:
        try:
            result = float(value)
            if not math.isfinite(result):
                return float(default)
            return min(maximum, max(minimum, result))
        except (TypeError, ValueError):
            return float(default)

    def apply_settings(self, settings: dict[str, Any]) -> None:
        merged = {**self.settings, **settings}
        merged["font_size"] = self._number(merged.get("font_size"), 24, 10, 96)
        merged["outline_width"] = self._number(merged.get("outline_width"), 2, 0, 8)
        merged["line_spacing"] = self._number(merged.get("line_spacing"), 1.15, 1, 2.5)
        merged["background_opacity"] = self._number(merged.get("background_opacity"), 32, 0, 100)
        merged["font_family"] = str(merged.get("font_family") or "Segoe UI")
        for key, default in (("text_color", "#ffffff"), ("outline_color", "#14181f")):
            if not QColor(str(merged.get(key))).isValid():
                merged[key] = default
        if merged.get("background") not in {"none", "white", "black"}:
            merged["background"] = "none"
        if merged.get("alignment") not in {"left", "center", "right"}:
            merged["alignment"] = "center"
        self.settings = merged
        try:
            geometry = tuple(int(merged[k]) for k in ("x", "y", "width", "height"))
        except (TypeError, ValueError, KeyError):
            geometry = (self.x(), self.y(), self.width(), self.height())
        geometry = (geometry[0], geometry[1], max(240, min(6000, geometry[2])), max(100, min(3000, geometry[3])))
        # A disconnected monitor must not leave the only output window unreachable.
        # OCR bounds deliberately are NOT remapped; this only recovers the UI.
        from PyQt6.QtCore import QRect
        visible = QRect(*geometry)
        screens = QApplication.screens()
        if screens and not any(visible.intersected(s.availableGeometry()).width() >= 80
                               and visible.intersected(s.availableGeometry()).height() >= 40 for s in screens):
            bounds = QApplication.primaryScreen().availableGeometry()
            width = min(geometry[2], max(240, bounds.width() - 40))
            height = min(geometry[3], max(100, bounds.height() - 40))
            geometry = (bounds.x() + (bounds.width() - width) // 2,
                        bounds.y() + max(0, bounds.height() - height - 30), width, height)
            self.settings.update(dict(zip(("x", "y", "width", "height"), geometry)))
        if self.geometry().getRect() != geometry:
            self._settings_geometry = True
            self.setGeometry(*geometry)
            self._settings_geometry = False
        self.update()

    def set_content(self, blocks: list[dict[str, Any]]) -> None:
        normalized = [{"name": str(b.get("name") or ""), "source": str(b.get("source") or ""),
                       "target": str(b.get("target") or "")} for b in blocks if b.get("target")]
        if self.blocks != normalized:
            self.blocks = normalized
            self.update()
        if not self.blocks and not self._edit_mode:
            self.hide()

    def show_output(self) -> None:
        if self.user_hidden or not (self.blocks or self._edit_mode):
            return
        self.show()
        self.raise_()

    def toggle_hidden(self) -> None:
        self.user_hidden = not self.user_hidden
        if self.user_hidden:
            self.hide()
        else:
            self.show_output()

    def set_edit_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._edit_mode == enabled:
            return
        self._edit_mode = enabled
        was_visible = self.isVisible()
        self._set_flags()  # Qt recreates the native window; reapply capture affinity.
        self._capture_tested = False
        self.capture_excluded = False
        self.setCursor(Qt.CursorShape.SizeAllCursor if enabled else Qt.CursorShape.ArrowCursor)
        if enabled:
            self.user_hidden = False
        if was_visible or enabled:
            self.show_output()
        if not enabled:
            self._emit_geometry()
            if not self.blocks:
                self.hide()
        self.update()

    def reset_position(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            bounds = screen.availableGeometry()
            width = min(1000, max(240, bounds.width() - 80))
            self.setGeometry(bounds.x() + (bounds.width() - width) // 2,
                             bounds.bottom() - 270, width, 240)
            self._emit_geometry()

    def _emit_geometry(self) -> None:
        geometry = dict(zip(("x", "y", "width", "height"), self.geometry().getRect()))
        self.settings.update(geometry)
        self.geometry_changed.emit(geometry)

    def region_bounds_physical(self) -> dict[str, int] | None:
        if not self.isVisible():
            return None
        if sys.platform == "win32":
            try:
                from ctypes import wintypes
                rect = wintypes.RECT()
                get_rect = ctypes.windll.user32.GetWindowRect
                get_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                get_rect.restype = wintypes.BOOL
                if get_rect(int(self.winId()), ctypes.byref(rect)):
                    return {"left": rect.left, "top": rect.top,
                            "width": rect.right - rect.left, "height": rect.bottom - rect.top}
            except (AttributeError, OSError):
                pass
        screen = self.screen()
        dpr = screen.devicePixelRatio() if screen else 1
        return {"left": round(self.x() * dpr), "top": round(self.y() * dpr),
                "width": round(self.width() * dpr), "height": round(self.height() * dpr)}

    def _apply_capture_affinity(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            from ctypes import wintypes
            apply = ctypes.windll.user32.SetWindowDisplayAffinity
            apply.argtypes = [wintypes.HWND, wintypes.DWORD]
            apply.restype = wintypes.BOOL
            return bool(apply(int(self.winId()), 0x11))  # WDA_EXCLUDEFROMCAPTURE
        except (AttributeError, OSError):
            return False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._affinity_requested = self._apply_capture_affinity()
        if not self._affinity_requested and self.capture_excluded:
            self.capture_excluded = False
            self.capture_exclusion_changed.emit(False)
        if not self._capture_tested and not self._capture_test_pending:
            self._capture_test_pending = True
            QTimer.singleShot(250, self.verify_capture_exclusion)

    def verify_capture_exclusion(self) -> bool:
        """Verify with the same MSS backend, not just SetWindowDisplayAffinity's return value.

        A tiny three-colour marker is rendered once. A capture containing any of
        those colours is conservatively rejected. No repeated hide/show loop.
        """
        self._capture_test_pending = False
        if not self.isVisible():
            return False
        self._capture_tested = True
        confirmed = False
        if self._affinity_requested:
            try:
                import mss
                bounds = self.region_bounds_physical()
                dpr = self.devicePixelRatioF()
                self._probe = True
                self.repaint()
                if sys.platform == "win32":
                    ctypes.windll.dwmapi.DwmFlush()
                probe = {"left": bounds["left"] + round(5 * dpr),
                         "top": bounds["top"] + round(5 * dpr),
                         "width": max(1, round(24 * dpr)), "height": max(1, round(12 * dpr))}
                with mss.mss() as capture:
                    shot = capture.grab(probe)
                    colors = {(255, 0, 197), (0, 251, 113), (17, 83, 255)}
                    visible_marker = any(shot.pixel(x, y) in colors for y in range(shot.height) for x in range(shot.width))
                    confirmed = not visible_marker
            except Exception:
                confirmed = False
            finally:
                self._probe = False
                self.update()
        self.capture_excluded = confirmed
        self.capture_exclusion_changed.emit(confirmed)
        return confirmed

    def _paragraph_lines(self, text: str, font: QFont, width: float):
        for paragraph in text.split("\n"):
            if not paragraph:
                yield "", 0.0
                continue
            layout = QTextLayout(paragraph, font)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            layout.setTextOption(option)
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(width)
                yield paragraph[line.textStart():line.textStart() + line.textLength()].rstrip(), line.naturalTextWidth()
            layout.endLayout()

    def _draw_text(self, painter: QPainter, text: str, y: float, font: QFont,
                   color: QColor, area: QRectF) -> float:
        metrics = QFontMetricsF(font)
        line_height = metrics.height() * float(self.settings["line_spacing"])
        for line, _ in self._paragraph_lines(text, font, area.width()):
            if y + line_height > area.bottom():
                self.content_truncated = True
                break
            width = metrics.horizontalAdvance(line)
            align = self.settings["alignment"]
            x = area.left() if align == "left" else area.right() - width if align == "right" else area.center().x() - width / 2
            path = QPainterPath()
            path.addText(QPointF(x, y + metrics.ascent()), font, line)
            outline = float(self.settings["outline_width"])
            if outline:
                painter.setPen(QPen(QColor(self.settings["outline_color"]), outline * 2,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPath(path)
            y += line_height
        return y

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        area = QRectF(self.rect()).adjusted(16, 14, -16, -14)
        background = self.settings["background"]
        if background != "none":
            color = QColor("#ffffff" if background == "white" else "#000000")
            color.setAlpha(round(float(self.settings["background_opacity"]) * 2.55))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        font = QFont(self.settings["font_family"])
        font.setPointSizeF(float(self.settings["font_size"]))
        self.content_truncated = False
        y = area.top()
        blocks = self.blocks
        if not blocks and self._edit_mode:
            blocks = [{"target": "Kéo để di chuyển • Kéo góc dưới phải để đổi kích thước", "source": ""}]
        for index, block in enumerate(blocks):
            if index:
                y += QFontMetricsF(font).height() * 0.35
            if self.settings.get("show_source") and block["source"]:
                source_font = QFont(font)
                source_font.setPointSizeF(font.pointSizeF() * 0.8)
                source_color = QColor(self.settings["text_color"])
                source_color.setAlpha(200)
                y = self._draw_text(painter, block["source"], y, source_font, source_color, area)
                y += 4
            y = self._draw_text(painter, block["target"], y, font, QColor(self.settings["text_color"]), area)
        if self.content_truncated:
            painter.setPen(QColor(self.settings["text_color"]))
            painter.setFont(font)
            painter.drawText(self.rect().adjusted(0, 0, -8, -2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "…")
        if self._edit_mode:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#2dd4bf"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
            painter.drawLine(self.width() - 20, self.height() - 5, self.width() - 5, self.height() - 20)
            painter.drawLine(self.width() - 12, self.height() - 5, self.width() - 5, self.height() - 12)
        if self._probe:
            for index, color in enumerate(("#ff00c5", "#00fb71", "#1153ff")):
                painter.fillRect(5 + index * 8, 5, 8, 12, QColor(color))

    def mousePressEvent(self, event) -> None:
        if self._edit_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._start_geometry = self.geometry()
            self._resizing = event.position().x() > self.width() - 28 and event.position().y() > self.height() - 28
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._edit_mode:
            return
        if self._drag_origin is not None:
            offset = event.globalPosition().toPoint() - self._drag_origin
            if self._resizing:
                self.resize(max(240, self._start_geometry.width() + offset.x()),
                            max(100, self._start_geometry.height() + offset.y()))
            else:
                self.move(self._start_geometry.topLeft() + offset)
        else:
            resize = event.position().x() > self.width() - 28 and event.position().y() > self.height() - 28
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if resize else Qt.CursorShape.SizeAllCursor)

    def mouseReleaseEvent(self, event) -> None:
        if self._edit_mode and self._drag_origin is not None:
            self._drag_origin = None
            self._start_geometry = None
            self._emit_geometry()
            event.accept()
        else:
            super().mouseReleaseEvent(event)
