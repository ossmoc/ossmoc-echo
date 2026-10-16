import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from ludiglot.core.auto_wuwa import AutoDocument, AutoTextBlock
from ludiglot.core.desktop_models import AUTO_DEFAULTS
from ludiglot.ui import auto_overlay as module
from ludiglot.ui.hotkey_editor import HotkeyRecorder, canonical_hotkey


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(app, monkeypatch):
    monkeypatch.setattr(module, "logical_frame", lambda bounds: (0,0,bounds["width"],bounds["height"]))
    window=module.AutoInlineOverlay()
    yield window
    window.hide(); window.deleteLater(); app.processEvents()


def test_dialogue_expands_only_horizontally_and_choices_stay_separate(overlay):
    block=AutoTextBlock("dialogue",(480,890,970,72),"English","Bản dịch Việt ngắn",("db","Side_1"))
    frame={"left":0,"top":0,"width":1920,"height":1080}
    overlay.set_frame(frame,(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    assert area.top()==890
    assert area.width()>970
    assert area.width()<1030
    choice=replace(block,kind="choices",bounds=(1288,650,350,56))
    area,font=overlay._layout(choice)
    assert area.left()==1288 and area.top()>=644
    assert area.bottom()<730
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput


def test_overlong_response_has_no_inline_mask_and_is_in_overflow(overlay):
    block=AutoTextBlock("choices",(1288,650,350,56),"English","Một câu Việt rất dài "*100,("db","Side_1"),2)
    overlay.set_frame({"left":0,"top":0,"width":1920,"height":1080},(block,),AUTO_DEFAULTS)
    assert overlay._layout(block) is None
    assert overlay.overflow == [block]


def test_answer_can_wrap_vietnamese_at_selected_font_inside_button(overlay):
    block = AutoTextBlock('choices', (1288,500,340,56), 'This is my longtime partner.',
        'Đây là người bạn đã luôn đồng hành cùng tôi.', ('db','Main_1'))
    next_block = replace(block, bounds=(1288,580,340,24), target='Đáp án tiếp theo')
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080}, (block,next_block), {**AUTO_DEFAULTS,'choice_font_size':18})
    area,font=overlay._layout(block)
    assert area.top()>=494 and area.bottom()<=next_block.bounds[1]-12
    assert area.left()==1288 and area.right()<=1690
    assert font.pixelSize()==18
    overlay.grab()
    assert not overlay.content_truncated and not overlay.overflow


def test_identical_final_blocks_do_not_change_signature(overlay):
    block=AutoTextBlock("dialogue",(480,890,970,72),"English","Bản Việt",("db","Side_1"))
    bounds={"left":0,"top":0,"width":1920,"height":1080}
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS); signature=overlay.content_signature
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    assert overlay.content_signature is signature
    overlay.set_frame(bounds, (replace(block,source="Noisy OCR but same Vietnamese output"),), AUTO_DEFAULTS)
    assert overlay.content_signature is signature


def test_outline_does_not_paint_over_white_glyph_fill(overlay, app):
    block = AutoTextBlock("dialogue", (480,890,970,72), "English", "Chữ Việt", ("db","Side_1"))
    overlay.set_frame({"left":0,"top":0,"width":1920,"height":1080}, (block,), AUTO_DEFAULTS)
    image = overlay.grab().toImage()
    # QPixmap.grab returns device pixels; the overlay geometry is logical.
    dpr = image.devicePixelRatio()
    assert any(image.pixelColor(x,y).red() > 230 and image.pixelColor(x,y).alpha() > 230
               for y in range(round(890*dpr),round(960*dpr))
               for x in range(round(850*dpr),round(1070*dpr)))


def test_reader_keeps_scroll_and_stays_dismissed_until_panel_closes(app,monkeypatch):
    monkeypatch.setattr(module,"logical_frame",lambda bounds:(0,0,1920,1080))
    reader=module.DocumentReader()
    # Avoid native capture probes in unit tests.
    reader._capture_tested=True
    doc=AutoDocument(("db","InfoDisplay_1"),"Tiêu đề","Một dòng Việt\n"*300)
    frame={"left":0,"top":0,"width":1920,"height":1080}
    reader.set_document(doc,frame,AUTO_DEFAULTS); app.processEvents()
    reader.text.verticalScrollBar().setValue(200)
    reader.set_document(doc,frame,AUTO_DEFAULTS)
    assert reader.text.verticalScrollBar().value()==200
    assert reader.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    reader.close(); reader.set_document(doc,frame,AUTO_DEFAULTS)
    assert not reader.isVisible()
    reader.set_document(None,frame,AUTO_DEFAULTS)
    reader.set_document(doc,frame,AUTO_DEFAULTS)
    assert reader.isVisible()
    reader.hide(); reader.deleteLater(); app.processEvents()


@pytest.mark.parametrize("raw,expected",[("shift+alt+r","Alt+Shift+R"),("CONTROL+f4","Ctrl+F4"),("", ""),("F8","F8")])
def test_shortcut_normalization(raw,expected):
    assert canonical_hotkey(raw)==expected


@pytest.mark.parametrize("raw",["alt+r,alt+e","R","alt+escape","alt+đ"])
def test_recorder_rejects_multistep_or_unsupported_shortcut(raw):
    with pytest.raises(ValueError):
        canonical_hotkey(raw)


def test_recorder_records_pressed_combination_not_text_input(app):
    recorder=HotkeyRecorder()
    event=QKeyEvent(QKeyEvent.Type.KeyPress,Qt.Key.Key_R,Qt.KeyboardModifier.AltModifier|Qt.KeyboardModifier.ShiftModifier,"R")
    recorder.keyPressEvent(event)
    assert recorder.text()=="Alt+Shift+R"
    recorder.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress,Qt.Key.Key_Delete,Qt.KeyboardModifier.NoModifier))
    assert recorder.text()==""


def test_short_dialogue_background_covers_english_without_full_width_bar(overlay):
    block=AutoTextBlock('dialogue',(636,891,621,31),'English','Qingxiao tiên tử? Tiểu—Qing—Xiao—',('db','Main_1'))
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    # The offscreen Windows plugin can use a wide fallback font; either way
    # this must be compact, not the old 1728px fixed-width strip.
    assert area.width()<1200 and area.left()<=636 and area.right()>=1257
    assert area.top()==891 and area.bottom()<960 and font.pixelSize()==28
    assert area.center().x()==960
    assert not overlay.auto_settings['show_bounds']
    assert overlay.auto_settings['background_opacity']==88


def test_compact_dialogue_expands_for_vietnamese_and_falls_back_if_too_long(overlay):
    block=AutoTextBlock('dialogue',(810,891,300,32),'English','Đây là một câu dịch dài hơn câu English gốc.',('db','Main_1'))
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    assert 320<area.width()<1300 and area.top()==891
    huge=replace(block,target='Câu quá dài '*200)
    assert overlay._layout(huge) is None


def test_two_line_dialogue_wraps_in_original_width_before_expanding(overlay):
    from PyQt6.QtGui import QFontMetricsF
    block=AutoTextBlock('dialogue',(500,890,914,73),'English',
        'Qingxiao Tư Kỵ, nếu một ngày Huyền Phương Thành gặp biến cố, ta mong ngươi sẽ rút kiếm vì quân dân nơi đây.',('db','Main_1'))
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    assert area.width()>=940 and area.top()==890 and area.bottom()<=999
    assert area.center().x()==960 and font.pixelSize()==28
    lines=list(overlay._paragraph_lines(block.target,font,area.width()-8))
    assert len(lines)>=2 and len(lines)*QFontMetricsF(font).height()<=area.height()-2


def test_partial_context_translation_uses_compact_real_caption_bounds(overlay):
    block=AutoTextBlock('dialogue',(500,890,400,31),'A recognized fragment','Bản Việt toàn câu',('db','Main_1'),partial=True)
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    area, font = overlay._layout(block)
    assert area.top()==890 and area.bottom()<=930
    assert area.left()<=500 and area.right()>=900
    assert area.width()==940 and area.center().x()==960
    assert not overlay.overflow


@pytest.mark.parametrize('factor', [1,1.25,1.5])
def test_clipped_full_translation_stays_below_name_covers_slot_and_never_overflows(overlay, factor):
    from PyQt6.QtGui import QFontMetricsF
    block=AutoTextBlock('dialogue',tuple(round(v*factor) for v in (504,877,902,117)),
        'Visible lower lines',
        '“Người Chấp Hoa”, “Đại Anh Hùng Kim Châu”, “Tia Chớp Đen”, “Người Đội Vòng Nguyệt Quế”, “Vua Anh Hùng”, “Kỵ Sĩ Hư Đản Trùng”… Phủ Ngoại Sự chỉ ghi chép sơ lược những chiến tích này của cậu.',
        ('db','Main_1'),partial=True)
    overlay.set_frame({'left':0,'top':0,'width':round(1920*factor),'height':round(1080*factor)},(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    assert area.top()>=847*factor and area.bottom()<=1000*factor
    assert area.left()<=504*factor and area.right()>=1406*factor
    lines=list(overlay._paragraph_lines(block.target,font,area.width()-8*factor))
    assert len(lines)*QFontMetricsF(font).height()<=area.height()-2*factor
    assert not overlay.overflow


def test_long_dialogue_wraps_at_fixed_size_instead_of_moving_up(overlay):
    block=AutoTextBlock('dialogue',(600,890,720,32),'English',
        'Đây là một đoạn hội thoại dài cần giữ dưới màn hình và xuống dòng đầy đủ. '*2,('db','Main_1'))
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    assert area.top()==890 and area.bottom()<=1000
    assert area.left()>=96 and area.right()<=1824
    assert font.pixelSize()==28
    assert not overlay.overflow


@pytest.mark.parametrize('dpi_scale', [1, 1.25, 1.5])
def test_bottom_dialogue_fit_and_painter_share_logical_dpi_padding(overlay, monkeypatch, dpi_scale):
    # Simulate physical-to-logical mapping; not a native monitor/DPI test.
    monkeypatch.setattr(module, 'logical_frame', lambda bounds: (0, 0,
        round(bounds['width']/dpi_scale), round(bounds['height']/dpi_scale)))
    block = AutoTextBlock('dialogue', (504, 877, 902, 117), 'Visible lower lines',
        'Đây là một đoạn hội thoại dài cần giữ dưới màn hình và xuống dòng đầy đủ. '*3,
        ('db', 'Main_1'), partial=True)
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080}, (block,), AUTO_DEFAULTS)
    assert not overlay.overflow
    overlay.grab()  # Paint the complete paragraph, not just the fit estimate.
    assert not overlay.content_truncated
    area, font = overlay._layout(block)
    assert area.top()*dpi_scale >= 847
    assert area.bottom()*dpi_scale <= 1000
    assert font.pixelSize()==round(28/dpi_scale)


@pytest.mark.parametrize('bounds',[(-5,890,900,32),(500,500,900,32),(500,890,0,32),(500,890,1800,32)])
def test_partial_caption_cannot_fabricate_a_valid_detection_from_bad_geometry(overlay, bounds):
    block=AutoTextBlock('dialogue',bounds,'English','Bản Việt',('db','Main_1'),partial=True)
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    assert overlay._layout(block) is None


def test_short_caption_ink_height_has_room_for_vietnamese_descenders(overlay):
    block=AutoTextBlock('dialogue',(809,891,301,24),'So the time has come.','Đã đến lúc rồi sao?',('db','Main_1'))
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    assert area.top()==891 and area.bottom()<=960 and font.pixelSize()==28


def test_glossary_masks_real_text_only_and_fits_without_covering_close_icon(overlay):
    block = AutoTextBlock('glossary',(614,327,700,241),'English',
        'Cơ quan làm việc đặt tại Tỉnh Sạn, có Giám Vận Sứ phụ trách kiểm đếm tình hình vật tư và hàng hóa vào thành, lại có Tuần Phương Sứ phụ trách kiểm tra thân phận người vào thành.',('db','Term1_Desc'),1)
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    area, font = overlay._layout(block)
    assert area.left()<=614 and area.top()<=327 and area.right()>=1314 and area.bottom()>=568
    assert area.right()<1480 and area.bottom()<600  # Close icon/buttons untouched.
    assert not overlay.overflow


@pytest.mark.parametrize('mode,y', [('cinematic',890),('walking',797)])
def test_verified_sentence_locks_text_font_wrap_and_ignores_ocr_jitter(overlay, mode, y):
    block = AutoTextBlock('dialogue',(600,y,720,32),'English',
        'Đây là một câu tiếng Việt được giữ ổn định khi OCR thay đổi.',('db','Main_1'),caption_mode=mode)
    bounds = {'left':0,'top':0,'width':1920,'height':1080}
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    layout=area.getRect(),font.pixelSize()
    signature=overlay.content_signature
    for rect in ((602,y+1,715,31),(604,y+2,700,30),(605,y,695,32)):
        noisy=replace(block,bounds=rect,source='Noisy OCR')
        overlay.set_frame(bounds,(noisy,),AUTO_DEFAULTS)
        text,font=overlay._layout(noisy)
        assert (text.getRect(),font.pixelSize())==layout
        assert overlay.content_signature is signature


def test_mask_expands_only_after_confirmation_without_reflow_or_shrink(overlay):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('dialogue',(650,890,620,32),'English','Bản Việt ổn định',('db','Main_1'))
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    area,font=overlay._layout(block)
    text=area.getRect(),font.pixelSize()
    mask=overlay._mask_rect(block).getRect()
    wide=replace(block,bounds=(500,880,920,70))
    overlay.set_frame(bounds,(wide,),AUTO_DEFAULTS)
    assert overlay._mask_rect(wide).getRect()==mask
    overlay.set_frame(bounds,(replace(wide,bounds=(501,880,918,70)),),AUTO_DEFAULTS)
    expanded=overlay._mask_rect(wide)
    assert expanded.left()<=510 and expanded.right()>=1410 and expanded.bottom()>=950
    assert (overlay._layout(wide)[0].getRect(),overlay._layout(wide)[1].pixelSize())==text
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    assert overlay._mask_rect(block).getRect()==expanded.getRect()
    overlay.grab()
    assert not overlay.content_truncated


def test_partial_full_toggle_cannot_rewrap_same_verified_sentence(overlay):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('dialogue',(650,890,620,32),'English','Bản Việt toàn câu',('db','Main_1'))
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    original=overlay._layout(block)[0].getRect()
    fragment=replace(block,partial=True,bounds=(850,935,200,30),source='Visible tail')
    overlay.set_frame(bounds,(fragment,),AUTO_DEFAULTS)
    overlay.set_frame(bounds,(fragment,),AUTO_DEFAULTS)
    assert overlay._layout(fragment)[0].getRect()==original
    assert overlay._mask_rect(fragment).top()==890
    assert 965<=overlay._mask_rect(fragment).bottom()<=999
    expanded=overlay._mask_rect(fragment).getRect()
    for observation in (block,fragment,block):
        overlay.set_frame(bounds,(observation,),AUTO_DEFAULTS)
        assert overlay._mask_rect(observation).getRect()==expanded
        assert overlay._layout(observation)[0].getRect()==original


def test_context_id_changes_do_not_release_visual_lock(overlay):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('dialogue',(650,890,620,32),'English','Bản Việt',('db','Main_1'))
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    original=overlay._layout(block)[0].getRect()
    for number in (1,2,1,2):
        moved=replace(block,bounds=(600,880,720,60))
        overlay.set_frame(bounds,(moved,),{**AUTO_DEFAULTS,'context_hint':f'Main_Test_3_6_20_{number}'})
        assert overlay._layout(moved)[0].getRect()==original


def test_partial_flag_does_not_expand_mask_when_visible_ink_shrinks(overlay):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('dialogue',(650,890,620,32),'English','Bản Việt',('db','Main_1'))
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    original=overlay._mask_rect(block).getRect()
    signature=overlay.content_signature
    partial=replace(block,bounds=(700,890,500,31),partial=True)
    overlay.set_frame(bounds,(partial,),AUTO_DEFAULTS)
    overlay.set_frame(bounds,(partial,),AUTO_DEFAULTS)
    assert overlay._mask_rect(partial).getRect()==original
    assert overlay.content_signature is signature


@pytest.mark.parametrize('mode,y', [('cinematic',890),('walking',797)])
@pytest.mark.parametrize('dpi_scale',[1,1.25,1.5])
def test_partial_flag_has_identical_compact_layout_as_full_sentence(overlay,monkeypatch,mode,y,dpi_scale):
    monkeypatch.setattr(module,'logical_frame',lambda bounds:(0,0,round(1920/dpi_scale),round(1080/dpi_scale)))
    block=AutoTextBlock('dialogue',(500,y,920,72),'English',
        'Không ngờ quanh làng lại xuất hiện Tàn Tượng. Giá mà vẫn có Cơ Khôi như trước kia.',
        ('db','Main_1'),caption_mode=mode)
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    full=overlay._calculate_layout(block)
    fragment=replace(block,partial=True)
    partial=overlay._calculate_layout(fragment)
    assert full is not None and partial is not None
    assert partial[0].getRect()==full[0].getRect()
    assert partial[1].pixelSize()==full[1].pixelSize()
    assert partial[0].top()*dpi_scale==pytest.approx(y)
    assert partial[0].height()*dpi_scale<90
    assert partial[1].pixelSize()==round(28/dpi_scale)
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(fragment,),AUTO_DEFAULTS)
    overlay.grab()
    assert not overlay.content_truncated and not overlay.overflow


@pytest.mark.parametrize('reset', ['empty','style','frame','explicit','sentence','mode'])
def test_presentation_lock_is_invalidated_on_lifecycle_changes(overlay, reset):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('dialogue',(650,890,620,32),'English','Bản Việt',('db','Main_1'))
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    original=overlay._layout(block)[0].getRect()
    moved=replace(block,bounds=(500,880,920,70))
    settings=AUTO_DEFAULTS
    if reset=='empty':
        overlay.set_frame(bounds,(),settings)
    elif reset=='style':
        settings={**settings,'font_size':30}
    elif reset=='frame':
        bounds={**bounds,'left':20}
    elif reset=='explicit':
        overlay.reset_presentation()
    elif reset=='sentence':
        moved=replace(moved,identity=('db','Main_2'),target='Câu mới')
    elif reset=='mode':
        moved=replace(moved,caption_mode='walking',bounds=(500,797,920,60))
    overlay.set_frame(bounds,(moved,),settings)
    assert overlay._layout(moved)[0].getRect()!=original
    assert len(overlay._presentations)==1


def test_changed_choice_list_releases_old_wrap_to_respect_next_button(overlay):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('choices',(1288,500,340,24),'English','Bản Việt',('db','Main_1'),1)
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    old=overlay._presentations[overlay._block_key(block)]
    other=replace(block,identity=('db','Main_2'),order=2,bounds=(1288,560,340,24))
    overlay.set_frame(bounds,(block,other),AUTO_DEFAULTS)
    assert overlay._presentations[overlay._block_key(block)] is not old
    assert overlay._mask_rect(block).bottom()<=other.bounds[1]-12


def test_bad_geometry_does_not_reuse_a_frozen_mask(overlay):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('dialogue',(650,890,620,32),'English','Bản Việt',('db','Main_1'))
    overlay.set_frame(bounds,(block,),AUTO_DEFAULTS)
    invalid=replace(block,bounds=(500,500,900,32))
    overlay.set_frame(bounds,(invalid,),AUTO_DEFAULTS)
    assert overlay._layout(invalid) is None
    assert not overlay._presentations


@pytest.mark.parametrize('dpi_scale',[1,1.25,1.5])
def test_walking_layout_stays_below_speaker_and_above_hud(overlay,monkeypatch,dpi_scale):
    monkeypatch.setattr(module,'logical_frame',lambda bounds:(0,0,round(1920/dpi_scale),round(1080/dpi_scale)))
    block=AutoTextBlock('dialogue',(610,797,700,65),'English',
        'Ừ, đến tớ cũng không còn ngửi thấy thứ sương mù khiến đầu óc quay cuồng ấy nữa. Có vẻ nó đã tan hết rồi.',
        ('db','Main_1'),caption_mode='walking')
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),AUTO_DEFAULTS)
    rect,font=overlay._layout(block)
    assert rect.top()*dpi_scale>=796.9 and rect.bottom()*dpi_scale<=881
    overlay.grab()
    assert not overlay.overflow and not overlay.content_truncated


@pytest.mark.parametrize('dpi_scale', [1,1.25,1.5])
def test_first_typewriter_letters_mirror_about_game_center_without_reflow(overlay,monkeypatch,dpi_scale):
    monkeypatch.setattr(module,'logical_frame',lambda b:(0,0,round(1920/dpi_scale),round(1080/dpi_scale)))
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    first=AutoTextBlock('dialogue',(500,890,45,28),'First','Bản dịch giữ ở giữa.',('db','Main_1'),partial=True)
    overlay.set_frame(bounds,(first,),{**AUTO_DEFAULTS,'font_size':24})
    area,font=overlay._layout(first)
    assert area.center().x()==pytest.approx(overlay.width()/2)
    assert area.left()*dpi_scale<=500 and area.right()*dpi_scale>=1420
    assert font.pixelSize()==round(24*overlay.height()/1080)
    signature=overlay.content_signature
    full=replace(first,bounds=(500,890,920,28),partial=False,source='Full original sentence')
    overlay.set_frame(bounds,(full,),{**AUTO_DEFAULTS,'font_size':24})
    overlay.set_frame(bounds,(full,),{**AUTO_DEFAULTS,'font_size':24})
    assert overlay._layout(full)[0].getRect()==area.getRect()
    assert overlay.content_signature is signature


def test_impossible_fixed_font_text_keeps_english_without_shrink_or_top_box(overlay):
    bounds={'left':0,'top':0,'width':1920,'height':1080}
    block=AutoTextBlock('dialogue',(500,890,920,32),'English','Quá dài '*500,('db','Main_1'))
    overlay.set_frame(bounds,(block,),{**AUTO_DEFAULTS,'font_size':32})
    assert overlay._layout(block) is None and overlay._mask_rect(block) is None
    assert overlay.overflow==[block]
    overlay.grab()
    assert not overlay.drawn_bounds and not overlay.content_truncated


def test_choices_cannot_silently_shrink_selected_font(overlay):
    block=AutoTextBlock('choices',(1288,500,340,24),'English',
        'Đây là người bạn đã luôn đồng hành cùng tôi.',('db','Main_1'))
    overlay.set_frame({'left':0,'top':0,'width':1920,'height':1080},(block,),{**AUTO_DEFAULTS,'choice_font_size':40})
    assert overlay._layout(block) is None and overlay.overflow==[block]
