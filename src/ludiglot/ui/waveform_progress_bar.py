"""自定义音频波形进度条组件。

针对用户细化需求进行的终极视觉重构：
  A. 两端音频爆发尖锐非对称：
     - 使用尖锐的指数绝对值函数（Cusp Peaks）产生极具数码质感的尖锐波峰；
     - 最外侧波峰（Peak 1）采用半波设计，即在 x=0 和 x=w 处达到最高点，向内侧单向衰减；
     - 左端高度依次为：最高、中、低、中高、中低；右端高度依次为：最高、中、中高、低、中高。
  B. 中央呼吸波纹线（Ripple Strings）：
     - 升级为更粗的线宽（1.5px），提升存在感；
     - 上下独立错开，动态起伏。
  C. 颜色纯度衰减与进度影响机制：
     - 基准线与两端内侧在未播放时呈低纯度金灰色（#A59B82）；
     - 已播放区域（0 到 prog_x 范围内）的所有元素（包括左侧爆发、中间波纹、基准线）全部变为高纯度的进度条金色（与两端最外侧一致的 #FCD373 亮金黄）。
  D. 悬停指示线：首尾精致的四角星（Four-Point Stars）。
"""

from __future__ import annotations

import math

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QLinearGradient, QBrush
from PyQt6.QtWidgets import QWidget


class AudioWaveformProgressBar(QWidget):
    """极致高保真鸣潮风非对称尖锐波形进度条。"""

    sigSeekStarted = pyqtSignal()
    sigSeekFinished = pyqtSignal(float)

    # ── 颜色常量定义 ──
    # 高纯度亮金黄色（图例中最纯的部分，亦为进度条颜色）
    COLOR_HIGH_PURITY = QColor(252, 211, 115, 255)  # #FCD373
    COLOR_PROGRESS_GLOW = QColor(255, 220, 130, 255) # 进度发光高亮金

    # 低纯度金灰色（未播放区域的内侧与基准线）
    COLOR_LOW_PURITY = QColor(165, 155, 130, 160)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(48)
        self.setMaximumHeight(64)
        self.setMouseTracking(True)

        # ── 进度与交互 ──
        self._progress: float = 0.0        # 0..1
        self._duration_ms: int = 0
        self._dragging: bool = False
        self._hover_pos: float = -1.0      # 鼠标悬停位置 (0..1)

        # ── 呼吸动画 ──
        self._time_phase: float = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(16)        # ~60 FPS
        self._timer.start()

    # ── 公开接口 ──────────────────────────────────────────────

    def set_progress(self, value: float, duration_ms: int = 0) -> None:
        """设置播放进度 (0.0 ~ 1.0)。"""
        if not self._dragging:
            self._progress = max(0.0, min(1.0, value))
        if duration_ms > 0:
            self._duration_ms = duration_ms

    def set_duration(self, ms: int) -> None:
        self._duration_ms = ms

    def get_progress(self) -> float:
        return self._progress

    def start_animation(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop_animation(self) -> None:
        self._timer.stop()

    def is_dragging(self) -> bool:
        return self._dragging

    # ── 鼠标交互 ──────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._progress = max(0.0, min(1.0, event.position().x() / self.width()))
            self.sigSeekStarted.emit()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().x() / self.width()
        self._hover_pos = max(0.0, min(1.0, pos))
        if self._dragging:
            self._progress = self._hover_pos
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            pos = max(0.0, min(1.0, event.position().x() / self.width()))
            self._progress = pos
            self.sigSeekFinished.emit(pos)
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover_pos = -1.0
        self.update()
        super().leaveEvent(event)

    def _tick(self) -> None:
        self._time_phase += 0.03
        self.update()

    # ── 核心绘制逻辑 ────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        if w < 10 or h < 10:
            painter.end()
            return

        mid_y = h / 2.0
        burst_len = w * 0.12
        capsule_start = w * 0.10
        capsule_end = w * 0.90
        prog_x = w * self._progress

        # ──【第一步：绘制未播放状态下的所有背景元素】──
        # 这时，波峰内侧、中间波纹、基准轨均呈现低纯度金灰色
        
        # 1. 绘制背景阴影波形 (Shadow Bursts)
        shadow_path = QPainterPath()
        self._build_double_burst_path(shadow_path, w, h, burst_len, mid_y, is_shadow=True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(160, 150, 125, 30))  # 极淡的影波
        painter.drawPath(shadow_path)

        # 2. 绘制低纯度基准轨 (Baseline)
        track_grad = QLinearGradient(0, mid_y, w, mid_y)
        track_grad.setColorAt(0.0, self.COLOR_HIGH_PURITY)
        track_grad.setColorAt(burst_len / w, self.COLOR_LOW_PURITY)
        track_grad.setColorAt(0.5, self.COLOR_LOW_PURITY)
        track_grad.setColorAt(1.0 - burst_len / w, self.COLOR_LOW_PURITY)
        track_grad.setColorAt(1.0, self.COLOR_HIGH_PURITY)

        pen_glow = QPen(track_grad, 2.5)
        pen_glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_glow)
        painter.drawLine(QPointF(0.0, mid_y), QPointF(w, mid_y))

        # 细微高亮中轨核心
        painter.setPen(QPen(QColor(255, 255, 255, 120), 0.7))
        painter.drawLine(QPointF(0.0, mid_y), QPointF(w, mid_y))

        # 3. 绘制中央动态呼吸波纹线 (Ripple Strings)
        self._draw_ripple_strings(painter, capsule_start, capsule_end, mid_y, is_progress=False)

        # 4. 绘制前景色音频爆发现象 (Foreground Waveform Bursts)
        fg_path = QPainterPath()
        self._build_double_burst_path(fg_path, w, h, burst_len, mid_y, is_shadow=False)
        
        fg_grad = QLinearGradient(0, mid_y, w, mid_y)
        fg_grad.setColorAt(0.0, self.COLOR_HIGH_PURITY)
        fg_grad.setColorAt(burst_len/w, self.COLOR_LOW_PURITY)
        fg_grad.setColorAt(0.5, self.COLOR_LOW_PURITY)
        fg_grad.setColorAt(1.0 - burst_len/w, self.COLOR_LOW_PURITY)
        fg_grad.setColorAt(1.0, self.COLOR_HIGH_PURITY)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fg_grad)
        painter.drawPath(fg_path)

        # ──【第二步：绘制已播放进度高亮覆盖（核心影响机制）】──
        # 将已播放区域（0 到 prog_x）通过 Clip 裁剪，在该区域内重新以高纯度进度条颜色渲染一切元素！
        if prog_x > 1.0:
            painter.save()
            painter.setClipRect(QRectF(0.0, 0.0, prog_x, h))
            
            # A. 进度基准线（高纯度金色 + 发光粗轨）
            pen_prog = QPen(self.COLOR_HIGH_PURITY, 3.2)
            pen_prog.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_prog)
            painter.drawLine(QPointF(0.0, mid_y), QPointF(prog_x, mid_y))
            
            # 白金发光核心
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1.0))
            painter.drawLine(QPointF(0.0, mid_y), QPointF(prog_x, mid_y))
            
            # B. 进度波纹线（被覆盖的高纯度金色波纹）
            self._draw_ripple_strings(painter, capsule_start, capsule_end, mid_y, is_progress=True)
            
            # C. 进度波频爆发现象（被覆盖的高纯度金色波频）
            painter.setPen(Qt.PenStyle.NoPen)
            # 已播放波形整体填充发光的亮金色
            painter.setBrush(self.COLOR_HIGH_PURITY)
            painter.drawPath(fg_path)
            
            # 叠加一层柔和发光覆盖
            painter.setBrush(QColor(255, 230, 160, 45))
            painter.drawPath(fg_path)
            
            painter.restore()

        # ──【第三步：绘制悬停指示线与四角星装饰】──
        if self._hover_pos >= 0.0 and not self._dragging:
            hx = w * self._hover_pos
            pen = QPen(QColor(252, 211, 115, 140), 1.2)
            painter.setPen(pen)
            painter.drawLine(QPointF(hx, 8), QPointF(hx, h - 8))

            # 顶部和底部绘制极其精致的四角星
            painter.setBrush(self.COLOR_PROGRESS_GLOW)
            painter.setPen(Qt.PenStyle.NoPen)
            self._draw_four_point_star(painter, hx, 8, R=4.5)
            self._draw_four_point_star(painter, hx, h - 8, R=4.5)

        painter.end()

    # ── 波形与波纹构建算法 ────────────────────────────────────────────

    def _draw_ripple_strings(self, painter: QPainter, start_x: float, end_x: float,
                             mid_y: float, is_progress: bool = False) -> None:
        """绘制中央多条错开的独立波纹线（Ripple Strings），水平最高点错开。"""
        n = 100
        
        # 1. 上侧波纹线 1 (最大振幅点大幅向左侧偏移，t≈0.25左右)
        path_t1 = QPainterPath()
        path_t1.moveTo(start_x, mid_y)
        for i in range(n + 1):
            t = i / n
            x = start_x + t * (end_x - start_x)
            # 使用 t ** 0.35 极左偏斜信封
            envelope = math.sin((t ** 0.35) * math.pi) ** 1.6
            osc = 1.0 + 0.15 * math.sin(self._time_phase * 1.5 + t * 4 * math.pi)
            y = mid_y - 8.0 * envelope * osc
            path_t1.lineTo(QPointF(x, y))

        # 2. 上侧波纹线 2 (最大振幅点向左侧偏移，t≈0.40左右)
        path_t2 = QPainterPath()
        path_t2.moveTo(start_x, mid_y)
        for i in range(n + 1):
            t = i / n
            x = start_x + t * (end_x - start_x)
            # 使用 t ** 0.7 左偏斜信封
            envelope = math.sin((t ** 0.7) * math.pi) ** 1.3
            osc = 0.7 + 0.10 * math.sin(self._time_phase * 1.0 + t * 5.5 * math.pi + 1.2)
            y = mid_y - 5.5 * envelope * osc
            path_t2.lineTo(QPointF(x, y))

        # 3. 下侧波纹线 1 (最大振幅点向右侧偏移，t≈0.70左右)
        path_b1 = QPainterPath()
        path_b1.moveTo(start_x, mid_y)
        for i in range(n + 1):
            t = i / n
            x = start_x + t * (end_x - start_x)
            # 使用 t ** 2.5 右偏斜信封
            envelope = math.sin((t ** 2.5) * math.pi) ** 1.5
            osc = 1.1 + 0.18 * math.sin(self._time_phase * 1.2 + t * 3.5 * math.pi + 0.6)
            y = mid_y + 8.5 * envelope * osc
            path_b1.lineTo(QPointF(x, y))

        # 4. 下侧波纹线 2 (最大振幅点大幅向右侧偏移，t≈0.95左右)
        path_b2 = QPainterPath()
        path_b2.moveTo(start_x, mid_y)
        for i in range(n + 1):
            t = i / n
            x = start_x + t * (end_x - start_x)
            # 使用 t ** 3.2 极右偏斜信封
            envelope = math.sin((t ** 3.2) * math.pi) ** 1.2
            osc = 0.65 + 0.08 * math.sin(self._time_phase * 0.8 + t * 6.2 * math.pi + 2.1)
            y = mid_y + 4.8 * envelope * osc
            path_b2.lineTo(QPointF(x, y))

        # 绘制所有错开的波纹线
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        # 决定绘制颜色与线粗（已播放区域使用亮金色，未播放区域使用低纯度色，线宽加粗到 1.5px）
        if is_progress:
            pen_main = QPen(self.COLOR_HIGH_PURITY, 1.5)
            pen_sub = QPen(self.COLOR_PROGRESS_GLOW, 1.5)
        else:
            pen_main = QPen(self.COLOR_LOW_PURITY, 1.5)
            pen_sub = QPen(self.COLOR_LOW_PURITY, 1.5)

        # 绘制
        painter.setPen(pen_main)
        painter.drawPath(path_t1)
        painter.setPen(pen_sub)
        painter.drawPath(path_t2)

        painter.setPen(pen_main)
        painter.drawPath(path_b1)
        painter.setPen(pen_sub)
        painter.drawPath(path_b2)
        
        painter.restore()

    def _build_double_burst_path(self, path: QPainterPath, w: float, h: float,
                                 burst_len: float, mid_y: float, is_shadow: bool = False) -> None:
        """构建两端音频爆发的完整对称齿状闭合路径（完美镜像对称）。"""
        upper_peaks = self._generate_peaks(w, h, burst_len, mid_y, is_shadow=is_shadow)
        
        # 1. 绘制上侧从左到右
        path.moveTo(upper_peaks[0])
        for pt in upper_peaks[1:]:
            path.lineTo(pt)
            
        # 2. 绘制下侧从右到左镜像返回
        for pt in reversed(upper_peaks):
            y_mirrored = mid_y + (mid_y - pt.y())
            path.lineTo(QPointF(pt.x(), y_mirrored))
            
        path.closeSubpath()

    def _generate_peaks(self, w: float, h: float, burst_len: float, mid_y: float,
                        is_shadow: bool = False) -> list[QPointF]:
        """计算左右非对称音频爆发的上侧齿形坐标。"""
        n = 85
        pts: list[QPointF] = []

        amp_scale = 1.15 if is_shadow else 1.0
        base_amp = h * 0.40 * amp_scale
        time_offset = self._time_phase * 0.8 if not is_shadow else 0.0

        # ── 1. 左端爆发点（0 ~ burst_len）：最高，中，低，中高，中低 ──
        for i in range(n + 1):
            t = i / n
            x = t * burst_len
            
            # 使用尖锐的指数绝对值包络（Cusp Peaks）产生极具数码音响质感的尖锐波峰
            # 最外侧第一峰（t=0）只取靠内侧的半波设计：设中心在 t=0.0，向内单侧指数衰减
            p1 = 0.92 * math.exp(-abs(t - 0.00) / 0.05)   # 最高 (x=0)
            p2 = 0.55 * math.exp(-abs(t - 0.20) / 0.06)   # 中
            p3 = 0.25 * math.exp(-abs(t - 0.40) / 0.06)   # 低
            p4 = 0.72 * math.exp(-abs(t - 0.62) / 0.06)   # 中高
            p5 = 0.42 * math.exp(-abs(t - 0.85) / 0.06)   # 中低
            
            breath = 1.0 + 0.05 * math.sin(time_offset + t * math.pi * 3)
            ripple = 0.93 + 0.07 * math.sin(t * 130.0)
            
            amp_ratio = max(p1, p2, p3, p4, p5) * breath * ripple
            y = mid_y - amp_ratio * base_amp
            pts.append(QPointF(x, y))

        # 中间平坦部分起点
        pts.append(QPointF(w - burst_len, mid_y))

        # ── 2. 右端爆发点（w-burst_len ~ w）：最高，中，中高，低，中高 ──
        # (同样采用尖锐波峰和外侧半波设计，x=w 处为最高峰 Peak 1 且只向内衰减)
        for i in range(n + 1):
            t = i / n
            x = (w - burst_len) + t * burst_len
            t_mirror = 1.0 - t  # t_mirror=0.0 为最外侧 (x=w)，t_mirror=1.0 为最内侧 (x=w-burst_len)
            
            p1 = 0.95 * math.exp(-abs(t_mirror - 0.00) / 0.05)   # 最高 (x=w)
            p2 = 0.55 * math.exp(-abs(t_mirror - 0.20) / 0.06)   # 中
            p3 = 0.76 * math.exp(-abs(t_mirror - 0.42) / 0.06)   # 中高
            p4 = 0.30 * math.exp(-abs(t_mirror - 0.64) / 0.06)   # 低
            p5 = 0.66 * math.exp(-abs(t_mirror - 0.85) / 0.06)   # 中高
            
            breath = 1.0 + 0.05 * math.sin(time_offset + t_mirror * math.pi * 3)
            ripple = 0.93 + 0.07 * math.sin(t_mirror * 130.0)
            
            amp_ratio = max(p1, p2, p3, p4, p5) * breath * ripple
            y = mid_y - amp_ratio * base_amp
            pts.append(QPointF(x, y))

        return pts

    def _draw_four_point_star(self, painter: QPainter, cx: float, cy: float, R: float) -> None:
        """绘制优雅的弯曲四角星。"""
        path = QPainterPath()
        path.moveTo(cx, cy - R)
        path.quadTo(cx, cy, cx + R, cy)
        path.quadTo(cx, cy, cx, cy + R)
        path.quadTo(cx, cy, cx - R, cy)
        path.quadTo(cx, cy, cx, cy - R)
        path.closeSubpath()
        painter.drawPath(path)