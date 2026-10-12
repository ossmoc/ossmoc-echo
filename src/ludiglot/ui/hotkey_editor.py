"""Single-combination recorder shared by region and application shortcuts."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ludiglot.ui.qt_hotkey_adapter import parse_win_hotkey


def canonical_hotkey(value: str) -> str:
    if not value.strip():
        return ""
    spec = parse_win_hotkey(value)
    if spec is None:
        raise ValueError("Chỉ nhận một tổ hợp Ctrl/Alt/Shift/Win với chữ, số hoặc F1–F24.")
    mods = [name for bit, name in ((2, "Ctrl"), (1, "Alt"), (4, "Shift"), (8, "Win")) if spec.modifiers & bit]
    if 0x70 <= spec.vk <= 0x87:
        key = f"F{spec.vk - 0x6f}"
    elif 48 <= spec.vk <= 90:
        key = chr(spec.vk)
        if not mods:
            raise ValueError("Phím chữ/số cần Ctrl, Alt, Shift hoặc Win để không chiếm phím chơi game.")
    else:
        raise ValueError("Phím không được hỗ trợ.")
    return "+".join(mods + [key])


class HotkeyRecorder(QLineEdit):
    def __init__(self, value="", controller=None, parent=None):
        super().__init__(value, parent)
        self.controller = controller
        self.setReadOnly(True)
        self.setPlaceholderText("Bấm vào đây rồi nhấn tổ hợp phím")
        self.setToolTip("Một tổ hợp duy nhất; F1–F24 hoặc Ctrl/Alt/Shift/Win + chữ/số.")
        self._suspended = False

    def focusInEvent(self, event):
        if self.controller and hasattr(self.controller, "suspend_hotkeys"):
            self.controller.suspend_hotkeys()
            self._suspended = True
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        if self._suspended:
            self.controller.resume_hotkeys()
            self._suspended = False
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.clearFocus()
            event.ignore()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.clear(); event.accept(); return
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
            suffix = f"F{key - Qt.Key.Key_F1 + 1}"
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            suffix = chr(key)
        else:
            event.accept(); return
        mods = event.modifiers()
        prefix = [name for flag, name in ((Qt.KeyboardModifier.ControlModifier, "Ctrl"),
                  (Qt.KeyboardModifier.AltModifier, "Alt"), (Qt.KeyboardModifier.ShiftModifier, "Shift"),
                  (Qt.KeyboardModifier.MetaModifier, "Win")) if mods & flag]
        try:
            self.setText(canonical_hotkey("+".join(prefix + [suffix])))
        except ValueError:
            self.setPlaceholderText("Chữ/số cần thêm Ctrl, Alt hoặc Shift")
        event.accept()


class HotkeyDialog(QDialog):
    def __init__(self, title, value, controller, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Nhấn tổ hợp mới. Xóa để vùng không có phím riêng."))
        self.recorder = HotkeyRecorder(value, controller, self)
        layout.addWidget(self.recorder)
        remove = QPushButton("Xóa phím")
        remove.clicked.connect(self.recorder.clear)
        layout.addWidget(remove)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def done(self, result):
        self.recorder.clearFocus()
        super().done(result)
