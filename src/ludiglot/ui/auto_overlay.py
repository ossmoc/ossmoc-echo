"""Source-aligned, click-through blocks and a non-activating document reader."""
from __future__ import annotations

import math

from PyQt6.QtCore import QRectF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QTextEdit, QVBoxLayout

from ludiglot.core.auto_wuwa import AutoDocument, AutoTextBlock
from ludiglot.core.desktop_models import AUTO_DEFAULTS
from ludiglot.ui.subtitle_overlay import SubtitleOverlay


def logical_frame(bounds: dict) -> tuple[int, int, int, int]:
    """Physical MSS coordinates → Qt logical coordinates on the same monitor."""
    import mss
    with mss.mss() as capture:
        monitors = capture.monitors[1:]
    cx = bounds["left"] + bounds["width"] / 2
    cy = bounds["top"] + bounds["height"] / 2
    for index, monitor in enumerate(monitors):
        if monitor["left"] <= cx < monitor["left"] + monitor["width"] and monitor["top"] <= cy < monitor["top"] + monitor["height"]:
            screens = QApplication.screens()
            if index >= len(screens):
                raise ValueError("Không ánh xạ được màn hình Auto sang Qt.")
            rect = screens[index].geometry()
            sx, sy = monitor["width"] / rect.width(), monitor["height"] / rect.height()
            return (round(rect.x() + (bounds["left"] - monitor["left"]) / sx),
                    round(rect.y() + (bounds["top"] - monitor["top"]) / sy),
                    round(bounds["width"] / sx), round(bounds["height"] / sy))
    raise ValueError("Khung Auto không nằm trên màn hình hiện có.")


class AutoInlineOverlay(SubtitleOverlay):
    def __init__(self):
        self.auto_blocks = ()
        self.frame_size = (1920, 1080)
        self.auto_settings = dict(AUTO_DEFAULTS)
        self.drawn_bounds = []
        self.overflow = []
        self.content_signature = None
        self._presentations = {}
        self._presentation_context = None
        self._choice_signature = None
        super().__init__()
        self.setMinimumSize(1, 1)
        self.setWindowTitle("Ossmoc Echo — Bản dịch")

    def set_frame(self, bounds: dict, blocks: tuple[AutoTextBlock, ...], settings: dict):
        geometry = logical_frame(bounds)
        changed_geometry = self.geometry().getRect() != geometry
        self.setGeometry(*geometry)
        if changed_geometry:
            self._capture_tested = False
            self.capture_excluded = False
        self.frame_size = (bounds["width"], bounds["height"])
        self.auto_settings = {**AUTO_DEFAULTS, **settings}
        self.settings.update(font_family=self.auto_settings["font_family"],
                             text_color=self.auto_settings["text_color"], outline_color=self.auto_settings["outline_color"],
                             outline_width=self.auto_settings["outline_width"], line_spacing=1.0, alignment="left")
        self.auto_blocks = blocks  # refresh invisible source/ID metadata
        style_keys = ('font_family','font_size','choice_font_size','text_color','outline_color','outline_width',
                      'background_color','background_opacity','show_bounds','generation')
        # OCR context IDs and reader scrolling settings aren't display style.
        # Alternating response/story IDs must not release the sentence lock.
        context = (tuple(sorted(bounds.items())), geometry,
                   tuple((k,str(self.auto_settings.get(k))) for k in style_keys))
        if context != self._presentation_context:
            self.reset_presentation()
            self._presentation_context = context
        choices = tuple(self._block_key(b) for b in blocks if b.kind == 'choices')
        if choices != self._choice_signature:
            self._presentations = {k:v for k,v in self._presentations.items() if k[0]!='choices'}
            self._choice_signature = choices
        active = {self._block_key(b) for b in blocks}
        self._presentations = {k: v for k, v in self._presentations.items() if k in active}
        for block in blocks:
            if block.kind not in {'dialogue', 'choices'}:
                continue
            candidate = self._calculate_layout(block)
            key = self._block_key(block)
            if candidate is None:
                if not self._valid_geometry(block):
                    self._presentations.pop(key, None)
                continue
            rect, font = candidate
            x,y,w,h = block.bounds
            ink = QRectF(x*self.width()/self.frame_size[0], y*self.height()/self.frame_size[1],
                         w*self.width()/self.frame_size[0], h*self.height()/self.frame_size[1])
            state = self._presentations.get(key)
            if state is None:
                self._presentations[key] = {'text': (QRectF(rect), QFont(font)),
                                           'mask': QRectF(rect), 'ink': ink,
                                           'pending': None, 'count': 0}
                continue
            # The sentence's text placement/wrapping/font never follow OCR ink.
            # A genuine English expansion can grow the mask after two similar
            # observations, but the mask never shrinks during this sentence.
            mask = state['mask']
            tolerance = max(2., 3*self.height()/1080)
            if state['ink'].adjusted(-tolerance,-tolerance,tolerance,tolerance).contains(ink):
                state['pending'] = None; state['count'] = 0
                continue
            pending = state['pending']
            consistent = pending is not None and all(abs(a-b) <= tolerance*2
                for a,b in zip(ink.getRect(), pending.getRect()))
            state['count'] = state['count']+1 if consistent else 1
            state['pending'] = QRectF(ink)
            if state['count'] >= 2:
                state['mask'] = mask if mask.contains(rect) else mask.united(rect)
                state['ink'] = state['ink'].united(ink)
                state['pending'] = None; state['count'] = 0
        signature = (tuple((self._block_key(b),
                            self._mask_rect(b).getRect() if self._mask_rect(b) else None)
                           for b in blocks), context)
        if signature != self.content_signature or changed_geometry:
            self.content_signature = signature
            self.update()
        # Computed before painting for the controller's non-fitting status.
        self.overflow = [block for block in blocks if self._layout(block) is None]

    @staticmethod
    def _block_key(block):
        return block.kind, block.identity, block.target, block.order, block.caption_mode, block.viewport

    def reset_presentation(self):
        self._presentations.clear()
        self._presentation_context = None
        self.content_signature = None
        self._choice_signature = None

    def _valid_geometry(self, block):
        x,y,w,h = block.bounds
        fw,fh = self.frame_size
        vx,vy,vw,vh=block.viewport or (0,0,fw,fh)
        return (all(math.isfinite(v) for v in (x,y,w,h)) and x>=0 and y>=0
                and w>0 and h>0 and x+w<=fw and y+h<=fh
                and (block.kind!='dialogue' or (x>=vx and x+w<=vx+vw and y+h<=vy+vh
                     and y>=vy+vh*(.72 if block.caption_mode=='walking' else .78))))

    def _mask_rect(self, block):
        state = self._presentations.get(self._block_key(block))
        if state:
            return state['mask']
        layout = self._calculate_layout(block)
        return layout[0] if layout else None

    def _dialogue_layout(self, block):
        """Fit verified dialogue below the speaker, never in a top fallback.

        Partial retrieval uses the same genuine visible OCR bounds as full
        retrieval. Reserve only the height actually needed for English ink
        and Vietnamese lines, never the entire game's caption viewport.
        """
        fw, fh = self.frame_size
        vx,vy,vw,vh=block.viewport or (0,0,fw,fh)
        x, y, width, height = block.bounds
        walking = block.caption_mode == 'walking'
        minimum_top = .72 if walking else .78
        if (not all(math.isfinite(v) for v in (x,y,width,height)) or width <= 0 or height <= 0
                or x < vx or y < vy+vh*minimum_top or x+width > vx+vw or y+height > vy+vh):
            return None
        sx, sy = self.width()/fw, self.height()/fh
        scale = fh/1080
        # WuWa lays out the complete sentence around the game-frame center
        # before revealing its glyphs. Mirror genuine ink's outermost reach,
        # rather than centering on a short, left-hand typewriter fragment.
        midpoint=vx+vw/2
        half_width = min(vw/2, max(midpoint-x, x+width-midpoint) + 10*scale)
        left, right = midpoint-half_width, midpoint+half_width
        top = y
        bottom = max(y+height+4*scale, y+32*scale+4*scale)
        limit_bottom = vy+vh*(.815 if walking else .925)  # Keep HUD/advance marker untouched.
        if bottom > limit_bottom+1:
            return None
        preferred = QRectF(left*sx, top*sy, (right-left)*sx, (bottom-top)*sy)
        center = midpoint*sx
        maximum = max(preferred.width(), vw*.90*sx)
        base = max(1, round(float(self.auto_settings['font_size'])*scale*sy))
        padding = max(3.0, 4*scale*sx)

        def font_for(size):
            font = QFont(self.settings['font_family']); font.setPixelSize(size)
            return font

        def fits(rect, font):
            lines = list(self._paragraph_lines(block.target, font, max(1,rect.width()-2*padding)))
            return bool(lines) and len(lines)*QFontMetricsF(font).height() <= rect.height()-2*scale*sy

        def compact(rect, font):
            # Find a fitting width while always covering the English slot.
            lo, hi = preferred.width(), rect.width()
            for _ in range(12):
                mid = (lo+hi)/2
                candidate = QRectF(center-mid/2,rect.y(),mid,rect.height())
                if fits(candidate,font):
                    hi = mid
                else:
                    lo = mid
            return QRectF(center-hi/2,rect.y(),hi,rect.height())

        font = font_for(base)
        if fits(preferred, font):
            return preferred, font
        # Wrap at the mirrored English width before widening. Only borrow
        # space below the ink, never the speaker name or the advance/HUD strip.
        available_height = max(preferred.height(), (limit_bottom-top)*sy)
        for candidate_width in (preferred.width(), maximum):
            area = QRectF(center-candidate_width/2,preferred.y(),candidate_width,available_height)
            if fits(area,font):
                result = compact(area,font)
                lines = list(self._paragraph_lines(block.target,font,max(1,result.width()-2*padding)))
                needed = len(lines)*QFontMetricsF(font).height()+2*scale*sy
                result.setHeight(max(preferred.height(),needed))
                return result,font
        # Finite screen space cannot fit arbitrary-length text legibly. Leave
        # English visible and report in the controller; never truncate/move up.
        return None

    def _layout(self, block):
        state = self._presentations.get(self._block_key(block))
        if state:
            return state['text']
        return self._calculate_layout(block)

    def _calculate_layout(self, block):
        if block.kind == 'dialogue':
            return self._dialogue_layout(block)
        if block.partial:
            return None
        fw, fh = self.frame_size
        sx, sy = self.width() / fw, self.height() / fh
        x, y, width, height = block.bounds
        if block.kind == "choices":
            # Keep inside the text portion of the original right-side buttons.
            width = max(width, round(fw * .88) - x)
            # OCR measures glyph ink, not the button's line-height. A small
            # scaled inset around that real box accommodates Vietnamese marks,
            # while staying well inside the sampled WuWa button padding.
            padding = round(6 * fh / 1080)
            y -= padding
            height = max(height + padding * 2, 44 * fh / 1080)
            # Borrow button padding only, never the next response's text.
            following = [b.bounds[1] for b in self.auto_blocks
                         if b.kind == 'choices' and b.bounds[1] > block.bounds[1]]
            if following:
                height = min(height, min(following)-y-12*fh/1080-4*fh/1080)
        elif block.kind == 'glossary':
            # Only real text rectangles, not the whole modal panel: leave its
            # heading decoration, close icon and response buttons untouched.
            padding = 6 * fh / 1080
            x -= padding; y -= padding
            width += padding*2; height += padding*2
        rect = QRectF(x * sx, y * sy, width * sx, (height + 4 * fh / 1080) * sy)
        if rect.left() < 0 or rect.right() > self.width() or rect.bottom() > self.height():
            return None
        size_key = 'choice_font_size' if block.kind=='choices' else 'font_size'
        base = max(1, round(float(self.auto_settings[size_key]) * fh / 1080 * sy))
        font = QFont(self.settings["font_family"]); font.setPixelSize(base)
        lines = list(self._paragraph_lines(block.target, font, max(1, rect.width() - max(6, 8 * sx))))
        if len(lines) * QFontMetricsF(font).height() <= rect.height() - 2 * sy:
            return rect, font
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.drawn_bounds = []
        self.content_truncated = False
        for block in self.auto_blocks:
            layout = self._layout(block)
            if layout is None:
                continue
            text_rect, font = layout
            rect = self._mask_rect(block)
            bg = QColor("#000000" if block.kind == "transition" else self.auto_settings["background_color"])
            bg.setAlpha(round(float(self.auto_settings["background_opacity"]) * 2.55))
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(bg)
            painter.drawRoundedRect(rect, 3, 3)
            painter.save(); painter.setClipRect(rect)
            padding = max(3.0, 4*self.width()/self.frame_size[0]*self.frame_size[1]/1080) if block.kind == 'dialogue' else 3
            area = text_rect.adjusted(padding, 0, -padding, 0)
            self.settings["alignment"] = "center" if block.kind in {"dialogue", "transition"} else "left"
            self._draw_text(painter, block.target, area.top(), font, QColor(self.settings["text_color"]), area)
            painter.restore()
            self.drawn_bounds.append((block.kind, rect.getRect()))
        if self.auto_settings.get("show_bounds"):
            sx, sy = self.width() / self.frame_size[0], self.height() / self.frame_size[1]
            painter.setBrush(Qt.BrushStyle.NoBrush); painter.setPen(QPen(QColor("#2dd4bf"), 1))
            for block in self.auto_blocks:
                x, y, w, h = block.bounds
                painter.drawRect(QRectF(x*sx, y*sy, w*sx, h*sy))
        if self._probe:
            for index, color in enumerate(("#ff00c5", "#00fb71", "#1153ff")):
                painter.fillRect(5 + index * 8, 5, 8, 12, QColor(color))


class DocumentReader(QDialog):
    geometry_changed = pyqtSignal(dict)
    font_changed = pyqtSignal(int)
    capture_exclusion_changed = pyqtSignal(bool)
    _apply_capture_affinity = SubtitleOverlay._apply_capture_affinity
    verify_capture_exclusion = SubtitleOverlay.verify_capture_exclusion

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Ossmoc Echo — Tài liệu Việt")
        self.setStyleSheet("QDialog {background:#20242b;color:white} QTextEdit {background:#15191f;color:white;border:0} QLabel {color:white}")
        self.document_id = None
        self.dismissed_id = None
        self.capture_excluded = False
        self._capture_tested = self._capture_test_pending = self._probe = False
        self._affinity_requested = False
        self._applying_geometry = False
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 24, 16, 16)
        header = QHBoxLayout()
        self.title_label = QLabel(); self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.title_label, 1)
        self.font_size = QSpinBox(); self.font_size.setRange(14, 64); self.font_size.setValue(22)
        self.font_size.valueChanged.connect(self._set_font)
        header.addWidget(self.font_size)
        copy = QPushButton("Copy"); copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        header.addWidget(copy)
        close = QPushButton("Đóng"); close.clicked.connect(self.close); header.addWidget(close)
        layout.addLayout(header)
        self.text = QTextEdit(); self.text.setReadOnly(True)
        layout.addWidget(self.text)
        self._set_font(22)

    def _set_font(self, size):
        if not hasattr(self, "text"):
            return
        self.text.setFont(QFont("Segoe UI", size))
        self.font_changed.emit(size)

    def region_bounds_physical(self):
        # Unlike the frameless subtitle, this dialog has a native title bar.
        # Probe coordinates are client-relative: GetWindowRect would sample the
        # title bar instead of the painted marker and falsely report exclusion.
        import sys
        if sys.platform == "win32" and (self.isVisible() or self.document_id):
            import win32gui
            handle = int(self.winId())
            left, top = win32gui.ClientToScreen(handle, (0,0))
            x, y, right, bottom = win32gui.GetClientRect(handle)
            return {"left": left, "top": top, "width": right-x, "height": bottom-y}
        return SubtitleOverlay.region_bounds_physical(self)

    def set_document(self, document: AutoDocument | None, frame: dict, settings: dict, *, hidden=False):
        if document is None:
            self.document_id = self.dismissed_id = None
            self.hide(); return
        if self.document_id != document.identity:
            self.dismissed_id = None
            self.document_id = document.identity
            self.title_label.setText(document.title + (" — phần đã xác nhận" if document.partial else ""))
            self.text.setPlainText(document.target)
            self.text.verticalScrollBar().setValue(0)
            geometry = settings.get("reader_geometry") or {}
            x, y, width, height = logical_frame(frame)
            rect = (x + round(width*.175), y + round(height*.125), round(width*.65), round(height*.75))
            if all(k in geometry for k in ("x","y","width","height")):
                saved = tuple(int(geometry[k]) for k in ("x","y","width","height"))
                from PyQt6.QtCore import QRect
                if any(QRect(*saved).intersects(s.availableGeometry()) for s in QApplication.screens()):
                    rect = saved
            self._applying_geometry = True
            self.setGeometry(*rect)
            self._applying_geometry = False
            self.font_size.setValue(int(settings.get("reader_font_size", 22)))
        # A saved player name can change while this same document is open.
        # Refresh its text without resetting geometry, scroll or dismissal.
        title = document.title + (" — phần đã xác nhận" if document.partial else "")
        if self.title_label.text() != title:
            self.title_label.setText(title)
        if self.text.toPlainText() != document.target:
            position = self.text.verticalScrollBar().value()
            self.text.setPlainText(document.target)
            self.text.verticalScrollBar().setValue(position)
        if hidden or self.dismissed_id == document.identity:
            self.hide()
        else:
            self.show()

    def closeEvent(self, event):
        self.dismissed_id = self.document_id
        self.hide(); event.ignore()

    def reject(self):
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        self._affinity_requested = self._apply_capture_affinity()
        if not self._capture_tested:
            QTimer.singleShot(250, self.verify_capture_exclusion)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._probe:
            painter = QPainter(self)
            for index, color in enumerate(("#ff00c5", "#00fb71", "#1153ff")):
                painter.fillRect(5 + index * 8, 5, 8, 12, QColor(color))

    def _save_geometry(self):
        if self.isVisible() and not self._applying_geometry:
            self.geometry_changed.emit(dict(zip(("x","y","width","height"), self.geometry().getRect())))

    def moveEvent(self, event):
        super().moveEvent(event); self._save_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event); self._save_geometry()
