"""Ossmoc Echo: automatic game captions, with a short guided desktop UI."""
from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QLineEdit, QMainWindow, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget, QFontComboBox)
from ludiglot.core.desktop_models import AUTO_DEFAULTS, HOTKEY_DEFAULTS
from ludiglot.core.story_catalog import dialogue_scene, story_label
from ludiglot.core.usage_help import ENGLISH_DATA_NOTE, MISSING_ENGLISH_DATA
from ludiglot.ui.hotkey_editor import HotkeyRecorder

PANEL_STYLESHEET = """
QMainWindow, QWidget { background: #151b25; color: #e8edf5; font-family: 'Segoe UI'; font-size: 10pt; }
QLabel { background: transparent; }
QLabel#pageTitle { font-size: 22pt; font-weight: 600; }
QLabel#muted { color: #9aa8bb; }
QLabel#step { color: #82aaff; font-size: 11pt; font-weight: 600; }
QFrame#sidebar { background: #0f141c; border-right: 1px solid #293244; }
QListWidget#navigation { border: none; background: transparent; outline: none; }
QListWidget#navigation::item { padding: 14px 14px; margin: 3px 8px; border-radius: 8px; color: #aebcd0; }
QListWidget#navigation::item:selected { background: #21334c; color: #a9c5ff; }
QListWidget#navigation::item:hover { background: #1d2838; }
QPushButton { padding: 9px 14px; border: 1px solid #39465b; border-radius: 7px; background: #232e40; }
QPushButton:hover { background: #2c3c54; border-color: #6489c9; }
QPushButton:disabled { color: #637085; background: #1c2531; border-color: #293344; }
QPushButton[primary="true"] { background: #5279cd; border-color: #7099ed; color: white; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #668cde; }
QPushButton[primary="true"]:disabled { background: #2c3c55; color: #8497b5; border-color: #344767; }
QLineEdit, QSpinBox, QComboBox, QTextEdit, QListWidget { background: #101721; border: 1px solid #344155; border-radius: 6px; padding: 7px; selection-background-color: #3c5d92; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #82aaff; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #1c2839; selection-background-color: #355787; }
QCheckBox { spacing: 9px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #5e7698; background: #101721; }
QCheckBox::indicator:checked { background: #82aaff; border-color: #adc9ff; }
QToolButton { border: none; color: #9ab8ed; padding: 8px 0; font-weight: 600; text-align: left; }
QScrollArea { border: none; }
QScrollBar:vertical { width: 9px; background: #151b25; }
QScrollBar::handle:vertical { background: #364861; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #e8edf5; background: #233047; border: 1px solid #5578ab; padding: 6px; }
"""


class ControlPanel(QMainWindow):
    PAGE_NAMES = ('Tổng quát', 'Tiến trình', 'Hiển thị', 'Dữ liệu')

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._refreshing = False
        self._story_signature = None
        self._auto_kind_checks = {}
        self._auto_style_widgets = {}
        self.setWindowTitle('Ossmoc Echo — Wuthering Waves')
        self.resize(1040,740); self.setMinimumSize(840,620)
        self.setStyleSheet(PANEL_STYLESHEET)
        root = QWidget(); self.setCentralWidget(root)
        outer = QHBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        sidebar = QFrame(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar); side.setContentsMargins(10,26,10,18)
        brand = QLabel('OSSMOC\nECHO'); brand.setStyleSheet('font-size: 22pt; font-weight: 700; color: #a9c5ff; padding: 0 12px;')
        side.addWidget(brand); side.addWidget(self._label('  Wuthering Waves',muted=True)); side.addSpacing(28)
        self.navigation = QListWidget(); self.navigation.setObjectName('navigation'); self.navigation.addItems(self.PAGE_NAMES)
        side.addWidget(self.navigation,1)
        self.hotkey_tip = self._label('',muted=True); self.hotkey_tip.setContentsMargins(12,8,8,0); side.addWidget(self.hotkey_tip)
        side.addWidget(self._label('  English → Tiếng Việt',muted=True)); outer.addWidget(sidebar)
        content = QVBoxLayout(); content.setContentsMargins(30,28,30,18); content.setSpacing(18)
        self.pages = QStackedWidget()
        for builder in (self._play_page,self._story_page,self._display_page,self._data_page):
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(builder()); self.pages.addWidget(scroll)
        content.addWidget(self.pages,1)
        self.status_label = self._label(controller.status,muted=True); content.addWidget(self.status_label)
        outer.addLayout(content,1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex); self.navigation.setCurrentRow(0)
        controller.status_changed.connect(self._on_status)
        controller.data_changed.connect(self.refresh_data)
        controller.regions_changed.connect(self.refresh_data)  # Existing signal also carries shortcut changes.
        controller.history_changed.connect(self.refresh_history)
        controller.log_message.connect(self._append_log)
        self.refresh_data(); self.refresh_history(); self._on_status(controller.status)

    @staticmethod
    def _label(text='',muted=False):
        label = QLabel(text); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
        if muted: label.setObjectName('muted')
        return label

    def _invoke(self, name, *args, **kwargs):
        try:
            return getattr(self.controller,name)(*args,**kwargs)
        except Exception as exc:
            self.status_label.setText(f'Chưa thực hiện được: {exc}')
            self.controller.log_message.emit(str(exc))

    def _button(self,text,name,*args,primary=False,**kwargs):
        button = QPushButton(text); button.setProperty('primary',primary)
        button.clicked.connect(lambda _:self._invoke(name,*args,**kwargs)); return button

    def _page(self,title,description=''):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,4,0); layout.setSpacing(18)
        heading = self._label(title); heading.setObjectName('pageTitle'); layout.addWidget(heading)
        if description:
            layout.addWidget(self._label(description,muted=True))
        return page,layout

    def _heading(self,layout,text):
        label = self._label(text); label.setObjectName('step'); layout.addWidget(label)

    def _fold(self,layout,title):
        toggle = QToolButton(); toggle.setText(title); toggle.setCheckable(True)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon); toggle.setArrowType(Qt.ArrowType.RightArrow)
        body = QWidget(); inner = QVBoxLayout(body); inner.setContentsMargins(0,5,0,5); inner.setSpacing(14)
        body.hide()
        toggle.toggled.connect(lambda opened:(body.setVisible(opened),toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)))
        layout.addWidget(toggle); layout.addWidget(body); return inner,toggle

    def _play_page(self):
        page,layout = self._page('Tổng quát')
        self._heading(layout,'01  Chọn game')
        row = QHBoxLayout(); self.capture_source_label = self._label('Chưa kết nối game')
        row.addWidget(self.capture_source_label,1)
        self.connect_button = self._button('Chọn game','choose_game_window'); row.addWidget(self.connect_button); layout.addLayout(row)
        layout.addWidget(self._label('Chỉ đọc cửa sổ game đã chọn.',muted=True))
        layout.addWidget(self._label('Tên nhân vật của bạn'))
        row = QHBoxLayout()
        self.player_name_edit = QLineEdit(); self.player_name_edit.setMaxLength(40)
        self.player_name_edit.setPlaceholderText('Nhập tên nhân vật trong game')
        self.player_name_edit.setToolTip('Thay mã tên nhân vật trong bản dịch. Để trống: dùng tên nhận diện được, nếu không có thì dùng Rover.')
        row.addWidget(self.player_name_edit, 1)
        self.save_player_name_button = QPushButton('Lưu tên')
        self.save_player_name_button.clicked.connect(self._save_player_name)
        self.player_name_edit.returnPressed.connect(self._save_player_name)
        row.addWidget(self.save_player_name_button); layout.addLayout(row)
        self.missing_data_note = self._label(MISSING_ENGLISH_DATA,muted=True)
        layout.addWidget(self.missing_data_note)
        self._heading(layout,'02  Bắt đầu dịch')
        self.runtime_summary = self._label(); layout.addWidget(self.runtime_summary)
        self.start_button = self._button('Bắt đầu dịch','toggle_realtime',primary=True); self.start_button.setMinimumHeight(48)
        layout.addWidget(self.start_button)
        self.screenshot_button = self._button('Chụp kèm bản dịch','capture_translated_screenshot')
        self.screenshot_button.setToolTip('Ảnh tự copy vào clipboard để Ctrl+V; PNG được lưu thêm trong userdata/screenshots cạnh app. Không cần dừng dịch.')
        layout.addWidget(self.screenshot_button)
        inner,self.options_toggle = self._fold(layout,'Tùy chọn dịch')
        types = QGridLayout()
        for number,(key,label) in enumerate((('dialogue','Hội thoại'),('walking','Thoại khi di chuyển'),('choices','Đáp án'),
                ('document','Nhật ký / tài liệu'),('glossary','Giải thích thuật ngữ'),('transition','Chữ chuyển cảnh'))):
            check = QCheckBox(label); check.toggled.connect(lambda value,k=key:self._auto_change(**{k:value}))
            self._auto_kind_checks[key]=check; types.addWidget(check,number//2,number%2)
        inner.addLayout(types)
        self.auto_light_ocr_check = QCheckBox('Đọc bổ sung khi chữ khó nhận diện')
        self.auto_light_ocr_check.setToolTip('Dùng model nhẹ chạy trên máy, không gửi ảnh đi. Có thể làm một số lượt dịch chậm hơn.')
        self.auto_light_ocr_check.toggled.connect(lambda value:self._auto_change(light_ocr=value)); inner.addWidget(self.auto_light_ocr_check)
        form = QFormLayout()
        self.interval_spin = QSpinBox(); self.interval_spin.setRange(100,10000); self.interval_spin.setSingleStep(50); self.interval_spin.setSuffix(' ms')
        self.interval_spin.valueChanged.connect(lambda value:self._invoke('update_runtime_settings',realtime_interval_ms=value) if not self._refreshing else None)
        form.addRow('Khoảng kiểm tra ảnh',self.interval_spin)
        self.capture_hotkey = HotkeyRecorder(controller=self.controller); form.addRow('Bắt đầu / tạm dừng',self.capture_hotkey)
        self.screenshot_hotkey = HotkeyRecorder(controller=self.controller); form.addRow('Chụp kèm bản dịch',self.screenshot_hotkey)
        inner.addLayout(form)
        save = QPushButton('Lưu phím tắt'); save.clicked.connect(lambda _:self._invoke('save_hotkeys',{
            'hotkey_capture':self.capture_hotkey.text(),'hotkey_screenshot':self.screenshot_hotkey.text()})); inner.addWidget(save)
        layout.addStretch(); return page

    def _story_page(self):
        page,layout = self._page('Tiến trình', 'Chọn cốt truyện bạn đang ở hiện tại.')
        self._heading(layout,'Cốt truyện chính')
        self.story_combo = QComboBox(); self.story_combo.currentIndexChanged.connect(self._story_changed)
        self.story_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.story_combo.setMinimumContentsLength(28)
        layout.addWidget(self.story_combo)
        self.story_combo.setToolTip('Các mốc cốt truyện có trong dữ liệu, không phải phiên bản game đang cài.')
        layout.addWidget(self._label('Thống kê ghép không tính tên nhân vật và cảnh không có văn bản English.',muted=True))
        self._heading(layout,'Đoạn gần nhất')
        self.dialogue_context_label = self._label(); layout.addWidget(self.dialogue_context_label)
        self._heading(layout,'Nhiệm vụ phụ gần đây')
        layout.addWidget(self._label('Chọn đoạn đã lưu để tiếp tục.',muted=True))
        self.side_memories = QListWidget(); self.side_memories.setMinimumHeight(150)
        self.side_memories.itemClicked.connect(lambda item:self._invoke('choose_story',item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.side_memories)
        self.side_empty = self._label('Chưa ghi nhớ nhiệm vụ phụ nào.',muted=True); layout.addWidget(self.side_empty)
        layout.addStretch(); return page

    def _display_page(self):
        page,layout = self._page('Chỉnh sửa hiển thị bản dịch')
        self.style_preview = QLabel('Chào mừng đến với Wuthering Waves.\nCùng tiếp tục câu chuyện nhé.'); self.style_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.style_preview.setMinimumHeight(94); layout.addWidget(self.style_preview)
        form = QFormLayout(); form.setVerticalSpacing(14)
        font = QFontComboBox(); font.currentFontChanged.connect(lambda value:self._auto_change(font_family=value.family()))
        self._auto_style_widgets['font_family']=font; form.addRow('Phông chữ',font)
        for key,label,low,high in (('font_size','Cỡ chữ hội thoại / thông tin',18,64),('choice_font_size','Cỡ chữ đáp án',18,64),('outline_width','Độ dày viền',0,8),('background_opacity','Độ đậm nền',0,100)):
            spin = QSpinBox(); spin.setRange(low,high); spin.setSuffix(' %' if key=='background_opacity' else ' px')
            spin.valueChanged.connect(lambda value,k=key:self._auto_change(**{k:value})); self._auto_style_widgets[key]=spin; form.addRow(label,spin)
        self.background_preset = QComboBox()
        for label,key in (('Xám khói','dark'),('Nền sáng','light'),('Chỉ chữ, không nền','none'),('Màu riêng','custom')):
            self.background_preset.addItem(label,key)
        self.background_preset.currentIndexChanged.connect(self._preset_changed); form.addRow('Kiểu nền',self.background_preset)
        for key,label in (('text_color','Màu chữ'),('outline_color','Màu viền'),('background_color','Màu nền')):
            button = QPushButton(); button.clicked.connect(lambda _,k=key:self._pick_color(k))
            self._auto_style_widgets[key]=button; form.addRow(label,button)
        layout.addLayout(form)
        layout.addStretch(); return page

    def _data_page(self):
        page,layout = self._page('Dữ liệu bản dịch')
        self.app_version_label = self._label(); layout.addWidget(self.app_version_label)
        self.app_update_label = self._label(); layout.addWidget(self.app_update_label)
        self.translation_version_label = self._label(); layout.addWidget(self.translation_version_label)
        self.overview_update_label = self._label(); layout.addWidget(self.overview_update_label)
        row = QHBoxLayout(); self.check_update_button = self._button('Kiểm tra cập nhật','check_updates',force=True); row.addWidget(self.check_update_button)
        self.app_download_button = self._button('Cập nhật Ossmoc Echo','download_app_update'); row.addWidget(self.app_download_button)
        self.download_button = self._button('Cập nhật bản dịch','download_update',primary=True); row.addWidget(self.download_button); row.addStretch(); layout.addLayout(row)
        layout.addWidget(self._label('Kiểm tra app và bản dịch một lần khi mở; chỉ tải khi bạn chọn cập nhật.',muted=True))
        self.english_data_state = self._label(); layout.addWidget(self.english_data_state)
        row = QHBoxLayout()
        self.prepare_english_button = self._button('Chuẩn bị / làm mới dữ liệu English','rescan_game')
        row.addWidget(self.prepare_english_button)
        row.addWidget(self._button('Hướng dẫn sử dụng','show_usage_guide')); row.addStretch(); layout.addLayout(row)
        self.english_data_note = self._label(ENGLISH_DATA_NOTE,muted=True); layout.addWidget(self.english_data_note)
        inner,self.data_tools_toggle = self._fold(layout,'Chẩn đoán và lịch sử')
        self.data_text = QTextEdit(); self.data_text.setReadOnly(True); self.data_text.setMaximumHeight(150); inner.addWidget(self.data_text)
        inner.addWidget(self._label('Lịch sử trong phiên — bấm đúp để sao chép',muted=True))
        self.history_list = QListWidget(); self.history_list.setMaximumHeight(140)
        self.history_list.itemDoubleClicked.connect(lambda item:QApplication.clipboard().setText(item.data(Qt.ItemDataRole.UserRole) or '')); inner.addWidget(self.history_list)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True); self.log_text.document().setMaximumBlockCount(300); self.log_text.setMaximumHeight(130)
        self.log_text.setPlainText('\n'.join(self.controller.logs[-300:])); inner.addWidget(self.log_text)
        inner.addWidget(self._button('Mở nhật ký','open_logs'))
        layout.addStretch(); return page

    def _auto_change(self,**values):
        if not self._refreshing:
            self._invoke('update_auto_settings',**values)
            self._preview_style()

    def _pick_color(self,key):
        settings = {**AUTO_DEFAULTS,**self.controller.raw.get('auto_wuwa',{})}
        color = QColorDialog.getColor(QColor(settings[key]),self,'Chọn màu')
        if color.isValid(): self._auto_change(**{key:color.name()})

    def _preset_changed(self):
        if self._refreshing: return
        choice = self.background_preset.currentData()
        settings = {**AUTO_DEFAULTS,**self.controller.raw.get('auto_wuwa',{})}
        changes = {'background_opacity':0} if choice=='none' else {'background_color':'#3b3e43','background_opacity':88} if choice=='dark' else {'background_color':'#f0f1f4','background_opacity':90} if choice=='light' else {}
        if choice=='light' and settings['text_color']=='#ffffff': changes.update(text_color='#18212b',outline_color='#ffffff')
        elif choice in ('none','dark') and settings['text_color']=='#18212b' and settings['outline_color']=='#ffffff': changes.update(text_color='#ffffff',outline_color='#101215')
        if changes: self._auto_change(**changes)

    def _preview_style(self):
        settings = {**AUTO_DEFAULTS,**self.controller.raw.get('auto_wuwa',{})}
        color = QColor(settings['background_color'])
        font = QFont(settings['font_family']); font.setPixelSize(min(32,int(settings['font_size'])))
        self.style_preview.setFont(font)
        family=str(settings['font_family']).replace("'",'').replace('"','')
        self.style_preview.setStyleSheet(f'font-family: "{family}"; font-size: {font.pixelSize()}px; color: {settings["text_color"]}; background: rgba({color.red()},{color.green()},{color.blue()},{round(int(settings["background_opacity"])*2.55)}); border-radius: 8px; padding: 12px;')

    def _save_player_name(self):
        from ludiglot.core.player_names import normalize_player_name
        name = normalize_player_name(self.player_name_edit.text())
        self._invoke('update_auto_settings', player_name=name)
        self.player_name_edit.setText(self.controller.raw.get('auto_wuwa', {}).get('player_name', ''))
        self.player_name_edit.setModified(False)

    def _story_changed(self):
        if not self._refreshing:
            self._invoke('choose_story',self.story_combo.currentData() or '')

    def refresh_data(self):
        self._refreshing=True
        auto = {**AUTO_DEFAULTS,**self.controller.raw.get('auto_wuwa',{})}
        # Context/status refreshes must not erase a name being edited.
        if not self.player_name_edit.isModified():
            self.player_name_edit.setText(auto.get('player_name', ''))
        capture = self.controller.raw.get('window_capture',{})
        connected = capture.get('mode')=='window' and bool(capture.get('target'))
        self.capture_source_label.setText('Wuthering Waves · đã chọn' if connected else 'Chưa chọn game.')
        self.connect_button.setText('Đổi cửa sổ game' if connected else 'Chọn game')
        for key,widget in {**self._auto_kind_checks,'light_ocr':self.auto_light_ocr_check}.items():
            widget.setChecked(bool(auto[key]))
        for key,widget in self._auto_style_widgets.items():
            if isinstance(widget,QFontComboBox): widget.setCurrentFont(QFont(auto[key]))
            elif isinstance(widget,QSpinBox): widget.setValue(int(auto[key]))
            else: widget.setText(str(auto[key])); widget.setStyleSheet(f'border-left: 12px solid {auto[key]};')
        preset = 'none' if not auto['background_opacity'] else 'dark' if auto['background_color']=='#3b3e43' else 'light' if auto['background_color']=='#f0f1f4' else 'custom'
        self.background_preset.setCurrentIndex(self.background_preset.findData(preset))
        self.interval_spin.setValue(int(self.controller.raw.get('realtime_interval_ms',350)))
        key = self.controller.raw.get('hotkey_capture',HOTKEY_DEFAULTS['hotkey_capture']) or ''
        if not self.capture_hotkey.hasFocus(): self.capture_hotkey.setText(key)
        shot = self.controller.raw.get('hotkey_screenshot',HOTKEY_DEFAULTS['hotkey_screenshot']) or ''
        if not self.screenshot_hotkey.hasFocus(): self.screenshot_hotkey.setText(shot)
        self.hotkey_tip.setText((key+' · ' if key else '')+'Bắt đầu / tạm dừng\n'+
                               (shot+' · ' if shot else '')+'Chụp kèm bản dịch')
        options = getattr(self.controller,'story_options',())
        selected_side = auto.get('story_group','') if str(auto.get('story_group','')).startswith('Side_') else ''
        signature = (tuple((option.key,option.label,option.count,getattr(option,'source_count',None),
                            getattr(option,'target_count',None),getattr(option,'indexed_count',None)) for option in options),selected_side)
        if signature!=self._story_signature:
            self.story_combo.clear(); self.story_combo.addItem('Tự nhận diện (khuyên dùng)','')
            for option in options:
                source_count=getattr(option,'source_count',None)
                label=option.label+(f' · Đã ghép {option.count}/{source_count} mục nguồn' if source_count is not None else '')
                self.story_combo.addItem(label,option.key)
                self.story_combo.setItemData(self.story_combo.count()-1,label,Qt.ItemDataRole.ToolTipRole)
                if not option.seed:
                    self.story_combo.model().item(self.story_combo.count()-1).setEnabled(False)
            if selected_side: self.story_combo.addItem('Nhiệm vụ phụ đã chọn',selected_side)
            self._story_signature=signature
        selected = self.story_combo.findData(auto.get('story_group',''))
        self.story_combo.setCurrentIndex(max(0,selected))
        hint = auto.get('context_hint',''); scene = dialogue_scene(hint)
        memories = [memory for memory in self.controller.raw.get('story_memory',[]) if isinstance(memory,dict)]
        memory = next((memory for memory in memories if memory.get('key')==hint),None)
        group=memory.get('group') if memory else scene[0] if scene else ''
        label = next((option.label for option in options if option.key==group),story_label(scene[0])[0] if scene else 'Chưa có đoạn đã ghi nhớ.')
        self.dialogue_context_label.setText(label + ('\n“'+str(memory.get('excerpt',''))+'”' if memory and memory.get('excerpt') else ''))
        self.side_memories.clear()
        for memory in memories:
            if memory.get('kind')=='side':
                item = QListWidgetItem('Nhiệm vụ phụ · “'+str(memory.get('excerpt') or 'Đoạn đã ghi nhớ')+'”')
                item.setData(Qt.ItemDataRole.UserRole,memory.get('group')); self.side_memories.addItem(item)
        self.side_empty.setVisible(self.side_memories.count()==0); self.side_memories.setVisible(self.side_memories.count()>0)
        self.translation_version_label.setText(getattr(self.controller,'translation_version_summary','Chưa xác định bản dịch đang dùng.'))
        self.app_version_label.setText('Ossmoc Echo · v'+str(getattr(self.controller,'app_version','chưa xác định')))
        self.app_update_label.setText(getattr(self.controller,'app_update_summary','Chưa kiểm tra cập nhật app.'))
        self.overview_update_label.setText(self.controller.update_summary)
        busy = bool(getattr(self.controller,'_maintenance',False) or getattr(self.controller,'_checking_updates',False))
        has_data = bool(getattr(self.controller,'has_english_data',False))
        self.missing_data_note.setVisible(not has_data)
        self.english_data_state.setText('Dữ liệu English: đã có.' if has_data else 'Dữ liệu English: chưa có — hãy chuẩn bị trước khi cập nhật bản dịch.')
        self.download_button.setEnabled(bool(getattr(self.controller,'update_available',False)) and has_data and not busy)
        self.download_button.setToolTip('' if has_data else MISSING_ENGLISH_DATA)
        self.prepare_english_button.setEnabled(not busy)
        self.app_download_button.setEnabled(bool(getattr(self.controller,'app_update_available',False)) and not busy)
        self.check_update_button.setEnabled(not busy)
        self.data_text.setPlainText(str(self.controller.data_summary))
        self._refreshing=False; self._preview_style(); self._on_status(self.controller.status)

    def _on_status(self,message):
        self.status_label.setText(message)
        running = bool(getattr(self.controller.session,'running',False))
        self.runtime_summary.setText('Đang dịch.' if running else 'Chưa bắt đầu.')
        key = self.controller.raw.get('hotkey_capture','')
        self.start_button.setText(('Tạm dừng dịch' if running else 'Bắt đầu dịch')+(' · '+key if key else ''))
        capture = self.controller.raw.get('window_capture',{})
        self.start_button.setEnabled(bool(self.controller.ready) and capture.get('mode')=='window' and bool(capture.get('target')) and not getattr(self.controller,'_maintenance',False))
        shot = self.controller.raw.get('hotkey_screenshot',HOTKEY_DEFAULTS['hotkey_screenshot']) or ''
        self.screenshot_button.setText('Chụp kèm bản dịch'+(' · '+shot if shot else ''))
        self.screenshot_button.setEnabled(capture.get('mode')=='window' and bool(capture.get('target')))

    def refresh_history(self):
        self.history_list.clear()
        for entry in reversed(self.controller.history[-100:]):
            text = str(entry.get('text') or entry.get('target') or '')
            item = QListWidgetItem(text.replace('\n','  ')[:180]); item.setData(Qt.ItemDataRole.UserRole,text); self.history_list.addItem(item)

    def _append_log(self,message):
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        self.log_text.insertPlainText(str(message)+'\n')

    def closeEvent(self,event):
        if getattr(self,'_allow_close',False): event.accept()
        else: event.ignore(); self.hide()

    def _quit(self):
        self._allow_close=True; QApplication.instance().quit()
