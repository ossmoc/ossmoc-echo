from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Dict

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QPoint, QPointF, QRect, QRectF, QEvent
from PyQt6.QtGui import QFont, QTextOption, QColor, QAction, QActionGroup, QCursor, QPainter, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QMenu, QComboBox, QSizePolicy,
    QSlider, QSpinBox, QDoubleSpinBox, QWidgetAction, QStyle,
    QFrame
)

from ludiglot.core.config import AppConfig
from ludiglot.core.overlay_runtime import (
    create_overlay_ocr_engine,
    initialize_overlay_runtime,
)
from ludiglot.core.overlay_audio_runtime import OverlayAudioRuntime
from ludiglot.core.display_shaper import DisplayPreferences
from ludiglot.core.preferences import (
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    ConfigJsonStore,
    OverlayPreferences,
    WindowBounds,
    WindowPoint,
    WindowSize,
    clamp_window_position,
)
from ludiglot.infrastructure.terminal_log_tee import install_process_log_tee
from ludiglot.ui.database_update_controller import DatabaseUpdateController
from ludiglot.ui.overlay_composition import (
    connect_overlay_composition_signals,
    create_runtime_callbacks,
    install_app_event_filter,
    install_audio_player,
    install_audio_playback_controls,
    install_capture_session,
    install_hotkey_registrar,
    install_result_presentation,
    install_sync_timer,
)
from ludiglot.ui.waveform_progress_bar import AudioWaveformProgressBar


class PersistentMenu(QMenu):
    """自定义菜单，支持在操作某些控件时保持开启。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 启用半透明背景，去除系统默认白色底框
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # 启用无边框窗口标记，彻底剥离 Windows 11 等操作系统的 native 强行大圆角和原生阴影，使 2px border-radius 生效
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)

    def showEvent(self, event):
        super().showEvent(event)
        parent_menu = self.parent()
        # 如果当前是一个嵌套子菜单，我们强制重新计算它的弹出坐标，让它完美贴合在母菜单边界之外，绝不覆盖
        if isinstance(parent_menu, QMenu):
            # 获取母菜单的物理屏幕坐标和宽度
            p_geom = parent_menu.geometry()
            my_geom = self.geometry()
            
            # 检测当前的展开方向是向左还是向右，并做高精度对齐
            direction = "left" if parent_menu.layoutDirection() == Qt.LayoutDirection.RightToLeft else "right"
            if direction == "left":
                # 向左展开：子选单的右边界对齐母选单的左边界
                new_x = p_geom.x() - my_geom.width()
            else:
                # 向右展开：子选单的左边界对齐母选单的右边界
                new_x = p_geom.x() + p_geom.width()
                
            self.move(new_x, my_geom.y())

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.position().toPoint())
        
        # 如果是叶子节点动作且标记为 persistent，则触发但不关闭
        if action and not action.menu() and action.property("persistent"):
            action.trigger()
            return # 关键：不调用 super() 阻止菜单关闭
            
        super().mouseReleaseEvent(event)


class GoldSpinBox(QSpinBox):
    """自绘制箭头的金铜色微调输入框，确保在任何布局和渲染引擎下100%显示完美的矢量三角形图标"""
    def paintEvent(self, event):
        super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        from PyQt6.QtWidgets import QStyle, QStyleOptionSpinBox
        from PyQt6.QtGui import QPolygonF
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        
        # 固定高度时 SC_SpinBoxUp/Down 的矩形方向是正确的，直接使用
        up_rect = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxUp, self)
        down_rect = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxDown, self)
        
        mouse_pos = self.mapFromGlobal(QCursor.pos())
        
        # 绘制向上箭头 ▲
        if not up_rect.isEmpty():
            painter.save()
            is_hover = self.underMouse() and up_rect.contains(mouse_pos)
            color = QColor(255, 255, 255, 255) if is_hover else QColor(170, 155, 106, 220)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            cx = up_rect.x() + up_rect.width() / 2.0
            cy = up_rect.y() + up_rect.height() / 2.0
            poly = QPolygonF([QPointF(cx, cy - 2.5), QPointF(cx + 3.5, cy + 1.5), QPointF(cx - 3.5, cy + 1.5)])
            painter.drawPolygon(poly)
            painter.restore()

        # 绘制向下箭头 ▼
        if not down_rect.isEmpty():
            painter.save()
            is_hover = self.underMouse() and down_rect.contains(mouse_pos)
            color = QColor(255, 255, 255, 255) if is_hover else QColor(170, 155, 106, 220)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            cx = down_rect.x() + down_rect.width() / 2.0
            cy = down_rect.y() + down_rect.height() / 2.0
            poly = QPolygonF([QPointF(cx - 3.5, cy - 1.5), QPointF(cx + 3.5, cy - 1.5), QPointF(cx, cy + 2.5)])
            painter.drawPolygon(poly)
            painter.restore()


class GoldDoubleSpinBox(QDoubleSpinBox):
    """自绘制箭头的金铜色双精度微调输入框，确保在任何布局和渲染引擎下100%显示完美的矢量三角形图标"""
    def paintEvent(self, event):
        super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        from PyQt6.QtWidgets import QStyle, QStyleOptionSpinBox
        from PyQt6.QtGui import QPolygonF
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        
        # 固定高度时 SC_SpinBoxUp/Down 的矩形方向是正确的，直接使用
        up_rect = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxUp, self)
        down_rect = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxDown, self)
        
        mouse_pos = self.mapFromGlobal(QCursor.pos())
        
        # 绘制向上箭头 ▲
        if not up_rect.isEmpty():
            painter.save()
            is_hover = self.underMouse() and up_rect.contains(mouse_pos)
            color = QColor(255, 255, 255, 255) if is_hover else QColor(170, 155, 106, 220)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            cx = up_rect.x() + up_rect.width() / 2.0
            cy = up_rect.y() + up_rect.height() / 2.0
            poly = QPolygonF([QPointF(cx, cy - 2.5), QPointF(cx + 3.5, cy + 1.5), QPointF(cx - 3.5, cy + 1.5)])
            painter.drawPolygon(poly)
            painter.restore()

        # 绘制向下箭头 ▼
        if not down_rect.isEmpty():
            painter.save()
            is_hover = self.underMouse() and down_rect.contains(mouse_pos)
            color = QColor(255, 255, 255, 255) if is_hover else QColor(170, 155, 106, 220)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            cx = down_rect.x() + down_rect.width() / 2.0
            cy = down_rect.y() + down_rect.height() / 2.0
            poly = QPolygonF([QPointF(cx - 3.5, cy - 1.5), QPointF(cx + 3.5, cy - 1.5), QPointF(cx, cy + 2.5)])
            painter.drawPolygon(poly)
            painter.restore()


class PlayPauseButton(QPushButton):
    """自绘制的播放/暂停按钮，保证图标完美居中和颜色可调，并带有精致的悬浮和状态切换动效。"""
    def __init__(self, parent=None):
        super().__init__("", parent)
        self._is_playing = False
        self._hover_val = 0.0
        self._press_val = 0.0
        self._state_val = 0.0  # 0.0 = Play, 1.0 = Pause
        
        # 强制覆盖全局 QSS，防止受到全局 QPushButton 的影响
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 0px;
            }
        """)
        
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        
        # 悬浮动画：0.0 -> 1.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(180)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._handle_hover_anim)
        
        # 播放/暂停状态切换动画：0.0 -> 1.0，OutBack 带有微弹回感，尽显灵动
        self._state_anim = QVariantAnimation(self)
        self._state_anim.setDuration(300)
        self._state_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._state_anim.valueChanged.connect(self._handle_state_anim)

    def _handle_hover_anim(self, value):
        self._hover_val = value
        self.update()

    def _handle_state_anim(self, value):
        self._state_val = value
        self.update()

    def set_playing(self, playing: bool):
        if self._is_playing != playing:
            self._is_playing = playing
            self._state_anim.stop()
            self._state_anim.setStartValue(self._state_val)
            self._state_anim.setEndValue(1.0 if playing else 0.0)
            self._state_anim.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_val)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_val)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_val = 1.0
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_val = 0.0
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        
        # 禁用或各类状态的颜色和微动效融合计算
        if not self.isEnabled():
            bg_color = QColor(255, 255, 255, 12)
            border_color = QColor(255, 255, 255, 25)
            icon_color = QColor(255, 255, 255, 76)
        else:
            h = self._hover_val
            p = self._press_val
            
            # 背景色：透明灰 -> 雅致金黄微亮
            bg_r = int(255 * (1.0 - h) + 170 * h)
            bg_g = int(255 * (1.0 - h) + 155 * h)
            bg_b = int(255 * (1.0 - h) + 106 * h)
            bg_a = int((38 * (1.0 - h) + 60 * h) * (1.0 - p * 0.2))
            bg_color = QColor(bg_r, bg_g, bg_b, bg_a)
            
            # 边框色：透白 -> 明亮金黄 #c9a64a
            border_r = int(255 * (1.0 - h) + 201 * h)
            border_g = int(255 * (1.0 - h) + 166 * h)
            border_b = int(255 * (1.0 - h) + 74 * h)
            border_a = int(76 * (1.0 - h) + 180 * h)
            border_color = QColor(border_r, border_g, border_b, border_a)
            
            # 图标颜色
            icon_r = int(200 * (1.0 - h) + 255 * h)
            icon_g = int(200 * (1.0 - h) + 255 * h)
            icon_b = int(200 * (1.0 - h) + 255 * h)
            icon_a = int(200 * (1.0 - h) + 255 * h)
            icon_color = QColor(icon_r, icon_g, icon_b, icon_a)

        # 1. 绘制背景/边框外框：暂停态是方形，播放态平滑旋转为菱形
        from PyQt6.QtGui import QPen
        import math

        cx = rect.width() / 2.0
        cy = rect.height() / 2.0
        frame_side = min(rect.width(), rect.height()) / math.sqrt(2) - 2.0
        frame_rect = QRectF(-frame_side / 2.0, -frame_side / 2.0, frame_side, frame_side)
        frame_rotation = self._state_val * 45.0

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(frame_rotation)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(frame_rect, 2.0, 2.0)

        pen = QPen(border_color, 1.0 + self._hover_val * 0.4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame_rect, 2.0, 2.0)
        painter.restore()

        # 2. 绘制图标（以中心为锚点应用变换）
        
        # 播放三角形（在 state_val 趋向 1.0 时顺时针旋转 90 度并淡出）
        if self._state_val < 0.99:
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(self._state_val * 90.0)
            
            opacity = 1.0 - self._state_val
            c_play = QColor(icon_color)
            c_play.setAlpha(int(icon_color.alpha() * opacity))
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c_play)
            
            from PyQt6.QtGui import QPolygonF
            size = 10.0
            half_size = size / 2.0
            offset = 0.5  # 视觉微调中心偏移
            p1 = QPointF(-half_size * 0.7 + offset, -half_size)
            p2 = QPointF(-half_size * 0.7 + offset, half_size)
            p3 = QPointF(half_size * 1.1 + offset, 0.0)
            poly = QPolygonF([p1, p2, p3])
            painter.drawPolygon(poly)
            painter.restore()
            
        # 暂停双线（从 -90 度逆时针转回 0 度并淡入）
        if self._state_val > 0.01:
            painter.save()
            painter.translate(cx, cy)
            painter.rotate((1.0 - self._state_val) * -90.0)
            
            opacity = self._state_val
            c_pause = QColor(icon_color)
            c_pause.setAlpha(int(icon_color.alpha() * opacity))
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c_pause)
            
            w = 3.0
            h = 10.0
            gap = 4.0
            
            # 左竖线
            x1 = -gap / 2.0 - w
            y1 = -h / 2.0
            painter.drawRect(QRectF(x1, y1, w, h))
            
            # 右竖线
            x2 = gap / 2.0
            painter.drawRect(QRectF(x2, y1, w, h))
            painter.restore()

        painter.end()


class CloseIconButton(QPushButton):
    """自绘制的关闭按钮，鸣潮风格的四片利刃组成的交叉图标。
    Hover 态为微动效：四片利刃平滑地朝外侧对角线方向扩张（无颜色和背景变化）。
    """
    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setFixedSize(32, 32)
        self._current_offset = 2.5  # 基础偏移，稍微移大，增加常规状态下的比例感
        
        # 强制覆盖全局 QSS，防止受到全局 QPushButton 的边框、底色和内边距影响
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 0px;
            }
        """)
        
        # 导入动画所需类
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(120)  # 120ms 灵动响应
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._handle_anim)
        
    def _handle_anim(self, value):
        self._current_offset = value
        self.update()
        
    def enterEvent(self, event):
        super().enterEvent(event)
        self._anim.stop()
        self._anim.setStartValue(self._current_offset)
        self._anim.setEndValue(6.0)  # 悬浮时利刃向外舒展 6.0px，展现出强烈的张力
        self._anim.start()
        
    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._anim.stop()
        self._anim.setStartValue(self._current_offset)
        self._anim.setEndValue(2.5)  # 移开时平滑缩回到 2.5px
        self._anim.start()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        cx = rect.width() / 2.0
        cy = rect.height() / 2.0
        
        # 纯净的白色/亮灰，无任何底色和彩色变幻
        if self.underMouse():
            icon_color = QColor(255, 255, 255, 230)  # 略微明亮以示交互
        else:
            icon_color = QColor(255, 255, 255, 180)  # 默认柔和白色
            
        # 绘制四瓣鸣潮风格利刃交叉
        painter.save()
        painter.translate(cx, cy)
        
        L = 12.5  # 增大：利刃整体长度为 12.5
        
        # 每次旋转 90 度画一片利刃，倾斜 45 度开始第一片
        painter.rotate(45)
        
        from PyQt6.QtGui import QPainterPath
        for _ in range(4):
            painter.save()
            # 沿着当前旋转方向 of X 轴平移，实现完美且极其平滑的向外舒展动画
            painter.translate(self._current_offset, 0)
            
            path = QPainterPath()
            # 刀刃起点移动至 0.0 开始绘制，基础宽度微增到 1.6 以适应比例
            path.moveTo(0.0, -1.6)
            # 绘制上弧线到刀尖
            path.quadTo(L * 0.4, -0.7, L - 1.8, 0.0)
            # 绘制下弧线到终点
            path.quadTo(L * 0.4, 0.7, 0.0, 1.6)
            path.closeSubpath()
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)
            painter.drawPath(path)
            
            # 绘制中间细微的利刃脊线
            from PyQt6.QtGui import QPen
            pen = QPen(QColor(0, 0, 0, 45), 0.8)
            painter.setPen(pen)
            painter.drawLine(QPointF(0.0, 0), QPointF(L - 3.0, 0))
            
            painter.restore()
            painter.rotate(90)
            
        painter.restore()
        painter.end()


class MenuIconButton(QPushButton):
    """自绘制的菜单/设置按钮，外部是六边形（无齿轮），内部是圆，内部的圆内有个直径稍小的圆形弧线。
    Hover 态为旋转与微放大动画：外部图标旋转 60 度，内部圆形弧线保持静止，整体放大 12%。
    """
    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setFixedSize(32, 32)
        self._anim_progress = 0.0
        
        # 强制覆盖全局 QSS，防止受到全局 QPushButton 的边框、底色和内边距影响
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 0px;
            }
        """)
        
        # 导入动画所需类
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(350)  # 350ms 顺滑旋转与放大
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._handle_anim)
        
    def _handle_anim(self, value):
        self._anim_progress = value
        self.update()
        
    def enterEvent(self, event):
        super().enterEvent(event)
        self._anim.stop()
        self._anim.setStartValue(self._anim_progress)
        self._anim.setEndValue(1.0)  # 悬浮态达到最大值
        self._anim.start()
        
    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._anim.stop()
        self._anim.setStartValue(self._anim_progress)
        self._anim.setEndValue(0.0)  # 恢复
        self._anim.start()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        cx = rect.width() / 2.0
        cy = rect.height() / 2.0
        
        # 悬停时仅高亮图标自身，不绘制任何背景色
        if self.underMouse() and self.isDown():
            icon_color = QColor(255, 255, 255, 255)
            arc_color = QColor(170, 155, 106, 250)  # 主金黄色
        elif self.underMouse():
            icon_color = QColor(255, 255, 255, 230)
            arc_color = QColor(170, 155, 106, 220)
        else:
            icon_color = QColor(255, 255, 255, 180)  # 默认淡白
            arc_color = QColor(150, 150, 150, 160)  # 默认灰色圆弧
            
        # 整体应用微放大（1.0 -> 1.2），创造高级的物理升起感
        painter.save()
        painter.translate(cx, cy)
        total_scale = 1.0 + self._anim_progress * 0.2
        painter.scale(total_scale, total_scale)
        
        # 1. 绘制会旋转的外部六边形与内部圆
        painter.save()
        angle = self._anim_progress * 60.0
        painter.rotate(angle)
        painter.scale(0.95, 0.95)  # 基础微调缩放以维持视觉精致度
        
        # 绘制六边形
        import math
        from PyQt6.QtGui import QPolygonF
        
        hexagon = QPolygonF()
        R = 9.5  # 六边形外接圆半径
        for i in range(6):
            # i * 60 + 30 使得平顶朝上，极其规整精美
            angle_rad = math.radians(i * 60 + 30)
            x = R * math.cos(angle_rad)
            y = R * math.sin(angle_rad)
            hexagon.append(QPointF(x, y))
            
        from PyQt6.QtGui import QPen
        hex_pen = QPen(icon_color, 1.5)
        painter.setPen(hex_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(hexagon)
        
        # 绘制内部圆
        r_inner = 4.5
        painter.drawEllipse(QPointF(0, 0), r_inner, r_inner)
        
        painter.restore()
        
        # 2. 绘制不旋转的内部圆形弧线（但参与整体的放大效果）
        painter.save()
        
        arc_pen = QPen(arc_color, 1.2)
        painter.setPen(arc_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        r_arc = 4.0  # 直径稍小的圆形弧线半径
        arc_rect = QRectF(-r_arc, -r_arc, r_arc * 2, r_arc * 2)
        start_angle = 45 * 16  # 起始角度
        span_angle = 270 * 16  # 跨越角度，留出优雅缺口
        painter.drawArc(arc_rect, start_angle, span_angle)
        
        painter.restore()
        painter.restore()
        painter.end()


class StarDivider(QWidget):
    """中间带四角星的分界线"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        cx = rect.width() / 2.0
        cy = rect.height() / 2.0
        
        # 绘制四角星
        painter.save()
        painter.translate(cx, cy)
        
        star_color = QColor(170, 155, 106, 220)  # 经典金黄色 #aa9b6a
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(star_color)
        
        # 绘制四角星的路径
        from PyQt6.QtGui import QPainterPath
        path = QPainterPath()
        R = 8.0  # 外半径
        r = 2.5  # 内半径
        
        path.moveTo(0, -R)
        path.lineTo(r, -r)
        path.lineTo(R, 0)
        path.lineTo(r, r)
        path.lineTo(0, R)
        path.lineTo(-r, r)
        path.lineTo(-R, 0)
        path.lineTo(-r, -r)
        path.closeSubpath()
        painter.drawPath(path)
        
        # 绘制四角星中心的微小亮核
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawEllipse(QPointF(0, 0), 1.2, 1.2)
        
        painter.restore()
        
        # 绘制两侧的渐变线条
        from PyQt6.QtGui import QLinearGradient, QPen
        
        # 左侧渐变线
        left_grad = QLinearGradient(0, cy, cx - 12, cy)
        left_grad.setColorAt(0.0, QColor(170, 155, 106, 0))
        left_grad.setColorAt(0.7, QColor(170, 155, 106, 120))
        left_grad.setColorAt(1.0, QColor(170, 155, 106, 200))
        
        pen_left = QPen(left_grad, 1.0)
        painter.setPen(pen_left)
        painter.drawLine(QPointF(10, cy), QPointF(cx - 12, cy))
        
        # 右侧渐变线
        right_grad = QLinearGradient(cx + 12, cy, rect.width(), cy)
        right_grad.setColorAt(0.0, QColor(170, 155, 106, 200))
        right_grad.setColorAt(0.3, QColor(170, 155, 106, 120))
        right_grad.setColorAt(1.0, QColor(170, 155, 106, 0))
        
        pen_right = QPen(right_grad, 1.0)
        painter.setPen(pen_right)
        painter.drawLine(QPointF(cx + 12, cy), QPointF(rect.width() - 10, cy))
        
        painter.end()


class UiSignals(QObject):
    status = pyqtSignal(str)
    result = pyqtSignal(dict)
    error = pyqtSignal(str)
    log = pyqtSignal(str)


class OverlayWindow(QMainWindow):
    """无边框、置顶覆盖层窗口（MVP）。"""

    capture_requested = pyqtSignal(bool)
    exception_capture_requested = pyqtSignal(bool)
    resources_initialized = pyqtSignal(object)
    resources_loaded = pyqtSignal()

    def __init__(self, config: AppConfig, config_path: Path) -> None:
        super().__init__()
        self.config = config
        self._config_path = config_path
        self.signals = UiSignals()
        self.log_path = Path(__file__).resolve().parents[3] / "log" / "gui.log"
        install_process_log_tee(self.log_path)
        self.matcher = None
        self.audio_resolver = None
        self.skill_param_resolver = None
        runtime_callbacks = create_runtime_callbacks(self)
        self.engine = create_overlay_ocr_engine(config, runtime_callbacks)
        self.db: Dict[str, Any] = {}
        self.voice_map: Dict[str, list[str]] = {}
        self.voice_event_index = None
        self.audio_index = None
        self.audio_runtime: OverlayAudioRuntime | None = None
        install_audio_player(self)
        install_hotkey_registrar(self)
        self.database_update_controller = DatabaseUpdateController(
            parent=self,
            config_provider=lambda: self.config,
            config_path=self._config_path,
            refresh_runtime=self._refresh_runtime_resources,
            log=self.signals.log.emit,
        )
        self._dragging = False
        self._drag_pos: QPoint | None = None
        self._resizing = False
        self._resize_edge = None  # 'left', 'right', 'top', 'bottom', 'topleft', 'topright', 'bottomleft', 'bottomright'
        self._resize_start_geometry = None
        self._resize_start_pos = None

        # UI 状态
        self.current_font_size = 13
        self.current_font_weight = "SemiBold"
        self.current_letter_spacing = 0
        self.current_line_spacing = 1.2
        self.current_font_en = config.font_en
        self.current_font_cn = config.font_cn

        # 1. 先初始化 UI
        self._setup_ui()
        install_audio_playback_controls(self)
        install_result_presentation(self)
        # 2. 连接所有信号（确保日志等能正常工作）
        self._connect_signals()
        # 3. 恢复配置（覆盖默认值，并调整窗口尺寸）
        self._restore_window_position()
        # 4. 最后应用样式和字体
        self._load_style()
        self._apply_font_settings()

        install_capture_session(self)
        connect_overlay_composition_signals(self)
        install_app_event_filter(self)

        threading.Thread(target=self._initialize_resources, daemon=True).start()
        self._hotkeys.start()
        install_sync_timer(self)

    def _setup_ui(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # 启用半透明背景，并给整个前端浮层一层轻微透明度，让覆盖窗口不再完全遮挡后方内容
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(0.92)
        self.setMouseTracking(True)  # 启用鼠标跟踪以便更新光标样式
        # 设置焦点策略以便接收焦点事件
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        container = QWidget(self)
        container.setObjectName("OverlayRoot") # 最外层金边
        self.setCentralWidget(container)
        container.setMouseTracking(True)

        # 中间装饰色带层
        stripe_frame = QWidget(container)
        stripe_frame.setObjectName("StripeFrame") # 这里显示灰白带
        stripe_frame.setMouseTracking(True)

        outer_layout = QVBoxLayout(container)
        outer_layout.setContentsMargins(7, 7, 7, 7) # 加大：让外框到色带之间的背景层显出来
        outer_layout.setSpacing(0)
        outer_layout.addWidget(stripe_frame)

        # 内部核心容器
        inner_frame = QWidget(stripe_frame)
        inner_frame.setObjectName("InnerFrame") # 这里显示核心背景和大圆角
        inner_frame.setMouseTracking(True)

        stripe_layout = QVBoxLayout(stripe_frame)
        stripe_layout.setContentsMargins(3, 3, 3, 3) # 保持：色带本身依然纤细
        stripe_layout.setSpacing(0)
        stripe_layout.addWidget(inner_frame)

        # 实际的内容布局都在 inner_frame 里
        layout = QVBoxLayout(inner_frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.title_label = QLabel("Ludiglot")
        self.title_label.setObjectName("Title")
        self.title_label.setFont(QFont("Segoe UI", 12))

        self.source_label = QTextEdit("等待捕获…")
        self.source_label.setObjectName("SourceText")
        self.source_label.setReadOnly(True)
        self.source_label.setAcceptRichText(True)  # 支持富文本
        self.source_label.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.source_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.source_label.setFrameStyle(QFrame.Shape.NoFrame)
        self.source_label.setMinimumHeight(60)
        self.source_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.cn_label = QTextEdit("")
        self.cn_label.setObjectName("AccentText")
        self.cn_label.setReadOnly(True)
        self.cn_label.setAcceptRichText(True)  # 显式启用富文本支持
        self.cn_label.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.cn_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cn_label.setFrameStyle(QFrame.Shape.NoFrame)
        self.cn_label.setMinimumHeight(80)
        self.cn_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("StatusText")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("LogBox")
        self.log_box.setMinimumHeight(90)


        layout.addWidget(self.title_label)

        # 直接添加文本组件，移除卡片容器化
        layout.addWidget(self.source_label)
        
        self.star_divider = StarDivider(self)
        layout.addWidget(self.star_divider)
        
        layout.addWidget(self.cn_label)
        
        # 音频控制栏（进度条 + 播放/暂停按钮 + 时间显示）
        audio_control_layout = QHBoxLayout()
        self.play_pause_btn = PlayPauseButton(self)
        self.play_pause_btn.setObjectName("AudioControl")
        self.play_pause_btn.setFixedSize(28, 28)
        self.play_pause_btn.setEnabled(False)
        self.play_pause_btn.clicked.connect(self._toggle_audio_playback)
        self._icon_play = "▶"
        self._icon_pause = "Ⅱ"
        
        self.audio_slider = AudioWaveformProgressBar(self)
        self.audio_slider.setObjectName("AudioWaveformSlider")
        self.audio_slider.setEnabled(False)
        self.audio_slider.sigSeekStarted.connect(self._on_audio_seek_started)
        self.audio_slider.sigSeekFinished.connect(self._on_audio_seek_finished)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("TimeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_label.setMinimumWidth(70)
        
        audio_control_layout.addWidget(self.play_pause_btn)
        audio_control_layout.addWidget(self.audio_slider, 1)
        audio_control_layout.addWidget(self.time_label)
        layout.addLayout(audio_control_layout)
        
        layout.addWidget(self.status_label)

        
        # 创建窗口右上角菜单按钮
        self._setup_window_menu()
    
    def _setup_window_menu(self) -> None:
        """创建窗口右上角下拉菜单 and 关闭按钮"""
        # 创建顶部控制栏小部件
        self.control_bar = QWidget(self)
        self.control_bar.setFixedHeight(45)
        
        # 使用水平布局来管理按钮
        layout = QHBoxLayout(self.control_bar)
        layout.setContentsMargins(0, 10, 12, 0)  # 边距
        layout.setSpacing(0)  # 按钮间距
        
        # 添加弹簧，将按钮推向右侧
        layout.addStretch()
        
        # 创建菜单按钮
        self.top_menu_btn = MenuIconButton(self.control_bar)
        self.top_menu_btn.setObjectName("TopMenuButton")
        layout.addWidget(self.top_menu_btn)
        
        # 创建关闭按钮
        self.top_close_btn = CloseIconButton(self.control_bar)
        self.top_close_btn.setObjectName("TopCloseButton")
        self.top_close_btn.clicked.connect(self.hide)
        layout.addWidget(self.top_close_btn)

        
        # 初始定位
        self._update_button_positions()
        
        # 创建下拉菜单
        self.window_menu = PersistentMenu(self)
        # self.top_menu_btn.setMenu(self.window_menu) # 禁用默认菜单，由 _show_window_menu 手动精确对齐
        
        # Update Database 操作
        update_action = QAction("Update Database", self)
        update_action.setToolTip("从游戏 Pak 重新解包并构建数据库")
        update_action.triggered.connect(self._update_database)
        self.window_menu.addAction(update_action)
        
        self.window_menu.addSeparator()

        # OCR 设置子菜单
        ocr_settings_menu = PersistentMenu("OCR Settings", self.window_menu)
        ocr_settings_menu.setStyleSheet(self.window_menu.styleSheet())
        self.window_menu.addMenu(ocr_settings_menu)

        # OCR 后端选择
        ocr_backend_menu = PersistentMenu("Backend", ocr_settings_menu)
        ocr_backend_menu.setStyleSheet(self.window_menu.styleSheet())
        ocr_settings_menu.addMenu(ocr_backend_menu)

        self.ocr_backend_group = QActionGroup(self)
        self.ocr_backend_group.setExclusive(True)
        for backend in ["auto", "windows", "paddle_vl"]:
            display_name = {
                "auto": "Auto (Prefer WinOCR)",
                "windows": "Windows OCR",
                "paddle_vl": "PaddleOCR-VL",
            }.get(backend, backend)
            action = QAction(display_name, self)
            action.setCheckable(True)
            action.setChecked(backend == self.config.ocr_backend)
            action.setProperty("persistent", True)
            action.triggered.connect(lambda checked, b=backend: self._on_backend_changed(b))
            self.ocr_backend_group.addAction(action)
            ocr_backend_menu.addAction(action)

        # OCR 模式选择
        ocr_mode_menu = PersistentMenu("Mode", ocr_settings_menu)
        ocr_mode_menu.setStyleSheet(self.window_menu.styleSheet())
        ocr_settings_menu.addMenu(ocr_mode_menu)

        self.ocr_mode_group = QActionGroup(self)
        self.ocr_mode_group.setExclusive(True)
        for mode in ["auto", "gpu", "cpu"]:
            display_name = {
                "auto": "Auto",
                "gpu": "GPU",
                "cpu": "CPU"
            }.get(mode, mode)
            action = QAction(display_name, self)
            action.setCheckable(True)
            action.setChecked(mode == self.config.ocr_mode)
            action.setProperty("persistent", True)
            action.triggered.connect(lambda checked, m=mode: self._on_mode_changed(m))
            self.ocr_mode_group.addAction(action)
            ocr_mode_menu.addAction(action)

        self.window_menu.addSeparator()
        
        # 字体设置子菜单
        font_settings_menu = PersistentMenu("Font Settings", self.window_menu)
        font_settings_menu.setStyleSheet(self.window_menu.styleSheet())
        self.window_menu.addMenu(font_settings_menu)
        
        # 字号调节器
        font_size_menu = PersistentMenu("Size", font_settings_menu)
        font_size_menu.setStyleSheet(self.window_menu.styleSheet())
        font_settings_menu.addMenu(font_size_menu)
        
        # 共享调节器样式
        spinner_qss = """
            QAbstractSpinBox { 
                background: rgba(15, 18, 22, 255); 
                color: #f0f4f8; 
                border: 1px solid rgba(170, 155, 106, 90); 
                border-radius: 4px; 
                min-height: 22px;
                max-height: 22px;
                padding: 0px 0px 0px 0px; 
                font-family: "Segoe UI", "Source Han Serif SC", sans-serif;
                font-size: 11px;
                selection-background-color: rgba(170, 155, 106, 80);
            }
            QAbstractSpinBox:hover {
                border: 1px solid rgba(170, 155, 106, 200);
            }
            QAbstractSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 18px;
                height: 11px; /* 恢复固定像素，与 down-button 共 22px 对齐，无缝填满 */
                border-left: 1px solid rgba(170, 155, 106, 90);
                background: rgba(170, 155, 106, 20);
                border-top-right-radius: 3px;
            }
            QAbstractSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 18px;
                height: 11px; /* 11+11=22px，与控件总高完全一致，无像素缝隙 */
                border-left: 1px solid rgba(170, 155, 106, 90);
                /* 不加 border-top，避免叠加分割线导致视觉缝隙 */
                background: rgba(170, 155, 106, 20);
                border-bottom-right-radius: 3px;
            }
            QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { 
                background: rgba(170, 155, 106, 60); 
            }
            QAbstractSpinBox::up-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QAbstractSpinBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
        """

        # 字号调节器
        size_action = QWidgetAction(self)
        size_widget = QWidget()
        size_layout = QHBoxLayout(size_widget)
        size_layout.setContentsMargins(10, 2, 10, 2)
        size_layout.setSpacing(10)
        size_label = QLabel("Size")
        size_label.setStyleSheet("color: white; font-weight: normal;")
        self.size_spin = GoldSpinBox()
        self.size_spin.setRange(8, 72)
        self.size_spin.setFixedWidth(65)
        self.size_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.size_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 严格禁用自动获取焦点
        self.size_spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        size_line_edit = self.size_spin.lineEdit()
        if size_line_edit:
            size_line_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            size_line_edit.setReadOnly(False)
            # 点击回车后确认并清除焦点（隐藏游标）
            size_line_edit.returnPressed.connect(size_line_edit.clearFocus)
            size_line_edit.editingFinished.connect(size_line_edit.clearFocus)
        
        # 强制设置标准的 LTR 布局方向，完全隔离并彻底解决父级菜单 RightToLeft 继承导致的按键/间距镜像错乱冲突
        size_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.size_spin.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        
        self.size_spin.setStyleSheet(spinner_qss)
        self.size_spin.setValue(self.current_font_size)
        self.size_spin.valueChanged.connect(self._adjust_font_size_direct)
        size_layout.addWidget(size_label)
        size_layout.addStretch()
        size_layout.addWidget(self.size_spin)
        size_action.setDefaultWidget(size_widget)
        if font_size_menu:
            font_size_menu.addAction(size_action)
        
        font_weight_menu = PersistentMenu("Weight", font_settings_menu)
        font_weight_menu.setStyleSheet(self.window_menu.styleSheet())
        font_settings_menu.addMenu(font_weight_menu)
        
        if font_weight_menu:

            self.font_weight_group = QActionGroup(self)
            self.font_weight_group.setExclusive(True)
        
            for weight in ["Light", "Normal", "SemiBold", "Bold", "Heavy"]:
                action = QAction(weight, self)
                action.setCheckable(True)
                action.setChecked(weight == self.current_font_weight)
                action.setProperty("persistent", True)
                action.triggered.connect(lambda checked, w=weight: self._adjust_font_weight(w))
                self.font_weight_group.addAction(action)
                font_weight_menu.addAction(action)
        
        # 字距调节器
        letter_spacing_menu = PersistentMenu("Letter Spacing", font_settings_menu)
        letter_spacing_menu.setStyleSheet(self.window_menu.styleSheet())
        font_settings_menu.addMenu(letter_spacing_menu)
        
        spacing_action = QWidgetAction(self)
        spacing_widget = QWidget()
        spacing_layout = QHBoxLayout(spacing_widget)
        spacing_layout.setContentsMargins(10, 2, 10, 2)
        spacing_layout.setSpacing(10)
        spacing_label = QLabel("Spacing")
        spacing_label.setStyleSheet("color: white; font-weight: normal;")
        self.spacing_spin = GoldDoubleSpinBox()
        self.spacing_spin.setRange(-10, 50)
        self.spacing_spin.setSingleStep(0.5)
        self.spacing_spin.setFixedWidth(65)
        self.spacing_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spacing_spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        spacing_line_edit = self.spacing_spin.lineEdit()
        if spacing_line_edit:
            spacing_line_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            spacing_line_edit.setReadOnly(False)
            # 点击回车后确认并清除焦点（隐藏游标）
            spacing_line_edit.returnPressed.connect(spacing_line_edit.clearFocus)
            spacing_line_edit.editingFinished.connect(spacing_line_edit.clearFocus)
        
        # 强制设置标准的 LTR 布局方向，完全隔离并彻底解决父级菜单 RightToLeft 继承导致的按键/间距镜像错乱冲突
        spacing_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.spacing_spin.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        
        self.spacing_spin.setStyleSheet(spinner_qss)
        self.spacing_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.spacing_spin.setValue(self.current_letter_spacing)
        self.spacing_spin.valueChanged.connect(self._adjust_letter_spacing_direct)
        spacing_layout.addWidget(spacing_label)
        spacing_layout.addStretch()
        spacing_layout.addWidget(self.spacing_spin)
        spacing_action.setDefaultWidget(spacing_widget)
        if letter_spacing_menu:
            letter_spacing_menu.addAction(spacing_action)
        
        # 行距调节器
        line_spacing_menu = PersistentMenu("Line Spacing", font_settings_menu)
        line_spacing_menu.setStyleSheet(self.window_menu.styleSheet())
        font_settings_menu.addMenu(line_spacing_menu)
        
        lh_action = QWidgetAction(self)
        lh_widget = QWidget()
        lh_layout = QHBoxLayout(lh_widget)
        lh_layout.setContentsMargins(10, 2, 10, 2)
        lh_layout.setSpacing(10)
        lh_label = QLabel("Line Spacing")
        lh_label.setStyleSheet("color: white; font-weight: normal;")
        self.lh_spin = GoldDoubleSpinBox()
        self.lh_spin.setRange(0.5, 5.0)
        self.lh_spin.setSingleStep(0.1)
        self.lh_spin.setFixedWidth(65)
        self.lh_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lh_spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        lh_line_edit = self.lh_spin.lineEdit()
        if lh_line_edit:
            lh_line_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            lh_line_edit.setReadOnly(False)
            # 点击回车后确认并清除焦点（隐藏游标）
            lh_line_edit.returnPressed.connect(lh_line_edit.clearFocus)
            lh_line_edit.editingFinished.connect(lh_line_edit.clearFocus)
        
        # 强制设置标准的 LTR 布局方向，完全隔离并彻底解决父级菜单 RightToLeft 继承导致的按键/间距镜像错乱冲突
        lh_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.lh_spin.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        
        self.lh_spin.setStyleSheet(spinner_qss)
        self.lh_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.lh_spin.setValue(self.current_line_spacing)
        self.lh_spin.valueChanged.connect(self._adjust_line_spacing_direct)
        lh_layout.addWidget(lh_label)
        lh_layout.addStretch()
        lh_layout.addWidget(self.lh_spin)
        lh_action.setDefaultWidget(lh_widget)
        if line_spacing_menu:
            line_spacing_menu.addAction(lh_action)

        # 字体选择菜单
        font_family_menu = PersistentMenu("Font Family", font_settings_menu)
        font_family_menu.setStyleSheet(self.window_menu.styleSheet())
        if font_settings_menu:
            font_settings_menu.addMenu(font_family_menu)
            
            # 英文字体
            en_font_menu = PersistentMenu("English", font_family_menu)
            en_font_menu.setStyleSheet(self.window_menu.styleSheet())
            font_family_menu.addMenu(en_font_menu)
            self._setup_font_selection_menu(en_font_menu, "en")
            
            # 中文字体
            cn_font_menu = PersistentMenu("Chinese", font_family_menu)
            cn_font_menu.setStyleSheet(self.window_menu.styleSheet())
            font_family_menu.addMenu(cn_font_menu)
            self._setup_font_selection_menu(cn_font_menu, "cn")

        self.window_menu.addSeparator()
        
        # 菜单展开方向 - 使用 PersistentMenu 保证无缝去边框和精确的 2px 鸣潮圆角
        direction_menu = PersistentMenu("Menu Direction", self.window_menu)
        direction_menu.setStyleSheet(self.window_menu.styleSheet())
        self.window_menu.addMenu(direction_menu)
        if direction_menu:
            left_action = QAction("← Left", self)
            left_action.triggered.connect(lambda: self._set_menu_direction("left"))
            direction_menu.addAction(left_action)
            right_action = QAction("Right →", self)
            right_action.triggered.connect(lambda: self._set_menu_direction("right"))
            direction_menu.addAction(right_action)
        
        self.top_menu_btn.clicked.connect(self._show_window_menu)
        
        # 初始化菜单样式（默认左展开）
        self._initialize_menu_style()

    def _show_window_menu(self):
        """显示窗口菜单，实现右边缘对齐"""
        # 核心：必须先获取 sizeHint，因为 exec() 之前 width() 可能还是旧值
        self.window_menu.ensurePolished()
        self.window_menu.adjustSize()
        menu_w = self.window_menu.sizeHint().width()
        
        # 获取按钮右边缘的全局 X 坐标
        btn_topleft = self.top_menu_btn.mapToGlobal(QPoint(0, 0))
        btn_right_x = btn_topleft.x() + self.top_menu_btn.width()
        btn_bottom_y = btn_topleft.y() + self.top_menu_btn.height()
        
        direction = getattr(self, "_menu_direction", "right")
        
        if direction == "left":
            # 强制右边缘对齐：菜单左 X = 按钮右 X - 菜单宽度 - 2 (视觉缝隙补偿)
            target_x = btn_right_x - menu_w - 2
            self.window_menu.exec(QPoint(target_x, btn_bottom_y))
        else:
            # 向右展开：左边缘对齐 + 2 (默认行为)
            self.window_menu.exec(QPoint(btn_topleft.x() + 2, btn_bottom_y))
    
    def _initialize_menu_style(self):
        """初始化菜单样式，确保所有用户都能看到正确的样式"""
        direction = getattr(self, "_menu_direction", "right")
        self._set_menu_direction(direction)

    def _set_menu_direction(self, direction: str):
        """设置菜单展开方向"""
        self._menu_direction = direction
        layout_dir = Qt.LayoutDirection.RightToLeft if direction == "left" else Qt.LayoutDirection.LeftToRight
        
        # 根据方向更新菜单样式（统一左侧高亮条）
        item_align = "right" if direction == "left" else "left"
        arrow_svg = (
            "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'><path fill='transparent' d='M0 0 H10 V10 H0 Z'/></svg>"
            if direction == "left" else
            "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8' viewBox='0 0 10 10'><path fill='%23aa9b6a' d='M3 1 L7 5 L3 9 Z'/></svg>"
        )
        
        menu_style = f"""
            QMenu {{
                background-color: #0f1216;
                color: #dcdcdc;
                font-family: "Segoe UI", "Source Han Serif SC", sans-serif;
                font-size: 12px;
                border: 1px solid #494535; /* 实心不透明金边，杜绝透明窗口下文字透底 */
                border-radius: 2px;
                padding: 0px; /* 彻底移除上下左右 padding，使首尾条目高亮完美触达菜单边界 */
                margin: 0px; /* 强制外边距为0，彻底消除一切空洞的黑底缝隙 */
            }}
            QMenu::item {{
                padding: 5px 10px;
                margin: 0px;
                border-radius: 0px;
                text-align: {item_align};
                border-left: 3px solid #5a5035; /* 常态：暗金色实心，完全不透明 */
            }}
            QMenu::item:checked {{
                background-color: rgba(170, 155, 106, 35);
                color: #ffffff;
                border-left: 3px solid #c9a651; /* 选中态：提亮饱和金色，与同色系高亮背景形成对比 */
            }}
            QMenu::item:selected {{
                background-color: rgba(170, 155, 106, 25);
                color: #ffffff;
                border-left: 3px solid #c9a651; /* 悬浮态：同上，明亮金色杠明确可见 */
            }}
            QMenu::item:disabled {{
                color: rgba(255, 255, 255, 40);
                background-color: transparent;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: rgba(170, 155, 106, 40);
                margin: 0px 8px; /* 彻底移除上下 margin，使选项高亮与分隔线之间零缝隙完美贴合 */
            }}
            QMenu::right-arrow {{
                width: 8px;
                height: 8px;
                image: url("{arrow_svg}");
            }}
            QMenu::left-arrow {{
                width: 8px;
                height: 8px;
                image: url("{arrow_svg}");
            }}
            QMenu::indicator {{
                width: 0px;
                height: 0px;
                image: none;
            }}
            QLabel {{
                color: #dcdcdc;
                font-family: "Segoe UI", "Source Han Serif SC", sans-serif;
                font-size: 11px;
            }}
        """
        
        # 递归设置所有菜单的方向和样式
        menus = [self.window_menu]
        while menus:
            m = menus.pop(0)
            m.setLayoutDirection(layout_dir)
            m.setStyleSheet(menu_style)
            for action in m.actions():
                if action.menu():
                    menus.append(action.menu())
        self.signals.log.emit(f"[UI] 菜单方向设置为: {direction}")
        self._persist_window_position()

    def _adjust_font_size_direct(self, value: int):
        """直接通过 SpinBox 调节字号"""
        self.current_font_size = value
        self._apply_font_settings()
        self.signals.log.emit(f"[UI] 字号设置为: {value}pt")
        self._persist_window_position()

    def _adjust_letter_spacing_direct(self, value: float):
        """直接调节字距"""
        self.current_letter_spacing = value
        self._apply_font_settings()
        self.signals.log.emit(f"[UI] 字距设置为: {value}px")
        self._persist_window_position()

    def _adjust_line_spacing_direct(self, value: float):
        """直接调节行距"""
        self.current_line_spacing = value
        self._apply_font_settings()
        self.signals.log.emit(f"[UI] 行距设置为: {value:.1f}x")
        self._persist_window_position()

    def _update_button_positions(self):
        """更新按钮位置"""
        if hasattr(self, 'control_bar'):
            # 基于内框（inner_frame）计算位置：
            # 外部边距是 7 (outer_layout) + 3 (stripe_layout) = 10 px
            # 顶部与内框顶部对齐，即 y = 10 px
            # 宽度为整个内框宽度，即 self.width() - 20
            margin = 10
            self.control_bar.setGeometry(margin, margin, self.width() - 2 * margin, 45)

    def _get_available_fonts(self) -> list[str]:
        """获取系统和data/fonts目录下的可用字体"""
        from PyQt6.QtGui import QFontDatabase
        
        fonts = []
        
        # 添加常用字体（系统自带）
        default_fonts = [
            "Source Han Serif SC, 思源宋体, serif",
            "Microsoft YaHei, 微软雅黑, sans-serif",
            "SimSun, 宋体, serif",
            "SimHei, 黑体, sans-serif",
            "KaiTi, 楷体, serif",
            "Arial, sans-serif",
            "Times New Roman, serif",
            "Courier New, monospace",
        ]
        fonts.extend(default_fonts)
        
        # 扫描 Fonts 目录
        font_dir = self.config.fonts_root
        if font_dir.exists():
            for font_file in font_dir.glob("*.[tT][tT][fF]"):
                try:
                    font_id = QFontDatabase.addApplicationFont(str(font_file))
                    if font_id != -1:
                        families = QFontDatabase.applicationFontFamilies(font_id)
                        for family in families:
                            fonts.append(family)
                            self.signals.log.emit(f"[FONT] Loaded: {family} from {font_file.name}")
                except Exception as e:
                    self.signals.log.emit(f"[FONT] Failed to load {font_file.name}: {e}")
            
            for font_file in font_dir.glob("*.[oO][tT][fF]"):
                try:
                    font_id = QFontDatabase.addApplicationFont(str(font_file))
                    if font_id != -1:
                        families = QFontDatabase.applicationFontFamilies(font_id)
                        for family in families:
                            fonts.append(family)
                            self.signals.log.emit(f"[FONT] Loaded: {family} from {font_file.name}")
                except Exception as e:
                    self.signals.log.emit(f"[FONT] Failed to load {font_file.name}: {e}")
        
        return fonts

    def _setup_font_selection_menu(self, menu: QMenu, lang: str) -> None:
        """设置字体选择菜单"""
        fonts = self._get_available_fonts()
        current_font = self.current_font_en if lang == "en" else self.current_font_cn
        
        font_group = QActionGroup(self)
        font_group.setExclusive(True)
        
        for font in fonts:
            action = QAction(font, self)
            action.setCheckable(True)
            action.setChecked(font == current_font)
            action.setProperty("persistent", True)
            action.triggered.connect(lambda checked, f=font, l=lang: self._change_font(f, l))
            font_group.addAction(action)
            menu.addAction(action)

    def _change_font(self, font: str, lang: str) -> None:
        """切换字体"""
        if lang == "en":
            self.current_font_en = font
            self.signals.log.emit(f"[UI] English font: {font}")
        else:
            self.current_font_cn = font
            self.signals.log.emit(f"[UI] Chinese font: {font}")
        
        self._apply_font_settings()
        self._persist_window_position()

    def _font_weight_css(self) -> str:
        weight_map = {
            "Light": "300",
            "Normal": "400",
            "SemiBold": "600",
            "Bold": "700",
            "Heavy": "900",
        }
        return weight_map.get(self.current_font_weight, "600")
    
    def _apply_font_settings(self):
        """应用字体设置到所有UI元素"""
        self.result_presentation.refresh_font_settings()

        self.signals.log.emit(
            f"[UI] 字体设置：{self.current_font_size}pt, {self.current_font_weight}, "
            f"字距{self.current_letter_spacing}px, 行距{self.current_line_spacing:.1f}x"
        )

    def _adjust_font_weight(self, weight: str):
        """调整字体粗细"""
        self.current_font_weight = weight
        # 更新菜单项的勾选状态
        for action in self.window_menu.findChildren(QAction):
            if action.text() in ["Light", "Normal", "SemiBold", "Bold", "Heavy"]:
                action.setChecked(action.text() == weight)
        self._apply_font_settings()
        self._persist_window_position()
    
    def _update_database(self) -> None:
        """更新数据库：从游戏 Pak 重新解包并构建"""
        self.database_update_controller.start()

    def _load_style(self) -> None:
        try:
            style_path = Path(__file__).resolve().parent / "style.qss"
            if style_path.exists():
                qss = style_path.read_text(encoding="utf-8")
                self.setStyleSheet(qss)
                self.signals.log.emit(f"[UI] QSS 样式加载成功: {style_path}")
            else:
                self.signals.log.emit(f"[UI] QSS 样式文件不存在: {style_path}")
        except Exception as e:
            self.signals.log.emit(f"[UI] QSS 样式加载异常: {e}")

    def _connect_signals(self) -> None:
        self.signals.status.connect(self.status_label.setText)
        self.signals.error.connect(self._show_error)
        self.signals.result.connect(self._show_result)
        self.signals.log.connect(self._append_log)

    def _initialize_resources(self) -> None:
        callbacks = create_runtime_callbacks(self)
        result = initialize_overlay_runtime(self.config, self.engine, callbacks)
        if not result.success or result.resources is None:
            return

        self.resources_initialized.emit(result.resources)

    def _refresh_runtime_resources(self) -> bool:
        callbacks = create_runtime_callbacks(self)
        result = initialize_overlay_runtime(self.config, self.engine, callbacks)
        if not result.success or result.resources is None:
            return False

        self._on_runtime_resources_initialized(result.resources)
        return True

    def _on_runtime_resources_initialized(self, resources) -> None:
        self._apply_runtime_resources(resources)
        self.signals.status.emit("就绪")
        self.resources_loaded.emit()

    def _apply_runtime_resources(self, resources) -> None:
        self.db = resources.db
        self.matcher = resources.matcher
        self.audio_resolver = resources.audio_resolver
        self.skill_param_resolver = resources.skill_param_resolver
        self.voice_map = resources.voice_map
        self.voice_event_index = resources.voice_event_index
        self.audio_index = resources.audio_index
        self._external_wem_root = resources.external_wem_root
        self.audio_runtime = OverlayAudioRuntime(
            self.config,
            self.audio_resolver,
            self.audio_index,
            self.signals.log.emit,
        )

    def _toggle_visibility(self) -> None:
        """切换窗口显示/隐藏状态，使用isHidden()避免状态不同步问题。"""
        if self.isHidden():
            self.show_and_activate()
        else:
            self.hide()
    
    def _toggle_audio_playback(self) -> None:
        """切换音频播放/暂停。"""
        self.audio_ui.toggle()

    def _on_audio_seek_started(self) -> None:
        """波形拖动开始。"""
        self.audio_ui.seek_started()

    def _on_audio_seek_finished(self, position: float) -> None:
        """波形释放时跳转到新位置。"""
        self.audio_ui.seek_finished(position)

    def _update_audio_progress(self) -> None:
        """定时更新音频进度条和时间标签。"""
        self.audio_ui.update_progress()

    def _translate_title(self, title: str) -> str:
        """标题翻译委托给核心匹配器，UI 层只负责调用。"""
        if not self.matcher:
            return ""
        try:
            return self.matcher.resolve_title_cn(title)
        except Exception:
            return ""

    def _on_mode_changed(self, mode: str) -> None:
        self.engine.set_mode(mode)
        self.config.ocr_mode = mode
        self.signals.status.emit(f"OCR 模式: {mode}")
        self._persist_window_position()

    def _on_backend_changed(self, backend: str) -> None:
        self.config.ocr_backend = backend
        self.signals.status.emit(f"OCR 后端: {backend}")
        self._persist_window_position()

    def trigger_capture(self) -> None:
        self.capture_requested.emit(True)

    def _set_audio_index(self, audio_index) -> None:
        self.audio_index = audio_index

    def _clear_capture_audio_state(self) -> None:
        self.audio_ui.clear_candidate()

    def _display_preferences(self) -> DisplayPreferences:
        return DisplayPreferences(
            gender_preference=getattr(self.config, "gender_preference", "female"),
            font_en=self.current_font_en,
            font_cn=self.current_font_cn,
            font_size=int(self.current_font_size) if self.current_font_size else 13,
            font_weight_css=self._font_weight_css(),
            line_spacing=float(self.current_line_spacing) if self.current_line_spacing else 1.2,
            letter_spacing=float(self.current_letter_spacing) if self.current_letter_spacing else 0.0,
        )

    def _show_result(self, result: Dict[str, Any]) -> None:
        self.result_presentation.present_result(result)

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)
        self._append_log(f"[ERROR] {message}")

    def _append_log(self, message: str) -> None:
        # 确保单次 sys.stdout.write 减少多线程交错导致的信息粘连
        msg_with_newline = message if message.endswith("\n") else message + "\n"
        try:
            sys.stdout.write(msg_with_newline)
            sys.stdout.flush()
        except Exception:
            # Best-effort console output; ignore stdout write/flush errors
            pass

        self.log_box.append(message)

    def show_and_activate(self) -> None:
        """显示并激活窗口，确保焦点。"""
        self.show()
        self.raise_()
        self.activateWindow()
        # 确保获得焦点以便检测焦点丢失
        self.setFocus()
        # 强制窗口置顶并获得键盘焦点
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)

    def show_without_activating(self) -> None:
        """Show updated translations without taking keyboard focus from the game."""
        self.show()
        self.raise_()
        QApplication.processEvents()  # 立即处理事件

    def stop_audio(self, emit_status: bool = True) -> None:
        self.audio_ui.stop(emit_status=emit_status)

    def _play_audio_for_key(self, text_key: str) -> None:
        self.audio_ui.play_for_key(text_key)

    def play_audio(self) -> None:
        self.audio_ui.play_current()



    def closeEvent(self, event) -> None:
        """关闭前保存所有状态"""
        if hasattr(self, "capture_session"):
            self.capture_session.stop_continuous()
        try:
            self._persist_window_position()
        except:
            pass
        if hasattr(self, "_hotkeys"):
            self._hotkeys.stop()
        super().closeEvent(event)
    
    def hideEvent(self, event) -> None:
        """窗口隐藏事件，记录日志以便调试快捷键问题。"""
        self.signals.log.emit("[WINDOW] 隐藏")
        super().hideEvent(event)
    
    def showEvent(self, event) -> None:
        """窗口显示事件。"""
        self.signals.log.emit("[WINDOW] 显示")
        super().showEvent(event)
        # 确保窗口获得焦点，以便接收focusOutEvent
        self.setFocus()

    def eventFilter(self, obj, event) -> bool:
        """全局事件过滤器：检测窗口失焦+鼠标点击窗口外 → 隐藏窗口"""
        if getattr(self, "disable_auto_hide", False):
            return super().eventFilter(obj, event)
            
        if self.isVisible():
            # 方案1: 检测失焦事件（窗口失去活动状态）
            if event.type() == QEvent.Type.WindowDeactivate:
                # 检查是否是菜单或子菜单导致的失焦
                if hasattr(self, 'window_menu'):
                    # 递归检查主菜单及其所有子菜单
                    menus_to_check = [self.window_menu]
                    while menus_to_check:
                        current_menu = menus_to_check.pop(0)
                        if current_menu.isVisible():
                            return False
                        for action in current_menu.actions():
                            if action.menu():
                                menus_to_check.append(action.menu())
                self.hide()
                return False
            # 方案2: 鼠标点击时检查是否在窗口外（双重保险）
            if event.type() == QEvent.Type.MouseButtonPress:
                try:
                    if hasattr(event, "globalPosition"):
                        pos = event.globalPosition().toPoint()
                    else:
                        pos = QCursor.pos()
                    
                    # 检查是否点击在菜单或其关联子菜单内
                    if hasattr(self, 'window_menu'):
                        menus_to_check = [self.window_menu]
                        while menus_to_check:
                            current_menu = menus_to_check.pop(0)
                            if current_menu.isVisible() and current_menu.frameGeometry().contains(pos):
                                return False
                            for action in current_menu.actions():
                                if action.menu():
                                    menus_to_check.append(action.menu())
                    
                    # 只有点击在窗口外且不在菜单内才隐藏
                    if not self.frameGeometry().contains(pos):
                        self.hide()
                except Exception:
                    pass
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            
            # 解决粘连问题：如果点击在按钮等控件上，不触发窗口拖拽或缩放
            child = self.childAt(pos)
            # 如果点击的是按钮（包括其内部的图标 Label）或下拉框，则直接返回
            if isinstance(child, (QPushButton, QComboBox, QLabel)) and child.parent() in {self.control_bar, self.centralWidget()}:
                # 特殊处理：如果是标题或状态栏等非交互 Label，允许拖拽
                if child not in {self.title_label, self.status_label}:
                    super().mousePressEvent(event)
                    return

            edge = self._get_resize_edge(pos)
            if edge:
                # 开始调整大小
                self._resizing = True
                self._resize_edge = edge
                self._resize_start_geometry = self.geometry()
                self._resize_start_pos = event.globalPosition().toPoint()
            else:
                # 开始拖拽窗口
                if child is None or child in {self.centralWidget(), self.title_label, self.status_label, self.control_bar}:
                    self._dragging = True
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        
        if self._resizing and self._resize_edge and self._resize_start_geometry:
            # 调整窗口大小
            current_pos = event.globalPosition().toPoint()
            delta_x = current_pos.x() - self._resize_start_pos.x()
            delta_y = current_pos.y() - self._resize_start_pos.y()
            
            geo = QRect(self._resize_start_geometry)
            min_width, min_height = 300, 200  # 最小窗口尺寸
            
            # 根据边缘类型调整几何
            if 'left' in self._resize_edge:
                new_width = max(geo.width() - delta_x, min_width)
                geo.setLeft(geo.right() - new_width)
            if 'right' in self._resize_edge:
                new_width = max(geo.width() + delta_x, min_width)
                geo.setWidth(new_width)
            if 'top' in self._resize_edge:
                new_height = max(geo.height() - delta_y, min_height)
                geo.setTop(geo.bottom() - new_height)
            if 'bottom' in self._resize_edge:
                new_height = max(geo.height() + delta_y, min_height)
                geo.setHeight(new_height)
            
            self.setGeometry(geo)
        elif self._dragging and self._drag_pos is not None:
            # 拖拽窗口
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        else:
            # 鼠标悬停时更新光标样式（未按下时）
            edge = self._get_resize_edge(pos)
            self._update_cursor_for_edge(edge)
        
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                self._drag_pos = None
                self._persist_window_position()
            elif self._resizing:
                self._resizing = False
                self._resize_edge = None
                self._resize_start_geometry = None
                self._resize_start_pos = None
                self._persist_window_position()
            self._persist_window_position()
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        """窗口大小改变事件：更新按钮位置"""
        super().resizeEvent(event)
        self._update_button_positions()

    def _restore_window_position(self) -> None:
        try:
            defaults = OverlayPreferences(
                window_pos=WindowPoint(*self.config.window_pos) if self.config.window_pos else None,
                font_size=self.current_font_size,
                font_weight=self.current_font_weight,
                letter_spacing=self.current_letter_spacing,
                line_spacing=self.current_line_spacing,
                menu_direction=getattr(self, "_menu_direction", "right"),
                font_en=self.current_font_en,
                font_cn=self.current_font_cn,
                ocr_backend=getattr(self.config, "ocr_backend", "auto"),
                ocr_mode=getattr(self.config, "ocr_mode", "auto"),
            )
            prefs = ConfigJsonStore(self._config_path).load_overlay_preferences(defaults)

            if prefs.window_size:
                self.resize(prefs.window_size.width, prefs.window_size.height)
                self.signals.log.emit(f"[RECOVERY] 恢复窗口尺寸: {prefs.window_size.width}x{prefs.window_size.height}")

            self.current_font_size = prefs.font_size
            self.current_font_weight = prefs.font_weight
            self.current_letter_spacing = prefs.letter_spacing
            self.current_line_spacing = prefs.line_spacing
            self.current_font_en = prefs.font_en or self.current_font_en
            self.current_font_cn = prefs.font_cn or self.current_font_cn
            self._menu_direction = prefs.menu_direction

            if hasattr(self, 'size_spin'):
                self.size_spin.blockSignals(True)
                self.size_spin.setValue(self.current_font_size)
                self.size_spin.blockSignals(False)
            if hasattr(self, 'spacing_spin'):
                self.spacing_spin.blockSignals(True)
                self.spacing_spin.setValue(self.current_letter_spacing)
                self.spacing_spin.blockSignals(False)
            if hasattr(self, 'lh_spin'):
                self.lh_spin.blockSignals(True)
                self.lh_spin.setValue(self.current_line_spacing)
                self.lh_spin.blockSignals(False)

            QTimer.singleShot(150, lambda d=self._menu_direction: self._set_menu_direction(d))
            self.signals.log.emit(
                f"[RECOVERY] 恢复 UI 设置: {self.current_font_size}pt, 字距{self.current_letter_spacing}px, 行距{self.current_line_spacing}x"
            )
        except Exception as e:
            self.signals.log.emit(f"[RECOVERY] 恢复配置失败: {e}")
            return

        if prefs.window_pos:
            restored = clamp_window_position(
                prefs.window_pos,
                WindowSize(self.width(), self.height()),
                self._available_window_bounds(),
            )
            self.move(restored.x, restored.y)
            self.signals.log.emit(f"[RECOVERY] 恢复窗口位置: {restored.x}, {restored.y}")

        self._apply_font_settings()

    def _available_window_bounds(self) -> list[WindowBounds]:
        bounds: list[WindowBounds] = []
        for screen in QGuiApplication.screens():
            geo = screen.availableGeometry()
            bounds.append(
                WindowBounds(
                    left=int(geo.left()),
                    top=int(geo.top()),
                    width=int(geo.width()),
                    height=int(geo.height()),
                )
            )
        return bounds

    def _get_resize_edge(self, pos: QPoint) -> str | None:
        """检测鼠标位置是否在窗口边缘，返回边缘类型。"""
        margin = 8  # 边缘检测区域宽度（像素）
        rect = self.rect()
        x, y = pos.x(), pos.y()
        
        at_left = x <= margin
        at_right = x >= rect.width() - margin
        at_top = y <= margin
        at_bottom = y >= rect.height() - margin
        
        # 角落优先
        if at_top and at_left:
            return 'topleft'
        elif at_top and at_right:
            return 'topright'
        elif at_bottom and at_left:
            return 'bottomleft'
        elif at_bottom and at_right:
            return 'bottomright'
        # 边缘
        elif at_left:
            return 'left'
        elif at_right:
            return 'right'
        elif at_top:
            return 'top'
        elif at_bottom:
            return 'bottom'
        
        return None
    
    def _update_cursor_for_edge(self, edge: str | None) -> None:
        """根据边缘类型更新鼠标光标。"""
        if edge == 'left' or edge == 'right':
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edge == 'top' or edge == 'bottom':
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif edge == 'topleft' or edge == 'bottomright':
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edge == 'topright' or edge == 'bottomleft':
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _persist_window_position(self) -> None:
        try:
            if not self.isVisible():
                return

            pos = self.pos()
            size = self.size()
            ConfigJsonStore(self._config_path).save_overlay_preferences(
                OverlayPreferences(
                    window_pos=WindowPoint(int(pos.x()), int(pos.y())),
                    window_size=WindowSize(int(size.width()), int(size.height())),
                    font_size=self.current_font_size,
                    font_weight=self.current_font_weight,
                    letter_spacing=self.current_letter_spacing,
                    line_spacing=self.current_line_spacing,
                    menu_direction=getattr(self, "_menu_direction", "right"),
                    font_en=self.current_font_en,
                    font_cn=self.current_font_cn,
                    ocr_backend=getattr(self.config, "ocr_backend", "auto"),
                    ocr_mode=getattr(self.config, "ocr_mode", "auto"),
                )
            )
        except Exception:
            pass

    def reset_window_position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.left() + max((geo.width() - self.width()) // 2, 0)
        y = geo.top() + max((geo.height() - self.height()) // 2, 0)
        self.move(x, y)
        self._persist_window_position()
