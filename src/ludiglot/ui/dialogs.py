from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect, QSize
from PyQt6.QtGui import QFont, QColor, QPainter, QCursor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QWidget, QProgressBar, QGraphicsDropShadowEffect,
    QLineEdit, QSpinBox, QDoubleSpinBox
)


class StyledDialog(QDialog):
    """
    符合 Ludiglot 整体风格的自定义对话框。
    无边框、半透明暗色背景、自定义按钮样式。
    """
    def __init__(self, title: str, message: str, parent=None, is_question: bool = False):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self._dragging = False
        self._drag_pos = QPoint()
        self.result_value = False

        # 主容器
        self.container = QWidget(self)
        self.container.setObjectName("DialogContainer")
        self.container.setStyleSheet("""
            QWidget#DialogContainer {
                background-color: rgba(20, 25, 35, 245);
                border: 1px solid rgba(170, 155, 106, 120);
                border-radius: 8px;
            }
            QLabel#TitleLabel {
                color: #aa9b6a;
                font-size: 14pt;
                font-weight: bold;
                background: transparent;
            }
            QLabel#MessageLabel {
                color: #cbd5e1;
                font-size: 11pt;
                background: transparent;
            }
            QPushButton {
                background-color: rgba(26, 35, 50, 180);
                color: #94a3b8;
                border: 1px solid rgba(45, 55, 72, 120);
                border-radius: 4px;
                padding: 6px 20px;
                font-size: 10pt;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: rgba(45, 55, 72, 220);
                color: #e2e8f0;
                border-color: rgba(170, 155, 106, 150);
            }
            QPushButton#PrimaryBtn {
                background-color: rgba(170, 155, 106, 40);
                border: 1px solid rgba(170, 155, 106, 100);
                color: #aa9b6a;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: rgba(170, 155, 106, 80);
                color: #fbbf24;
            }
        """)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(20)

        # 标题
        self.title_label = QLabel(title)
        self.title_label.setObjectName("TitleLabel")
        layout.addWidget(self.title_label)

        # 消息
        self.message_label = QLabel(message)
        self.message_label.setObjectName("MessageLabel")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        # 按钮栏
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        if is_question:
            self.no_btn = QPushButton("取消")
            self.no_btn.clicked.connect(self.reject)
            btn_layout.addWidget(self.no_btn)
            
            self.yes_btn = QPushButton("确定")
            self.yes_btn.setObjectName("PrimaryBtn")
            self.yes_btn.clicked.connect(self.accept)
            btn_layout.addWidget(self.yes_btn)
        else:
            self.ok_btn = QPushButton("好的")
            self.ok_btn.setObjectName("PrimaryBtn")
            self.ok_btn.clicked.connect(self.accept)
            btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10) # 留出阴影空间
        main_layout.addWidget(self.container)

        # 添加阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 5)
        self.container.setGraphicsEffect(shadow)

        self.setMinimumWidth(400)
        self.setMaximumWidth(550)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False

    @staticmethod
    def information(parent, title: str, message: str):
        dlg = StyledDialog(title, message, parent, is_question=False)
        return dlg.exec()

    @staticmethod
    def question(parent, title: str, message: str):
        dlg = StyledDialog(title, message, parent, is_question=True)
        return dlg.exec()

    @staticmethod
    def warning(parent, title: str, message: str):
        return StyledDialog.information(parent, title, message)

    @staticmethod
    def critical(parent, title: str, message: str):
        return StyledDialog.information(parent, title, message)


class StyledProgressDialog(QDialog):
    """
    符合 Ludiglot 风格的进度对话框。
    """
    def __init__(self, title: str, message: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        self.container = QWidget(self)
        self.container.setObjectName("ProgressContainer")
        self.container.setStyleSheet("""
            QWidget#ProgressContainer {
                background-color: rgba(20, 25, 35, 245);
                border: 1px solid rgba(170, 155, 106, 120);
                border-radius: 8px;
            }
            QLabel#TitleLabel {
                color: #aa9b6a;
                font-size: 13pt;
                font-weight: bold;
                background: transparent;
            }
            QLabel#StatusLabel {
                color: #94a3b8;
                font-size: 10pt;
                background: transparent;
            }
            QProgressBar {
                border: 1px solid rgba(45, 55, 72, 100);
                border-radius: 3px;
                background-color: rgba(26, 35, 50, 150);
                text-align: center;
                color: transparent;
                height: 6px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(170, 155, 106, 180),
                    stop:1 rgba(251, 191, 36, 220));
                border-radius: 2px;
            }
        """)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(15)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("TitleLabel")
        layout.addWidget(self.title_label)

        self.status_label = QLabel(message)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # 忙碌状态
        layout.addWidget(self.progress_bar)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 5)
        self.container.setGraphicsEffect(shadow)

        self.setMinimumWidth(450)

    def setLabelText(self, text: str):
        self.status_label.setText(text)

    def setValue(self, value: int):
        self.progress_bar.setValue(value)

    def setRange(self, min_val: int, max_val: int):
        self.progress_bar.setRange(min_val, max_val)


class StyledInputDialog(StyledDialog):
    """
    符合 Ludiglot 风格的输入对话框。
    """
    def __init__(self, title: str, message: str, parent=None, initial=0, min_val=0, max_val=100, decimals=0):
        super().__init__(title, message, parent, is_question=True)
        
        # 删除父类自动添加的 message_label，我们要重新排列
        self.message_label.deleteLater()
        
        # 重新创建内容布局
        content_layout = self.container.layout()
        # 按钮栏在最后，我们在标题后插入消息和输入框
        
        self.msg_label = QLabel(message)
        self.msg_label.setObjectName("MessageLabel")
        content_layout.insertWidget(1, self.msg_label)

        if decimals == 0:
            self.input_field = QSpinBox()
            self.input_field.setRange(int(min_val), int(max_val))
            self.input_field.setValue(int(initial))
        else:
            self.input_field = QDoubleSpinBox()
            self.input_field.setRange(float(min_val), float(max_val))
            self.input_field.setValue(float(initial))
            self.input_field.setDecimals(decimals)
            self.input_field.setSingleStep(0.1)

        self.input_field.setStyleSheet("""
            QSpinBox, QDoubleSpinBox {
                background-color: rgba(26, 35, 50, 150);
                color: #e2e8f0;
                border: 1px solid rgba(45, 55, 72, 120);
                border-radius: 4px;
                padding: 8px;
                font-size: 11pt;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                width: 0px;
            }
        """)
        self.input_field.setMinimumHeight(45)
        content_layout.insertWidget(2, self.input_field)
        self.input_field.setFocus()

    def get_value(self):
        return self.input_field.value()

    @staticmethod
    def get_int(parent, title: str, message: str, initial=0, min_val=0, max_val=100):
        dlg = StyledInputDialog(title, message, parent, initial, min_val, max_val, 0)
        ok = dlg.exec()
        return dlg.get_value(), ok == QDialog.DialogCode.Accepted

    @staticmethod
    def get_double(parent, title: str, message: str, initial=0, min_val=0, max_val=100, decimals=1):
        dlg = StyledInputDialog(title, message, parent, initial, min_val, max_val, decimals)
        ok = dlg.exec()
        return dlg.get_value(), ok == QDialog.DialogCode.Accepted
