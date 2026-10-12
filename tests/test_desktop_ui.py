import os
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PyQt6.QtCore import QObject,Qt,pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication,QPushButton,QLabel,QGroupBox,QToolButton
from ludiglot.core.story_catalog import StoryGroup
from ludiglot.ui.control_panel import ControlPanel
from ludiglot.ui.subtitle_overlay import SubtitleOverlay
from ludiglot.ui.game_picker import GamePicker,is_wuwa
from ludiglot.core.window_capture import WindowTarget


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class FakeController(QObject):
    status_changed=pyqtSignal(str); regions_changed=pyqtSignal(); data_changed=pyqtSignal()
    history_changed=pyqtSignal(); log_message=pyqtSignal(str)
    def __init__(self):
        super().__init__(); self.calls=[]
        self.raw={'auto_wuwa':{},'window_capture':{'mode':'window','target':{'title':'Wuthering Waves'}},'hotkey_capture':'Alt+W'}
        self.status='Sẵn sàng'; self.ready=True; self.session=SimpleNamespace(running=False)
        self.logs=['Sẵn sàng']; self.history=[{'text':'Chào Jinhsi'}]
        self.data_summary='100 câu Việt'; self.translation_version_summary='Bản dịch đang dùng: v3.0.16 · WuWa 3.7'
        self.app_version='1.0.2'; self.app_update_summary='Ossmoc Echo đã là bản mới nhất (v1.0.2)'; self.app_update_available=False
        self.has_english_data=True
        self.update_summary='Đã là bản mới nhất Wuthering Waves 3.7'; self.update_available=False
        self.story_options=(StoryGroup('Main_LahaiRoi_3_1','3.1 · Lahai-Roi','3.1','Main_LahaiRoi_3_1_1_1',10),
                            StoryGroup('Main_Mengzhou_3_6','3.6 · Mengzhou','3.6','Main_Mengzhou_3_6_20_1',12))
    def __getattr__(self,name):
        if name in {'choose_game_window','toggle_realtime','check_updates','download_update','download_app_update','rescan_game','show_usage_guide','import_translation','restore_translation','open_logs','save_hotkeys','update_runtime_settings'}:
            return lambda *a,**k:self.calls.append((name,a,k))
        raise AttributeError(name)
    def update_auto_settings(self,**values):
        self.calls.append(('update_auto_settings',(),values)); self.raw['auto_wuwa'].update(values); self.data_changed.emit()
    def choose_story(self,group):
        self.calls.append(('choose_story',(group,),{})); self.raw['auto_wuwa']['story_group']=group; self.data_changed.emit()


@pytest.fixture
def panel(app):
    widget=ControlPanel(FakeController()); yield widget
    widget.hide(); widget.deleteLater(); app.processEvents()


def test_panel_has_four_simple_pages_and_new_brand(panel):
    assert panel.pages.count()==4 and panel.navigation.count()==4
    assert tuple(panel.navigation.item(i).text() for i in range(4))==('Tổng quát','Tiến trình','Hiển thị','Dữ liệu')
    assert 'Ossmoc Echo' in panel.windowTitle()
    assert not panel.findChildren(QGroupBox)  # No nested cards within cards.


def test_page_headings_are_factual_without_marketing_or_id_introduction(panel):
    labels=[label.text() for label in panel.findChildren(QLabel)]
    headings=[label.text() for label in panel.findChildren(QLabel) if label.objectName()=='pageTitle']
    assert set(headings)=={'Tổng quát','Tiến trình','Chỉnh sửa hiển thị bản dịch','Dữ liệu bản dịch'}
    assert 'Chọn cốt truyện bạn đang ở hiện tại.' in labels
    for removed in ('Bản Việt, ngay trong game','Kết nối Wuthering Waves và bắt đầu chơi',
                    'Không cần biết ID câu thoại','Chữ dễ đọc, khung gọn','Echo tự tìm','Echo tự nhớ'):
        assert not any(removed in label for label in labels)


@pytest.mark.parametrize('removed',['Vùng OCR','Vùng đặc biệt','Ẩn/hiện phụ đề','Thu về khay','Xóa phụ đề',
    'Tiếp tục realtime','Hiện đường biên','Thoát Ludiglot','Profile bố cục','Hủy tác vụ','Vị trí khung dịch','Dùng màn hình','Khoanh khung','Dùng ID'])
def test_obsolete_actions_and_manual_regions_do_not_exist(panel,removed):
    texts=[w.text() for cls in (QPushButton,QLabel,QToolButton) for w in panel.findChildren(cls)]
    assert not any(removed.casefold() in text.casefold() for text in texts)
    assert not hasattr(panel,'region_table') and not hasattr(panel,'special_combo')


def test_connect_and_start_route_to_existing_controller(panel):
    panel.connect_button.click(); assert panel.controller.calls[-1]==('choose_game_window',(),{})
    panel.start_button.click(); assert panel.controller.calls[-1]==('toggle_realtime',(),{})
    assert 'Alt+W' in panel.start_button.text()


def test_start_requires_ready_selected_game(panel):
    panel.controller.raw['window_capture']={'mode':'desktop'}; panel.refresh_data()
    assert not panel.start_button.isEnabled()
    assert 'Chưa chọn game' in panel.capture_source_label.text()
    panel.controller.raw['window_capture']={'mode':'window','target':{'title':'Wuthering Waves'}}
    panel.controller.ready=False; panel.refresh_data(); assert not panel.start_button.isEnabled()
    panel.controller.ready=True; panel.refresh_data(); assert panel.start_button.isEnabled()
    panel.controller.session.running=True; panel.controller.status_changed.emit('Đang dịch')
    assert 'Tạm dừng dịch' in panel.start_button.text()


def test_one_hotkey_not_special_or_hide_and_runtime_setting_dispatch(panel):
    panel.capture_hotkey.setText('Ctrl+F8')
    next(button for button in panel.findChildren(QPushButton) if button.text()=='Lưu phím tắt').click()
    assert panel.controller.calls[-1]==('save_hotkeys',({'hotkey_capture':'Ctrl+F8'},),{})
    panel.interval_spin.setValue(800)
    assert panel.controller.calls[-1]==('update_runtime_settings',(),{'realtime_interval_ms':800})
    panel.controller.raw['hotkey_capture']='Ctrl+F8'; panel.refresh_data()
    assert 'Ctrl+F8' in panel.start_button.text() and 'Ctrl+F8' in panel.hotkey_tip.text()


def test_story_selection_has_human_labels_without_raw_ids(panel):
    assert panel.story_combo.itemText(0)=='Tự nhận diện (khuyên dùng)'
    assert panel.story_combo.itemText(2)=='3.6 · Mengzhou'
    assert 'Main_' not in '\n'.join(panel.story_combo.itemText(i) for i in range(panel.story_combo.count()))
    panel.story_combo.setCurrentIndex(2)
    assert panel.controller.calls[-1]==('choose_story',('Main_Mengzhou_3_6',),{})
    panel.story_combo.setCurrentIndex(0); assert panel.controller.calls[-1]==('choose_story',('',),{})


def test_memory_is_friendly_and_side_quest_can_be_restored(panel):
    memory={'group':'Side_DXTS','key':'Side_DXTS_4_5','kind':'side','excerpt':'Help me find the missing letter.'}
    panel.controller.raw['story_memory']=[memory]
    panel.controller.raw['auto_wuwa']['context_hint']=memory['key']; panel.refresh_data()
    assert 'Nhiệm vụ phụ' in panel.dialogue_context_label.text()
    assert 'Side_' not in panel.dialogue_context_label.text()
    item=panel.side_memories.item(0); assert 'missing letter' in item.text()
    panel.side_memories.itemClicked.emit(item)
    assert panel.controller.calls[-1]==('choose_story',('Side_DXTS',),{})
    assert panel.story_combo.currentText()=='Nhiệm vụ phụ đã chọn'


def test_refresh_does_not_dispatch_mutations_or_network_requests(panel):
    panel.controller.calls.clear()
    for _ in range(5): panel.refresh_data(); panel.navigation.setCurrentRow(2); panel.navigation.setCurrentRow(0)
    assert not panel.controller.calls


def test_single_style_form_changes_actual_auto_not_old_overlay(panel):
    assert not hasattr(panel,'_overlay_widgets')
    panel._auto_style_widgets['font_size'].setValue(35)
    assert panel.controller.calls[-1]==('update_auto_settings',(),{'font_size':35})
    panel._auto_style_widgets['choice_font_size'].setValue(22)
    assert panel.controller.calls[-1]==('update_auto_settings',(),{'choice_font_size':22})
    assert 'font-size: 32px' in panel.style_preview.styleSheet()
    panel.background_preset.setCurrentIndex(panel.background_preset.findData('none'))
    assert panel.controller.raw['auto_wuwa']['background_opacity']==0
    panel.background_preset.setCurrentIndex(panel.background_preset.findData('light'))
    assert panel.controller.raw['auto_wuwa']['text_color']=='#18212b'
    panel.background_preset.setCurrentIndex(panel.background_preset.findData('dark'))
    assert panel.controller.raw['auto_wuwa']['text_color']=='#ffffff'


def test_custom_colours_and_style_survive_refresh(panel):
    panel.controller.raw['auto_wuwa'].update(font_size=38,text_color='#ffeeaa',background_color='#334455',outline_width=3)
    panel.refresh_data(); assert panel._auto_style_widgets['font_size'].value()==38
    assert panel._auto_style_widgets['text_color'].text()=='#ffeeaa'
    assert panel.background_preset.currentData()=='custom'
    assert not panel.controller.calls


def test_update_is_short_and_download_requires_new_release(panel):
    panel.controller.update_summary='Có bản cập nhật mới'; panel.controller.update_available=True; panel.refresh_data()
    assert panel.overview_update_label.text()=='Có bản cập nhật mới' and panel.download_button.isEnabled()
    panel.controller.update_available=False; panel.refresh_data(); assert not panel.download_button.isEnabled()
    assert 'Bản dịch đang dùng: v3.0.16' in panel.translation_version_label.text()


def test_app_update_is_separate_and_unneeded_data_actions_removed(panel):
    texts=[w.text() for w in panel.findChildren(QPushButton)]
    assert 'Phục hồi bản trước' not in texts and 'Nhập bản dịch' not in texts
    assert 'Chuẩn bị / làm mới dữ liệu English' in texts
    assert 'v1.0.2' in panel.app_version_label.text()
    assert not panel.app_download_button.isEnabled()
    panel.controller.app_update_available=True
    panel.controller.app_update_summary='Có bản cập nhật Ossmoc Echo v1.0.3'; panel.refresh_data()
    assert panel.app_download_button.isEnabled()
    panel.app_download_button.click(); assert panel.controller.calls[-1][0]=='download_app_update'
    panel.check_update_button.click(); assert panel.controller.calls[-1]==('check_updates',(),{'force':True})
    panel.controller._checking_updates=True; panel.refresh_data()
    assert not panel.check_update_button.isEnabled() and not panel.app_download_button.isEnabled()


def test_english_help_visible_without_opening_diagnostics_and_blocks_missing_data(panel):
    assert 'Tác dụng:' in panel.english_data_note.text()
    assert 'Khi dùng:' in panel.english_data_note.text()
    assert 'không chọn file .exe hay .pak' in panel.english_data_note.text()
    assert 'không phải phiên bản Ossmoc Echo' in panel.english_data_note.text()
    assert not panel.data_tools_toggle.isChecked()
    panel.controller.has_english_data=False
    panel.controller.update_available=True; panel.refresh_data()
    assert not panel.download_button.isEnabled()
    assert 'Chưa có dữ liệu English' in panel.download_button.toolTip()
    assert 'chưa có' in panel.english_data_state.text()
    assert not panel.missing_data_note.isHidden()
    panel.prepare_english_button.click(); assert panel.controller.calls[-1][0]=='rescan_game'
    panel.controller.has_english_data=True; panel.refresh_data()
    assert panel.download_button.isEnabled() and panel.missing_data_note.isHidden()
    next(b for b in panel.findChildren(QPushButton) if b.text()=='Hướng dẫn sử dụng').click()
    assert panel.controller.calls[-1][0]=='show_usage_guide'


def test_logs_plain_history_copy_complete_and_advanced_collapsed(panel,app):
    panel.controller.log_message.emit('<b>private text</b>'); assert '<b>private text</b>' in panel.log_text.toPlainText()
    panel.history_list.itemDoubleClicked.emit(panel.history_list.item(0)); assert app.clipboard().text()=='Chào Jinhsi'
    assert not panel.options_toggle.isChecked() and not panel.data_tools_toggle.isChecked()


def test_close_hides_to_tray_silently_but_explicit_quit_works(panel,app,monkeypatch):
    panel.show(); event=QCloseEvent(); panel.closeEvent(event)
    assert not event.isAccepted() and not panel.isVisible()
    calls=[]; monkeypatch.setattr(QApplication,'quit',lambda *_:calls.append('quit'))
    panel._quit(); event=QCloseEvent(); panel.closeEvent(event)
    assert event.isAccepted() and calls==['quit']


def test_game_picker_only_shows_game_not_technical_window_inventory(app):
    game=WindowTarget(123,456,'Wuthering Waves','D:/game/Client.exe','GameWindow')
    other=WindowTarget(444,777,'C:/Users/Private/Rainmeter.ini','Rainmeter.exe','Other')
    picker=GamePicker([other,game],lambda:[game])
    assert picker.windows.count()==1 and picker.windows.item(0).text()=='Wuthering Waves'
    assert 'PID' not in picker.windows.item(0).text() and 'HWND' not in picker.windows.item(0).text()
    picker.select(); assert picker.selected_target==game
    picker.deleteLater()


def test_game_picker_empty_refresh_duplicate_windows_and_cancel(app):
    one=WindowTarget(1,1,'Wuthering Waves','Client.exe','Game')
    two=WindowTarget(2,2,'Wuthering Waves','Client.exe','Game')
    picker=GamePicker([],lambda:[one,two]); assert not picker.connect_button.isEnabled()
    picker.populate([one,two]); assert picker.windows.count()==2
    picker.windows.setCurrentRow(1); picker.select(); assert picker.selected_target==two
    assert not is_wuwa(WindowTarget(3,3,'Wuthering Waves','launcher.exe','Launcher'))
    picker.deleteLater()


@pytest.fixture
def overlay(app):
    widget=SubtitleOverlay({'x':20,'y':20,'width':500,'height':180}); yield widget
    widget.hide(); widget.deleteLater(); app.processEvents()


def test_overlay_does_not_reshow_user_hidden_output(overlay,app):
    overlay.set_content([{'target':'Chào Changli'}]); overlay.show_output(); assert overlay.isVisible()
    overlay.toggle_hidden(); overlay.set_content([{'target':'Chào Jinhsi'}]); overlay.show_output()
    assert not overlay.isVisible() and overlay.user_hidden
    overlay.toggle_hidden(); assert overlay.isVisible()
    overlay.set_content([]); assert not overlay.isVisible()


def test_background_alpha_is_separate_from_glyph_opacity(overlay,app):
    overlay.set_content([{'target':'Phụ đề tiếng Việt'}]); overlay.show_output(); app.processEvents()
    assert overlay.windowOpacity()==1.0 and overlay.grab().toImage().pixelColor(40,100).alpha()==0
    overlay.apply_settings({'background':'white','background_opacity':30})
    pixel=overlay.grab().toImage().pixelColor(40,100); assert 75<=pixel.alpha()<=77 and pixel.red()>250
    overlay.apply_settings({'background':'black','background_opacity':70})
    assert 177<=overlay.grab().toImage().pixelColor(40,100).alpha()<=180
    assert overlay.windowOpacity()==1.0


def test_edit_mode_is_only_interactive_mode_and_emits_geometry(overlay,app):
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    geometry=[]; overlay.geometry_changed.connect(geometry.append)
    overlay.set_edit_mode(True); assert not overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.setGeometry(30,40,450,160); overlay.set_edit_mode(False)
    assert geometry[-1]=={'x':30,'y':40,'width':450,'height':160} and not overlay.isVisible()


def test_offscreen_saved_geometry_is_recovered_and_invalid_style_is_safe(overlay,app):
    overlay.apply_settings({'x':100000,'y':-100000,'font_size':'bad','line_spacing':float('nan'),'text_color':'no colour','background':'something'})
    assert overlay.geometry().intersects(app.primaryScreen().availableGeometry())
    assert overlay.settings['font_size']==24 and overlay.settings['line_spacing']==1.15
    assert overlay.settings['text_color']=='#ffffff' and overlay.settings['background']=='none'


def test_wrapping_handles_vietnamese_newlines_and_long_text(overlay,app):
    overlay.set_content([{'target':'Chào Jinhsi và Changli!\n'+'Tiếng Việt có dấu '*200,'source':'Source'}]); overlay.show_output(); overlay.grab()
    assert overlay.content_truncated and len(overlay.blocks)==1
    overlay.apply_settings({'show_source':True,'font_size':12}); overlay.grab()


def test_unverified_capture_is_never_reported_safe(overlay,monkeypatch):
    overlay.set_content([{'target':'Test'}]); overlay.show_output(); monkeypatch.setattr(overlay,'_affinity_requested',False)
    assert overlay.verify_capture_exclusion() is False and overlay.capture_excluded is False
