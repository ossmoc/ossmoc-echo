"""Small game-only picker; native handles never become user-facing labels."""
import os
import re
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout


def is_wuwa(target):
    title = re.sub(r'\s+', '', target.title).casefold()
    executable = target.executable.replace('\\','/').casefold()
    return ('wutheringwaves' in title or '鸣潮' in title or
            ('wutheringwaves' in re.sub(r'\s+','',executable) and executable.endswith('client-win64-shipping.exe'))) \
        and 'launcher' not in os.path.basename(executable)


class GamePicker(QDialog):
    def __init__(self, windows, refresh, parent=None):
        super().__init__(parent)
        from ludiglot.ui.control_panel import PANEL_STYLESHEET
        self.setStyleSheet(PANEL_STYLESHEET)
        self.setWindowTitle('Kết nối Wuthering Waves')
        self.resize(500,330)
        self.selected_target = None
        self._refresh = refresh
        layout = QVBoxLayout(self); layout.setContentsMargins(24,24,24,24); layout.setSpacing(14)
        title = QLabel('Chọn cửa sổ game'); title.setObjectName('pageTitle'); layout.addWidget(title)
        note = QLabel('Mở Wuthering Waves trước, rồi chọn game bên dưới.'); note.setWordWrap(True); layout.addWidget(note)
        self.windows = QListWidget(); self.windows.setMinimumHeight(110); layout.addWidget(self.windows)
        self.message = QLabel(); self.message.setWordWrap(True); self.message.setObjectName('muted'); layout.addWidget(self.message)
        row = QHBoxLayout(); reload = QPushButton('Tìm lại'); reload.clicked.connect(lambda:self.populate(self._refresh())); row.addWidget(reload)
        row.addStretch(); cancel = QPushButton('Để sau'); cancel.clicked.connect(self.reject); row.addWidget(cancel)
        self.connect_button = QPushButton('Kết nối'); self.connect_button.setProperty('primary',True)
        self.connect_button.clicked.connect(self.select); row.addWidget(self.connect_button); layout.addLayout(row)
        self.windows.currentRowChanged.connect(lambda index:self.connect_button.setEnabled(index>=0))
        self.windows.itemDoubleClicked.connect(lambda _:self.select())
        self.populate(windows)

    def populate(self, windows):
        self.windows.clear()
        games = [target for target in windows if is_wuwa(target)]
        for number,target in enumerate(games,1):
            item = QListWidgetItem('Wuthering Waves' + (f' · cửa sổ {number}' if len(games)>1 else ''))
            item.setData(Qt.ItemDataRole.UserRole,target); self.windows.addItem(item)
        self.connect_button.setEnabled(bool(games))
        self.message.setText('App chỉ đọc game đã chọn, không đọc các cửa sổ khác.' if games else
                             'Chưa thấy game. Nếu đang thu nhỏ game, hãy mở lại rồi bấm Tìm lại.')
        if games: self.windows.setCurrentRow(0)

    def select(self):
        item = self.windows.currentItem()
        if item:
            self.selected_target = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
