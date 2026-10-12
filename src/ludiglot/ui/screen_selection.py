from __future__ import annotations

from PyQt6.QtCore import QObject, QEventLoop, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap, QGuiApplication
from PyQt6.QtWidgets import QRubberBand, QWidget


class ScreenOverlay(QWidget):
    """单屏幕覆盖窗口"""
    region_selected = pyqtSignal(QRect)

    def __init__(self, screen, background: QPixmap | None = None, parent=None):
        super().__init__(parent)
        self._screen = screen
        self._background = background
        self.setGeometry(screen.geometry()) # 逻辑坐标

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 每个窗口只负责自己所在的屏幕，避免跨屏DPI问题
        self.setScreen(screen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self.rubber_band: QRubberBand | None = None
        self.origin: QPoint | None = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if not painter.isActive(): return
        if self._background is not None:
            painter.drawPixmap(self.rect(), self._background)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80)) # 半透明遮罩
        painter.end()

    def mousePressEvent(self, event) -> None:
        self.origin = event.pos()
        if self.rubber_band is None:
            self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        if self.origin:
            self.rubber_band.setGeometry(QRect(self.origin, event.pos()).normalized())
        self.rubber_band.show()

    def mouseMoveEvent(self, event) -> None:
        if self.rubber_band and self.origin:
            self.rubber_band.setGeometry(QRect(self.origin, event.pos()).normalized())

    def mouseReleaseEvent(self, event) -> None:
        if self.rubber_band and self.origin:
            rect = self.rubber_band.geometry().normalized()
            # 转换为全局逻辑坐标
            global_top_left = self.mapToGlobal(rect.topLeft())
            global_rect = QRect(global_top_left, rect.size())
            self.region_selected.emit(global_rect)
        else:
            self.region_selected.emit(QRect())

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.region_selected.emit(QRect())
        else:
            super().keyPressEvent(event)


class ScreenSelector(QObject):
    """全屏选区控制器，管理多屏覆盖窗口。"""
    region_selected_signal = pyqtSignal(QRect)

    def __init__(self, backgrounds: list[QPixmap] | None = None) -> None:
        super().__init__()
        self._overlays: list[ScreenOverlay] = []
        self._selected_rect: QRect | None = None
        self._loop: QEventLoop | None = None

        # 为每个屏幕创建一个覆盖窗口
        screens = QGuiApplication.screens()
        for idx, screen in enumerate(screens):
            bg = backgrounds[idx] if backgrounds and idx < len(backgrounds) else None
            overlay = ScreenOverlay(screen, bg)
            overlay.region_selected.connect(self._on_region_selected)
            self._overlays.append(overlay)

        print(f"[ScreenSelector] 已初始化 {len(self._overlays)} 个屏幕覆盖层")

    def get_region(self) -> QRect | None:
        try:
            self._loop = QEventLoop()

            for overlay in self._overlays:
                overlay.showFullScreen()
                overlay.raise_()
                overlay.activateWindow()

            self._loop.exec()
            return self._selected_rect
        except Exception as e:
            print(f"[ScreenSelector ERROR] {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            for overlay in self._overlays:
                overlay.close()

    def _on_region_selected(self, rect: QRect) -> None:
        # 当任意一个屏幕完成了选区，保存结果并退出循环
        if not rect.isNull():
            self._selected_rect = rect
        if self._loop and self._loop.isRunning():
            self._loop.quit()
