from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from ludiglot.core.auto_wuwa import (AutoDocument, AutoFrameResult, AutoPresenceTracker, AutoTextBlock,
    AutoTextIndex, AutoWuwaProcessor, SEARCH_BANDS, box_bounds, choice_border, grouped_rows, is_black_scene)
from ludiglot.core.desktop_models import AUTO_DEFAULTS, migrate_desktop_settings


def entry(key, english, vietnamese, *, verified=True):
    return {"text_key": key, "source_json": "lang_multi_text.db", "official_en": english,
            "official_vi": vietnamese, "translation_source": "wahu_exact_id_reviewed" if verified else "official_english_fallback"}


def index(*entries):
    return AutoTextIndex({str(i): {"matches": [value]} for i, value in enumerate(entries)})


def box(text, x=1280, y=500, w=300, h=28):
    return {"text": text, "conf": .92, "box": [[x,y],[x+w,y],[x+w,y+h],[x,y+h]]}


def test_auto_migration_is_opt_in_and_preserves_custom_fields():
    raw = {"mystery": 42, "regions": [{"id": "a", "hotkey": "alt+r"}], "overlay": {"background": "none"}}
    result = migrate_desktop_settings(raw)
    assert result["auto_wuwa"]["mode"] == "regions"
    assert result["auto_wuwa"]["bounds"] is None
    assert result["regions"][0]["hotkey"] == "alt+r"
    assert result["mystery"] == 42
    assert result["overlay"]["background"] == "none"


def test_fixed_answer_font_migrates_independently_without_changing_dialogue_font():
    original={'auto_wuwa':{'font_size':38,'background_color':'#334455','context_hint':'Main_Test_3_6_1_1'}}
    migrated=migrate_desktop_settings(original)['auto_wuwa']
    assert migrated['font_size']==38 and migrated['choice_font_size']==20
    assert migrated['background_color']=='#334455'
    assert migrated['context_hint']=='Main_Test_3_6_1_1'
    custom=migrate_desktop_settings({'auto_wuwa':{'font_size':38,'choice_font_size':24}})['auto_wuwa']
    assert custom['choice_font_size']==24 and custom['font_size']==38


def test_only_verified_provider_and_allowed_source_families_are_admitted():
    en = "Please stay here while I look for the missing cartridges."
    assert index(entry("Side_Test_1", en, "Ở lại nhé")).resolve(en, "dialogue")
    assert index(entry("Side_Test_1", en, "Không dùng", verified=False)).resolve(en, "dialogue") is None
    assert index(entry("ItemInfo_1_Name", en, "Không dùng")).resolve(en, "dialogue") is None
    assert index(entry("HotKeyText_ExitTips_Name", en, "Không dùng")).resolve(en, "dialogue") is None


def test_long_fuzzy_requires_margin_and_coverage():
    en = "Why don't you just make them digital?"
    search = index(entry("Side_Test_1", en, "Sao không làm dạng số?"))
    assert search.resolve("Why don't youjust make them digital?", "choices")
    assert search.resolve("make them digital", "choices") is None
    competing = index(entry("Side_Test_1", en, "A"), entry("Side_Test_2", "Why don't you just make this digital?", "B"))
    assert competing.resolve("Why dont you just make that digital", "choices") is None


def test_short_choices_are_exact_and_target_unambiguous():
    search = index(entry("Entity_1", "Chat", "Trò chuyện"))
    assert search.resolve("Chat", "choices")
    assert search.resolve("Chat", "dialogue") is None
    assert search.resolve("Chai", "choices") is None
    assert index(entry("Entity_1", "Chat", "Trò chuyện"), entry("Entity_2", "Chat", "Bản khác")).resolve("Chat", "choices") is None


def test_generic_interaction_control_is_not_confused_with_story_word():
    search=index(entry("Entity_1","Chat","Tán gẫu"),entry("PrefabTextItem_1_Text","Chat","Trò chuyện"))
    assert search.resolve("Chat","choices").vietnamese=="Trò chuyện"
    assert search.resolve("Chat","dialogue") is None


def test_auto_temporarily_disables_text_rescue_geometry_and_restores_flags_on_error():
    class BrokenEngine:
        win_ocr_refine=True
        win_ocr_adaptive=True
        def recognize_pipeline(self,*args,**kwargs):
            assert self.win_ocr_refine is False and self.win_ocr_adaptive is False
            raise RuntimeError("OCR unavailable")
    engine=BrokenEngine()
    with pytest.raises(RuntimeError):
        AutoWuwaProcessor(index()).process(Image.new("RGB",(1920,1080),"gray"),engine,AUTO_DEFAULTS)
    assert engine.win_ocr_refine and engine.win_ocr_adaptive


def test_conflicting_same_identity_is_rejected():
    search = index(entry("Side_1", "A long English sentence for a conversation.", "A"),
                   entry("Side_1", "A long English sentence for a conversation.", "B"))
    assert search.resolve("A long English sentence for a conversation.", "dialogue") is None


def test_verified_source_move_is_admitted_but_id_only_guess_is_not():
    item=entry('Main_Mengzhou_3_6_20_1',"Suisui... are you sure you won't stay a few more days?",'Ở lại thêm vài ngày nhé?')
    item['translation_source']='wahu_verified_source_move'
    assert index(item).resolve(item['official_en'],'dialogue')
    item['translation_source']='guessed_same_key'
    assert index(item).resolve(item['official_en'],'dialogue') is None


def test_player_name_requires_exact_literals_and_uses_observed_name():
    search=index(entry('Main_Mengzhou_3_6_20_3','There you are at last, {PlayerName}.','{PlayerName}, cuối cùng cậu cũng tới rồi.'))
    result=search.resolve('There you are at last, Ossmoc.','dialogue')
    assert result.identity[1]=='Main_Mengzhou_3_6_20_3'
    assert result.vietnamese=='Ossmoc, cuối cùng cậu cũng tới rồi.'
    assert search.resolve('There you were lost, Ossmoc.','dialogue') is None
    assert search.resolve('There you are at last, Ossmoc.','transition') is None
    assert index(entry('Main_Mengzhou_3_6_20_3','Hi, {PlayerName}.','Chào {PlayerName}')).resolve('Hi, Ossmoc.','dialogue') is None


def test_context_uses_existing_nearby_records_and_never_predicts_next_id():
    search=index(entry('Main_Mengzhou_3_6_20_1','A verified English sentence that establishes the scene.','Mở đoạn'),
                 entry('Main_Mengzhou_3_6_21_7','Please stay here while I look for the missing cartridges.','Ở lại'),
                 entry('Main_Other_3_6_21_7','Please stay here while I look for the missing cartridge.','Bản khác'))
    assert search.resolve('A verified English sentence that establishes the scene.','dialogue')
    assert search.context_hint=='Main_Mengzhou_3_6_20_1'
    assert search.resolve('Please stay here while Ilook for the missing cartridges.','dialogue').identity[1]=='Main_Mengzhou_3_6_21_7'
    assert search.resolve('The screen now displays completely unrelated combat text.','dialogue') is None
    assert not search.set_context('Main_Mengzhou_3_6_21_1') # invented successor
    assert search.set_context('') and not search._scope_keys


def test_context_never_overrides_unrelated_exact_match_or_stronger_candidate():
    search=index(entry('Main_Mengzhou_3_6_20_1','Please stay here while I look for the missing cartridge.','Một'),
                 entry('Main_Other_3_6_21_1','Please stay here while I look for the missing cartridges.','Hai'))
    assert search.set_context('Main_Mengzhou_3_6_20_1')
    assert search.resolve('Please stay here while I look for the missing cartridges.','dialogue').vietnamese=='Hai'
    search.set_context('Main_Mengzhou_3_6_20_1')
    assert search.resolve('Please stay here while Ilook for the missing cartridges.','dialogue') is None


def test_unselected_choice_gray_border_needs_dark_interior():
    image=Image.new('RGB',(1920,1080),'#334466')
    draw=ImageDraw.Draw(image)
    draw.rectangle((1228,645,1660,728),fill='#25282d')
    draw.line((1226,645,1226,728),fill='#a49b9a',width=2)
    assert choice_border(image,(1289,661,348,58))
    uniform=Image.new('RGB',(1920,1080),'#a49b9a')
    assert not choice_border(uniform,(1289,661,348,58))


def test_contrast_retry_uses_real_scaled_geometry_and_caches_result():
    text='There you are at last, Ossmoc.'
    image=Image.new('RGB',(1920,1080),'#334466')
    ImageDraw.Draw(image).text((900,815),'Suisui',fill='#ffe76b',stroke_width=4)
    class RetryEngine:
        calls=0
        win_ocr_refine=True
        def recognize_pipeline(self,crop,**kwargs):
            self.calls+=1
            assert not self.win_ocr_refine
            if crop.mode=='L':
                # Retry spans the horizontal band: origin=(192,872).
                    sx,sy=crop.width/1536,crop.height/68
                    return SimpleNamespace(boxes=[box(text,x=round(558*sx),y=round(18*sy),w=round(420*sx),h=round(32*sy))])
            if crop.size==crop_response('dialogue',[])[0]:
                return SimpleNamespace(boxes=crop_response('dialogue',[box('There yo a atl st',x=750,y=890,w=420,h=32)])[1])
            return SimpleNamespace(boxes=[])
    engine=RetryEngine()
    processor=AutoWuwaProcessor(index(entry('Main_Mengzhou_3_6_20_3','There you are at last, {PlayerName}.','{PlayerName}, tới rồi.')))
    result=processor.process(image,engine,AUTO_DEFAULTS)
    assert result.blocks[0].bounds==(750,890,420,32)
    assert result.blocks[0].target=='Ossmoc, tới rồi.'
    assert engine.win_ocr_refine
    calls=engine.calls
    assert processor.process(image,engine,AUTO_DEFAULTS).blocks==result.blocks
    assert engine.calls==calls


def test_generation_change_discards_automatic_context():
    search=index(entry('Main_Mengzhou_3_6_20_1','A verified English sentence that establishes the scene.','Mở đoạn'))
    processor=AutoWuwaProcessor(search); engine=Engine({})
    image=Image.new('RGB',(1920,1080),'gray')
    processor.process(image,engine,{**AUTO_DEFAULTS,'generation':1})
    assert search.set_context('Main_Mengzhou_3_6_20_1')
    processor.process(image,engine,{**AUTO_DEFAULTS,'generation':2})
    assert not search.context_hint


def test_document_requires_title_and_distinct_body_anchors():
    lines = ["We conducted a comprehensive survey of the old forest.",
             "The researchers discovered valuable seeds and specimens.",
             "Next we will study the local population in greater detail."]
    search = index(entry("InfoDisplay_1_Title", "Forest diary", "Nhật ký"),
                   entry("InfoDisplay_1_Text", "\n".join(lines), "Toàn bộ bài Việt"))
    assert search.document("Forest diary", lines).target == "Toàn bộ bài Việt"
    assert search.document("Wrong title", lines) is None
    assert search.document("Forest diary", [lines[0], lines[0]]) is None
    assert search.document("Forest diary", ["A completely different paragraph appears in this story."]*3) is None
    assert search.document("", lines).title == "Nhật ký"


def test_document_preserves_paragraphs_from_escaped_game_newlines():
    a = "We conducted a comprehensive survey of the old forest."
    b = "The researchers discovered valuable seeds and specimens."
    search = index(entry("InfoDisplay_1_Title", "Diary", "Nhật ký"),
        entry("InfoDisplay_1_Text", a+"\\n"+b, "Đoạn thứ nhất\\nĐoạn thứ hai"))
    assert search.document("Diary", [a,b]).target == "Đoạn thứ nhất\nĐoạn thứ hai"


def test_duplicate_titles_are_disambiguated_by_body():
    a = ["The first team surveyed the forest and found many new species.", "The team returned home after a successful investigation."]
    b = ["The second team visited the city and discovered a lost archive.", "The archive contained maps and records of ancient history."]
    search = index(entry("InfoDisplay_1_Title", "Diary", "A"), entry("InfoDisplay_1_Text", "\n".join(a), "A text"),
                   entry("InfoDisplay_2_Title", "Diary", "B"), entry("InfoDisplay_2_Text", "\n".join(b), "B text"))
    assert search.document("Diary", b).target == "B text"


def test_invalid_and_mock_geometry_is_not_used():
    assert box_bounds({"box": [[0,0]]}) is None
    assert box_bounds({**box("mock"), "geometry_valid": False}) is None
    assert box_bounds(box("real")) == (1280,500,300,28)


def test_wrapped_choice_is_grouped_without_merging_the_next_button():
    rows = grouped_rows([box("First part", y=500), box("wrapped line", y=534), box("Next choice", y=620)])
    assert [text for text, _ in rows] == ["First part wrapped line", "Next choice"]


def test_dark_background_alone_is_not_enough_to_translate():
    assert is_black_scene(Image.new("RGB", (1920,1080), "black"))
    assert not is_black_scene(Image.new("RGB", (1920,1080), "gray"))
    assert index().resolve("Random unsupported sentence on a black screen.", "transition") is None


def test_choice_border_is_required():
    image = Image.new("RGB", (1920,1080), "#334466")
    rect = (1288,500,350,56)
    assert not choice_border(image, rect)
    ImageDraw.Draw(image).line((1226,490,1226,565), fill="white", width=3)
    assert choice_border(image, rect)


class Engine:
    def __init__(self, responses):
        self.responses = responses; self.calls = 0

    def recognize_pipeline(self, image, **kwargs):
        self.calls += 1
        return SimpleNamespace(boxes=self.responses.get(image.size, []))


def crop_response(kind, boxes, width=1920, height=1080):
    fx,fy,fw,fh=SEARCH_BANDS[kind]
    x,y=round(fx*width),round(fy*height)
    size=(round((fx+fw)*width)-x,round((fy+fh)*height)-y)
    return size, [{**b,"box":[[p[0]-x,p[1]-y] for p in b["box"]]} for b in boxes]


def test_processor_translates_dialogue_and_choices_in_same_frame_and_skips_unchanged_ocr():
    story="I should really figure out a good way to keep these things safe."
    choice="Why don't you just make them digital?"
    search=index(entry("Side_1",story,"Câu thoại Việt"),entry("Side_2",choice,"Đáp án Việt"))
    image=Image.new("RGB",(1920,1080),"#334466")
    ImageDraw.Draw(image).line((1226,630,1226,720),fill="white",width=3)
    ImageDraw.Draw(image).text((900,815), "KU-Cass", fill="#ffe76b", stroke_width=4)
    responses=dict([crop_response("dialogue",[box(story,x=450,y=890,w=1000,h=32)]),
                    crop_response("choices",[box(choice,x=1288,y=652,w=360,h=56)])])
    engine=Engine(responses); processor=AutoWuwaProcessor(search)
    result=processor.process(image,engine,AUTO_DEFAULTS)
    assert set(result.detected_kinds)=={"choices","dialogue"}
    assert len(result.blocks)==2
    calls=engine.calls
    processor.process(image,engine,AUTO_DEFAULTS)
    assert engine.calls==calls


def test_presence_requires_two_misses_but_recovers_after_one():
    tracker=AutoPresenceTracker()
    b=AutoTextBlock("dialogue",(450,890,1000,32),"A long English story sentence.","Bản Việt",("db","Side_1"))
    live=AutoFrameResult(blocks=(b,),present_kinds=("dialogue",))
    tracker.update(live)
    assert tracker.update(AutoFrameResult()).blocks == (b,)
    assert tracker.update(live).blocks == (b,)
    tracker.update(AutoFrameResult())
    assert tracker.update(AutoFrameResult()).blocks == ()


def test_noise_keeps_output_but_different_unresolved_sentence_drops_it():
    tracker=AutoPresenceTracker()
    b=AutoTextBlock("dialogue",(450,890,1000,32),"Please stay here while I search for the cartridges.","Bản Việt",("db","Side_1"))
    tracker.update(AutoFrameResult(blocks=(b,),present_kinds=("dialogue",)))
    noise=AutoFrameResult(present_kinds=("dialogue",),observed_texts=(("dialogue","Please stay here while I search for the cartridge."),))
    assert tracker.update(noise).blocks == (b,)
    changed=AutoFrameResult(present_kinds=("dialogue",),observed_texts=(("dialogue","A completely different sentence is shown now."),))
    assert tracker.update(changed).blocks == ()


def test_document_expires_and_reset_drops_all_state():
    tracker=AutoPresenceTracker(); doc=AutoDocument(("db","InfoDisplay_1"),"Title","Bài Việt")
    tracker.update(AutoFrameResult(document=doc,document_present=True))
    assert tracker.update(AutoFrameResult()).document == doc
    assert tracker.update(AutoFrameResult()).document is None
    tracker.update(AutoFrameResult(document=doc,document_present=True)); tracker.reset()
    assert tracker.document is None and not tracker.blocks


def test_idle_discovery_is_not_repeated_every_tracking_tick(monkeypatch):
    from ludiglot.core import auto_wuwa as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    engine = Engine({}); processor = AutoWuwaProcessor(index())
    image = Image.new("RGB", (1920,1080), "gray")
    processor.process(image, engine, AUTO_DEFAULTS)
    assert engine.calls == 5  # Four legacy bands plus the small Glossary label.
    clock[0] += .35
    processor.process(image, engine, AUTO_DEFAULTS)
    assert engine.calls == 5
    clock[0] += 1.5
    processor.process(image, engine, AUTO_DEFAULTS)
    assert engine.calls == 5  # identical crops still use the cached OCR
    ImageDraw.Draw(image).rectangle((1300,500,1310,510), fill="white")
    clock[0] += 1.5
    processor.process(image, engine, AUTO_DEFAULTS)
    assert engine.calls > 5


def test_invalid_optional_auto_config_is_recovered_without_losing_legacy_fields():
    from ludiglot.core.desktop_models import migrate_desktop_settings
    raw = {"existing": "keep", "auto_wuwa": {"font_size": "bad", "background_color": "invalid",
           "reader_geometry": {"x": "not a coordinate"}, "mode": "unsupported"}}
    migrated = migrate_desktop_settings(raw)
    assert migrated["existing"] == "keep"
    assert migrated["auto_wuwa"]["font_size"] == 28
    assert migrated["auto_wuwa"]["background_color"] == AUTO_DEFAULTS['background_color']
    assert migrated["auto_wuwa"]["reader_geometry"] == {}
    assert migrated["auto_wuwa"]["mode"] == "regions"


def test_exact_hud_text_is_not_a_dialogue_without_the_speaker_layout():
    story = "An exact long sentence that might appear in the bottom HUD."
    engine = Engine(dict([crop_response("dialogue", [box(story,x=450,y=890,w=1000,h=32)])]))
    processor = AutoWuwaProcessor(index(entry("PrefabTextItem_1_Text", story, "Bản Việt")))
    result = processor.process(Image.new("RGB", (1920,1080), "gray"), engine, AUTO_DEFAULTS)
    assert not result.blocks and "dialogue" not in result.detected_kinds


def test_unrecognized_different_document_never_keeps_previous_reader():
    tracker = AutoPresenceTracker()
    doc = AutoDocument(("db", "InfoDisplay_1"), "Nhật ký", "Bản Việt",
        source_title="Diary", source_body="The first team surveyed the forest and discovered rare seeds.\nThe team returned home after a successful investigation.")
    tracker.update(AutoFrameResult(document=doc, document_present=True))
    unknown = AutoFrameResult(document_present=True, observed_texts=(("document_title", "Diary"),
        ("document", "An unrelated document about purchasing tickets at the station."),
        ("document", "Please bring identification before boarding the train tomorrow.")))
    assert tracker.update(unknown).document is None


def test_old_presentation_upgrade_disables_debug_and_preserves_custom_palette():
    from ludiglot.core.desktop_models import normalize_auto_settings
    old = dict(background_color='#25282d',background_opacity=95,show_bounds=True,mode='auto',context_hint='saved')
    new = normalize_auto_settings(old)
    assert new['background_color']=='#3b3e43' and new['background_opacity']==88
    assert not new['show_bounds'] and new['mode']=='auto' and new['context_hint']=='saved'
    custom = normalize_auto_settings({**old,'background_color':'#667788','background_opacity':80})
    assert custom['background_color']=='#667788' and custom['background_opacity']==80
    assert normalize_auto_settings({**new,'show_bounds':True})['show_bounds']


def visual_fixture():
    image=Image.new('RGB',(1920,1080),'#334466')
    draw=ImageDraw.Draw(image)
    draw.text((900,815),'Suisui',fill='#ffe76b',stroke_width=4)
    # Deterministic outlined ink, not a font-dependent OCR fixture.
    for x in range(650,1050,14):
        draw.rectangle((x,896,x+6,916),fill='#f5ecd9')
    block=AutoTextBlock('dialogue',(640,890,430,32),'Please stay here while I search for the cartridges.',
                        'Bản Việt',('db','Side_1'))
    return image,block


def test_visible_ink_holds_verified_dialogue_through_bad_or_empty_ocr():
    image,block=visual_fixture()
    processor=AutoWuwaProcessor(index()); tracker=AutoPresenceTracker()
    tracker.update(processor._track(AutoFrameResult(blocks=(block,),present_kinds=('dialogue',)),image))
    noisy=AutoFrameResult(present_kinds=('dialogue',),observed_texts=(('dialogue','P e se s ay h r'),))
    for observation in (noisy,AutoFrameResult(),noisy,AutoFrameResult()):
        frame=processor._track(observation,image)
        assert frame.stable_kinds==('dialogue',)
        assert tracker.update(frame).blocks==(block,)
    # A different paragraph at the same position must clear the old mask.
    changed=image.copy(); draw=ImageDraw.Draw(changed)
    draw.rectangle((640,890,1070,922),fill='#334466')
    draw.rectangle((700,897,990,902),fill='white')
    frame=processor._track(noisy,changed)
    assert not frame.stable_kinds and not tracker.update(frame).blocks


def test_visual_memory_never_holds_on_bright_background_or_missing_speaker():
    image,block=visual_fixture(); processor=AutoWuwaProcessor(index())
    processor._track(AutoFrameResult(blocks=(block,)),image)
    changed=image.copy(); ImageDraw.Draw(changed).rectangle((640,890,1070,922),fill='white')
    assert not processor._track(AutoFrameResult(),changed).stable_kinds
    processor._track(AutoFrameResult(blocks=(block,)),image)
    changed=image.copy(); ImageDraw.Draw(changed).rectangle((650,795,1250,860),fill='#334466')
    assert not processor._track(AutoFrameResult(),changed).stable_kinds
    assert processor._dialogue_visual is None


def test_generation_reset_drops_visual_memory_and_cannot_resurrect_tracker():
    image,block=visual_fixture(); processor=AutoWuwaProcessor(index()); tracker=AutoPresenceTracker()
    processor.process(image,Engine({}),{**AUTO_DEFAULTS,'generation':1},backend='test')
    processor._track(AutoFrameResult(blocks=(block,)),image)
    frame=processor.process(image,Engine({}),{**AUTO_DEFAULTS,'generation':2},backend='test',force=True)
    assert not frame.stable_kinds and processor._dialogue_visual is None
    assert not tracker.update(AutoFrameResult(stable_kinds=('dialogue',))).blocks


def test_same_verified_caption_snaps_only_tiny_geometry_jitter():
    from dataclasses import replace
    image,block=visual_fixture(); processor=AutoWuwaProcessor(index())
    processor._track(AutoFrameResult(blocks=(block,)),image)
    jitter=replace(block,bounds=(642,891,431,32))
    assert processor._track(AutoFrameResult(blocks=(jitter,)),image).blocks[0].bounds==block.bounds
    moved=replace(block,bounds=(650,895,431,32))
    assert processor._track(AutoFrameResult(blocks=(moved,)),image).blocks[0].bounds==moved.bounds


def walking_fixture():
    image=Image.new('RGB',(1920,1080),'#334466')
    draw=ImageDraw.Draw(image)
    for x in (917,939,961,983):
        draw.rectangle((x,730,x+12,758),outline='#ffe76b',width=3)
    story='Even I cannot smell that strange fog anymore. It has cleared out.'
    responses=dict([crop_response('walking_speaker',[box('Abby',x=915,y=730,w=90,h=30)]),
                    crop_response('walking_dialogue',[box(story,x=500,y=797,w=920,h=63)])])
    return image,story,responses


def test_walking_dialogue_requires_exact_game_speaker_and_verified_source():
    image,story,responses=walking_fixture()
    search=index(entry('Main_Mengzhou_3_6_40_2',story,'Thoại di chuyển Việt'),
                 entry('Speaker_1_Name','Abby','',verified=False))
    processor=AutoWuwaProcessor(search)
    result=processor.process(image,Engine(responses),AUTO_DEFAULTS,backend='test',force=True)
    assert result.detected_kinds==('dialogue',)
    assert len(result.blocks)==1 and result.blocks[0].caption_mode=='walking'
    assert result.blocks[0].bounds==(500,797,920,63)
    assert result.blocks[0].target=='Thoại di chuyển Việt'
    assert search.context_hint=='Main_Mengzhou_3_6_40_2'
    # A speaker's English name is sufficient; it needn't have a Vietnamese name.
    assert 'abby' in search.speaker_names


@pytest.mark.parametrize('name',['Ossmoc','smoc',''])
def test_custom_player_name_uses_verified_rover_role_after_reading_caption(name):
    image,story,responses=walking_fixture()
    responses.update(dict([crop_response('walking_speaker',
        [box(name,x=930,y=730,w=90,h=30)] if name else [])]))
    player=entry('Main_Mengzhou_3_6_121_5',story,'Thoại của người chơi')
    player['verified_speaker']='Rover'
    processor=AutoWuwaProcessor(index(player))
    result=processor.process(image,Engine(responses),AUTO_DEFAULTS,backend='test',force=True)
    assert len(result.blocks)==1 and result.blocks[0].caption_mode=='walking'
    assert result.blocks[0].target=='Thoại của người chơi'


@pytest.mark.parametrize('problem',['no_role','npc','hud','unverified','no_gold'])
def test_unknown_player_label_never_authorizes_unverified_or_hud_text(problem):
    image,story,responses=walking_fixture()
    responses.pop(crop_response('walking_speaker',[])[0])
    item=entry('ItemInfo_123_Description' if problem=='hud' else 'Main_Mengzhou_3_6_121_5',
        story,'Không được che',verified=problem!='unverified')
    item['verified_speaker']='Abby' if problem=='npc' else '' if problem=='no_role' else 'Rover'
    if problem=='no_gold':
        image=Image.new('RGB',(1920,1080),'#334466')
    result=AutoWuwaProcessor(index(item)).process(image,Engine(responses),AUTO_DEFAULTS,backend='test',force=True)
    assert not result.blocks


@pytest.mark.parametrize('problem',['no_name','wrong_name','no_gold','disabled','unverified'])
def test_walking_hud_or_unverified_caption_never_creates_a_mask(problem):
    image,story,responses=walking_fixture()
    settings=dict(AUTO_DEFAULTS)
    name='Other person' if problem=='wrong_name' else 'Abby'
    search=index(entry('Main_1',story,'Việt',verified=problem!='unverified'),
                 entry('Speaker_1_Name',name,'',verified=False))
    if problem=='no_name':
        responses.pop(crop_response('walking_speaker',[])[0])
    if problem=='no_gold':
        image=Image.new('RGB',(1920,1080),'#334466')
    if problem=='disabled':
        settings['walking']=False
    result=AutoWuwaProcessor(search).process(image,Engine(responses),settings,backend='test',force=True)
    assert not result.blocks


def test_walking_speaker_triggers_immediate_scan_between_wide_discoveries():
    image,story,responses=walking_fixture()
    search=index(entry('Main_1',story,'Việt'),entry('Speaker_1_Name','Abby','',verified=False))
    processor=AutoWuwaProcessor(search)
    processor.process(Image.new('RGB',(1920,1080),'#334466'),Engine({}),AUTO_DEFAULTS,backend='test',force=True)
    processor._next_discovery=float('inf')
    frame=processor.process(image,Engine(responses),AUTO_DEFAULTS,backend='test')
    assert frame.blocks and frame.blocks[0].caption_mode=='walking'


def test_walking_modal_priority_never_translates_story_behind_document():
    image,story,responses=walking_fixture()
    responses.update(dict([crop_response('glossary_label',[box('Glossary',x=390,y=142,w=200,h=35)])]))
    search=index(entry('Main_1',story,'Việt'),entry('Speaker_1_Name','Abby','',verified=False))
    frame=AutoWuwaProcessor(search).process(image,Engine(responses),AUTO_DEFAULTS,backend='test',force=True)
    assert frame.present_kinds==('glossary',) and not frame.blocks


def test_gold_scenery_does_not_trigger_expensive_walking_speaker_ocr():
    image,story,responses=walking_fixture()
    ImageDraw.Draw(image).rectangle((700,713,1228,783),fill='#ffe76b')
    search=index(entry('Main_1',story,'Việt'),entry('Speaker_1_Name','Abby','',verified=False))
    processor=AutoWuwaProcessor(search)
    frame=processor.process(image,Engine(responses),AUTO_DEFAULTS,backend='test',force=True)
    assert not frame.blocks and 'walking_speaker' not in processor._ocr_cache


def test_walking_layout_memory_holds_same_ink_but_clears_missing_speaker():
    image,story,_=walking_fixture()
    draw=ImageDraw.Draw(image)
    for x in range(650,1050,14):
        draw.rectangle((x,800,x+6,820),fill='#f5ecd9')
    block=AutoTextBlock('dialogue',(640,794,430,32),story,'Việt',('db','Main_1'),caption_mode='walking')
    processor=AutoWuwaProcessor(index())
    processor._track(AutoFrameResult(blocks=(block,)),image)
    assert processor._track(AutoFrameResult(),image).stable_kinds==('dialogue',)
    draw.rectangle((700,713,1228,783),fill='#334466')
    assert not processor._track(AutoFrameResult(),image).stable_kinds


def test_both_contrast_variants_are_bounded_and_unverified_text_is_discarded():
    image,block=visual_fixture(); processor=AutoWuwaProcessor(index())
    class Retry:
        calls=0
        win_ocr_refine=True
        def recognize_pipeline(self,crop,**kwargs):
            self.calls+=1
            return SimpleNamespace(boxes=[box('Unsupported invented sentence',x=30,y=30,w=100,h=20)])
    engine=Retry(); original=[box(block.source,x=640,y=890,w=430,h=32)]
    assert processor._rescue_dialogue(image,original,engine,'windows',(192,864,1728,994))==original
    assert engine.calls==2 and engine.win_ocr_refine


def test_short_story_exact_match_is_allowed_but_not_guessed():
    search=index(entry('Main_Mengzhou_3_6_30_17','So the time has come.','Vậy là thời khắc đã tới.'))
    assert search.resolve('So the time has come.','dialogue')
    assert not search.resolve('So the time has cone.','dialogue')
    assert not search.resolve('Yes','dialogue')
    assert not search.resolve('So the time has come.','transition')
    ambiguous=index(entry('Main_1','So the time has come.','Một'),entry('Main_2','So the time has come.','Hai'))
    assert not ambiguous.resolve('So the time has come.','dialogue')


def test_short_dialogue_still_requires_real_geometry_and_speaker():
    story='So the time has come.'
    processor=AutoWuwaProcessor(index(entry('Main_Mengzhou_3_6_30_17',story,'Đã đến lúc.')))
    responses=dict([crop_response('dialogue',[box(story,x=808,y=891,w=305,h=31)])])
    image, _=visual_fixture()
    assert processor.process(image,Engine(responses),AUTO_DEFAULTS,backend='test',force=True).blocks
    assert not processor.process(Image.new('RGB',image.size,'gray'),Engine(responses),AUTO_DEFAULTS,backend='test',force=True).blocks


def test_fragment_requires_known_scene_distinctive_text_and_global_uniqueness():
    story='Paragon Qingxiao, should disaster one day befall Xuanfang Hold, I hope you will raise your sword for its people.'
    seed=entry('Main_Mengzhou_3_6_30_10','This exact sentence identifies the current conversation.','Mở đoạn')
    search=index(seed,entry('Main_Mengzhou_3_6_30_11',story,'Bản đầy đủ'))
    fragment='should disaster one day befall Xuanfang Hold I hope you'
    assert not search.resolve(fragment,'dialogue')
    assert search.set_context(seed['text_key'])
    result=search.resolve(fragment,'dialogue')
    assert result.partial and result.vietnamese=='Bản đầy đủ'
    assert not search.resolve('I hope you will raise','dialogue')
    assert not search.resolve(fragment,'choices')
    ambiguous=index(seed,entry('Main_Mengzhou_3_6_30_11',story,'Bản một'),
                    entry('Main_Other_3_6_30_11',story+' But never lose hope.','Bản hai'))
    ambiguous.set_context(seed['text_key'])
    assert not ambiguous.resolve(fragment,'dialogue')
    search.set_context('')
    assert not search.resolve(fragment,'dialogue')


def test_clipped_noisy_caption_matches_independent_anchors_not_predicted_id():
    seed = entry('Main_Mengzhou_3_6_50_16', 'A verified response establishes this conversation.', 'Đáp án')
    full = 'Bloom Bearer Hero of Jinzhou Dark Thunderbolt Laureate Hero of Heroes Voidworm Rider These are the stories the Ministry of Foreign Affairs recorded in broad strokes'
    caption = 'Thunderbolt Laureate Hero of Heroes Voidworm Rider These are the stories the Ministryof Foreign Affairs recorded in broad strokes 6NV'
    search = index(seed, entry('Main_Mengzhou_3_6_50_15', full, 'Toàn bộ bản Việt'))
    assert search.resolve(caption, 'dialogue') is None
    search.set_context(seed['text_key'])
    found = search.resolve(caption, 'dialogue')
    assert found and found.partial and found.identity[1].endswith('_50_15')
    assert found.vietnamese == 'Toàn bộ bản Việt'
    assert search.resolve('the Ministry of', 'dialogue') is None
    assert search.resolve(caption, 'choices') is None


def test_same_baseline_polygons_read_left_to_right_despite_vertical_jitter():
    boxes = [box('Voidworm Rider', x=1200, y=877, w=200, h=38),
             box('Thunderbolt Laureate', x=504, y=878, w=350, h=38),
             box('Hero of Heroes', x=870, y=879, w=250, h=38),
             box('broad strokes', x=856, y=965, w=220, h=28)]
    rows = grouped_rows(boxes, paragraph=True)
    assert rows[0][0] == 'Thunderbolt Laureate Hero of Heroes Voidworm Rider broad strokes'


def test_clipped_anchors_reject_ambiguity_stale_context_and_contradicting_text():
    seed = entry('Main_Mengzhou_3_6_50_16', 'A verified response establishes this conversation.', 'Đáp án')
    full = 'Old unseen opening Hero of Heroes Voidworm Rider These are the stories the Ministry of Foreign Affairs recorded in broad strokes'
    caption = 'noise Hero of Heroes Voidworm Rider These are the stories noise Foreign Affairs recorded in broad strokes'
    for other_key in ('Main_Mengzhou_3_6_50_17', 'Main_Other_3_6_50_17'):
        search = index(seed, entry('Main_Mengzhou_3_6_50_15', full, 'Một'), entry(other_key, full + ' Another ending', 'Hai'))
        search.set_context(seed['text_key'])
        assert search.resolve(caption, 'dialogue') is None
    search = index(seed, entry('Main_Mengzhou_3_6_50_15', full, 'Một'))
    search.set_context(seed['text_key'])
    assert search.resolve('Hero of Heroes completely unrelated wording goes on for many additional words and changes the meaning entirely Voidworm Rider', 'dialogue') is None


def glossary_fixture(body=None, label='Glossary'):
    title = 'Ministry of Foreign Affairs'
    lines = body or ['An administrative bureau based in Shaftpost Yard.',
                     'Inspection Officers are responsible for recording',
                     'and verifying goods and commodities entering the Hold.']
    search = index(entry('Term750048_Title', title, 'Phủ Ngoại Sự'),
                   entry('Term750048_Desc', ' '.join(lines), 'Toàn văn đã xác nhận'))
    boxes = [box(title, x=615, y=268, w=380, h=32)] + [box(t, x=614, y=327+i*42, w=700, h=32) for i,t in enumerate(lines)]
    responses = dict([crop_response('glossary_label', [box(label, x=392, y=149, w=140, h=28)]),
                      crop_response('glossary', boxes)])
    return search, responses


def test_glossary_has_modal_priority_and_verified_title_body_not_reader():
    search, responses = glossary_fixture()
    processor = AutoWuwaProcessor(search); engine = Engine(responses)
    frame = processor.process(Image.new('RGB', (1920,1080), 'gray'), engine, AUTO_DEFAULTS, backend='test')
    assert frame.detected_kinds == ('glossary',) and not frame.document_present and frame.document is None
    assert len(frame.blocks) == 2 and all(b.kind == 'glossary' for b in frame.blocks)
    assert engine.calls == 2  # Stop before OCR of the occluded story/background.
    assert not search.resolve(' '.join([b.source for b in frame.blocks]), 'dialogue')
    calls = engine.calls
    processor.process(Image.new('RGB', (1920,1080), 'gray'), engine, AUTO_DEFAULTS, backend='test')
    assert engine.calls == calls


def test_glossary_requires_label_and_body_rejects_same_title_different_content():
    search, responses = glossary_fixture(label='Unrelated menu')
    frame = AutoWuwaProcessor(search).process(Image.new('RGB',(1920,1080),'gray'), Engine(responses), AUTO_DEFAULTS, backend='test')
    assert 'glossary' not in frame.detected_kinds
    assert search.glossary('Ministry of Foreign Affairs', ['Entirely different administrative rule on a different subject.', 'Nothing here refers to those inspection officers at all.']) is None
    assert search.glossary('Ministry of Foreign Affairs', ['An administrative bureau based in Shaftpost Yard.']) is None


def test_untranslated_glossary_suppresses_old_dialogue_and_disappears_on_close():
    tracker = AutoPresenceTracker()
    old = AutoTextBlock('dialogue', (500,890,800,32), 'A previous story line', 'Cũ', ('db','Main_1'))
    tracker.update(AutoFrameResult(blocks=(old,), present_kinds=('dialogue',)))
    assert not tracker.update(AutoFrameResult(present_kinds=('glossary',))).blocks
    popup = AutoTextBlock('glossary',(610,330,700,240),'English','Thông tin',('db','Term1_Desc'))
    assert tracker.update(AutoFrameResult(blocks=(popup,),present_kinds=('glossary',))).blocks == (popup,)
    assert tracker.update(AutoFrameResult(blocks=(old,),present_kinds=('dialogue',))).blocks == (old,)
    tracker.reset()
    assert not tracker.blocks


def test_cpu_fallback_is_one_bounded_retry_cached_and_never_trusts_unverified_text():
    image,_=visual_fixture()
    story='So the time has come.'
    class Fallback:
        calls=0
        def recognize(self,crop):
            self.calls+=1
            return [box(story,x=617,y=27,w=301,h=24)]
    fallback=Fallback()
    processor=AutoWuwaProcessor(index(entry('Main_Mengzhou_3_6_30_18',story,'Đã đến lúc.')),fallback)
    result=processor.process(image,Engine({}),AUTO_DEFAULTS)
    assert result.blocks[0].bounds==(809,891,301,24) and fallback.calls==1
    assert processor.process(image,Engine({}),AUTO_DEFAULTS).blocks==result.blocks and fallback.calls==1
    assert not processor.process(image,Engine({}),{**AUTO_DEFAULTS,'light_ocr':False},force=True).blocks
    assert fallback.calls==1
    unknown=AutoWuwaProcessor(index(),fallback)
    assert not unknown.process(image,Engine({}),AUTO_DEFAULTS).blocks


def test_async_fallback_is_polled_on_primary_cache_miss_and_never_trusts_old_ink():
    image, _ = visual_fixture()
    story = 'So the time has come.'
    class AsyncFallback:
        completed = False
        tokens = []
        last_completed_ms = 68
        cancellations = 0
        def cancel(self):
            self.cancellations += 1
        def poll_or_submit(self, crop, token):
            self.tokens.append(token)
            if self.completed and token == self.tokens[0]:
                return [box(story, x=617, y=27, w=301, h=24)]
            return None
    fallback = AsyncFallback()
    processor = AutoWuwaProcessor(index(entry('Main_Mengzhou_3_6_30_18', story, 'Đã đến lúc.')), fallback)
    assert not processor.process(image, Engine({}), AUTO_DEFAULTS).blocks
    fallback.completed = True
    assert processor.process(image, Engine({}), AUTO_DEFAULTS).blocks[0].target == 'Đã đến lúc.'
    assert len(fallback.tokens) == 2
    assert processor.last_timings['fallback_background_ms'] == 68
    # Different text pixels must not accept the completed OCR for the old crop.
    changed = image.copy()
    from PIL import ImageDraw
    ImageDraw.Draw(changed).text((820, 895), 'Different words now', fill='white')
    assert not processor.process(changed, Engine({}), AUTO_DEFAULTS).blocks
    assert fallback.tokens[-1] != fallback.tokens[0]
    processor.process(image, Engine({}), {**AUTO_DEFAULTS, 'generation': 2})
    assert fallback.cancellations == 2


@pytest.mark.parametrize('contrast', [False, True])
def test_verified_scene_fragment_returns_full_target_without_cpu_retry(monkeypatch, contrast):
    image,_ = visual_fixture()
    seed=entry('Main_Mengzhou_3_6_30_10','This exact sentence identifies the current conversation.','Mở đoạn')
    full='Paragon Qingxiao, should disaster one day befall Xuanfang Hold, I hope you will raise your sword for its people.'
    fragment='should disaster one day befall Xuanfang Hold I hope you'
    class UnneededCpu:
        def recognize(self, crop):
            pytest.fail('Already verified excerpt must not wait for CPU OCR')
    processor=AutoWuwaProcessor(index(seed,entry('Main_Mengzhou_3_6_30_11',full,'Toàn câu Việt')),UnneededCpu())
    boxes=[box(fragment,x=500,y=890,w=920,h=32)]
    if contrast:
        monkeypatch.setattr(processor,'_rescue_dialogue',lambda *args: boxes)
    else:
        monkeypatch.setattr(processor,'_rescue_dialogue',lambda *args: pytest.fail('Unnecessary contrast'))
    response=dict([crop_response('dialogue', [] if contrast else boxes)])
    result=processor.process(image,Engine(response),{**AUTO_DEFAULTS,'context_hint':seed['text_key']})
    assert result.blocks and result.blocks[-1].partial
    assert result.blocks[-1].target=='Toàn câu Việt'
    assert processor.last_timings['cpu_calls']==0


def test_unique_context_excerpt_skips_full_top_two_global_fuzzy_ranking(monkeypatch):
    from ludiglot.core import auto_wuwa as module
    full='Paragon Qingxiao, should disaster one day befall Xuanfang Hold, I hope you will raise your sword for its people.'
    search=index(entry('Main_Mengzhou_3_6_30_11',full,'Bản đầy đủ'))
    search.set_context('Main_Mengzhou_3_6_30_11')
    monkeypatch.setattr(module.process,'extract',lambda *args,**kwargs: pytest.fail('Unneeded full fuzzy ranking'))
    result=search.resolve('should disaster one day befall Xuanfang Hold I hope you','dialogue')
    assert result and result.partial


def test_fuzzy_context_is_ranked_first_but_a_stronger_global_sentence_still_wins(monkeypatch):
    from ludiglot.core import auto_wuwa as module
    search=index(entry('Main_Mengzhou_3_6_20_1','Please stay here while I look for the missing cartridge.','Một'),
                 entry('Main_Other_3_6_21_1','Please stay here while I look for the missing cartridges.','Hai'))
    search.set_context('Main_Mengzhou_3_6_20_1')
    calls=[]; original=module.process.extract
    def ranked(query, keys, **kwargs):
        calls.append(keys is search._scope_keys)
        return original(query,keys,**kwargs)
    monkeypatch.setattr(module.process,'extract',ranked)
    assert search.resolve('Please stay here while Ilook for the missing cartridges.','dialogue') is None
    assert calls[0]  # Context is tried first, but cannot override the winner.


def test_new_speaker_is_detected_between_wide_discovery_ticks(monkeypatch):
    from ludiglot.core import auto_wuwa as module
    clock=[100.0]; monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    image,block=visual_fixture()
    engine=Engine(dict([crop_response('dialogue',[box(block.source,x=640,y=890,w=430,h=32)])]))
    processor=AutoWuwaProcessor(index(entry('Side_1',block.source,block.target)))
    assert not processor.process(Image.new('RGB',image.size,'gray'),engine,AUTO_DEFAULTS,backend='test').blocks
    clock[0]+=.25
    assert processor.process(image,engine,AUTO_DEFAULTS,backend='test').blocks


def test_tracking_dialogue_defers_information_but_glossary_keeps_immediate_priority(monkeypatch):
    from ludiglot.core import auto_wuwa as module
    clock=[100.0]; monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    image,block=visual_fixture()
    search,modal=glossary_fixture()
    story_entry=entry('Side_1',block.source,block.target)
    search=index(story_entry,entry('Term750048_Title','Ministry of Foreign Affairs','Phủ Ngoại Sự'),
        entry('Term750048_Desc','An administrative bureau based in Shaftpost Yard. Inspection Officers are responsible for recording and verifying goods and commodities entering the Hold.','Thông tin'))
    class Recording(Engine):
        def recognize_pipeline(self,image,**kwargs):
            seen.append(image.size)
            return super().recognize_pipeline(image,**kwargs)
    seen=[]
    engine=Recording(dict([crop_response('dialogue',[box(block.source,x=640,y=890,w=430,h=32)])]))
    processor=AutoWuwaProcessor(search)
    assert processor.process(image,engine,AUTO_DEFAULTS,backend='test').blocks
    clock[0]+=1.6
    changed=image.copy(); draw=ImageDraw.Draw(changed)
    draw.rectangle((500,150,501,250),fill='white')
    seen.clear()
    processor.process(changed,engine,AUTO_DEFAULTS,backend='test')
    assert crop_response('title',[])[0] not in seen and crop_response('document',[])[0] not in seen
    # Open Glossary while story remains behind it, before next wide discovery.
    engine.responses.update(modal)
    changed.putpixel((450,150),(250,250,250))
    clock[0]+=.25
    result=processor.process(changed,engine,AUTO_DEFAULTS,backend='test')
    assert result.detected_kinds==('glossary',) and all(b.kind=='glossary' for b in result.blocks)


def test_process_timings_reset_each_frame_and_measure_ocr_lookup_calls():
    image,block=visual_fixture()
    engine=Engine(dict([crop_response('dialogue',[box(block.source,x=640,y=890,w=430,h=32)])]))
    processor=AutoWuwaProcessor(index(entry('Side_1',block.source,block.target)))
    processor.process(image,engine,AUTO_DEFAULTS,backend='test')
    assert processor.last_timings['windows_calls']>0 and processor.last_timings['cpu_calls']==0
    assert processor.last_timings['process_ms']>=processor.last_timings['lookup_ms']>=0
    processor.process(image,engine,AUTO_DEFAULTS,backend='test')
    assert processor.last_timings['windows_calls']==0 and processor.last_timings['windows_ocr_ms']==0


def test_alternating_answer_and_dialogue_in_same_scene_keeps_lookup_memo(monkeypatch):
    answer=entry('Main_Mengzhou_3_6_50_16','This is a verified response in the conversation.','Đáp án')
    story=entry('Main_Mengzhou_3_6_50_15','These are some verified words of the story conversation.','Thoại')
    search=index(answer,story)
    search.set_context(answer['text_key'])
    assert search.resolve(story['official_en'],'dialogue')
    search.set_context(answer['text_key'])
    monkeypatch.setattr(search,'_resolve',lambda *args,**kwargs:pytest.fail('Identical scene must keep memo'))
    assert search.resolve(story['official_en'],'dialogue').vietnamese=='Thoại'
    assert search.context_hint==story['text_key']
    search.set_context('')
    assert not search._memo and search._scope_signature is None


def test_player_name_template_is_indexed_and_compiled_once_not_global_fuzzy(monkeypatch):
    from ludiglot.core import auto_wuwa as module
    search=index(entry('Main_1','There you are at last, {PlayerName}.','Cuối cùng {PlayerName} cũng đến.'),
                 entry('Main_2','We researched a completely different archaeological site, {PlayerName}.','Khác'))
    monkeypatch.setattr(module.process,'extract',lambda *a,**kw:pytest.fail('Exact observed-name grammar should precede fuzzy'))
    monkeypatch.setattr(module.re,'compile',lambda *a,**kw:pytest.fail('Templates must be compiled at index construction'))
    found=search.resolve('There you are at last, Ossmoc.','dialogue')
    assert found.vietnamese=='Cuối cùng Ossmoc cũng đến.'


def test_template_word_index_preserves_embedded_names_and_ambiguous_grammar():
    english='We finally discovered a new Guild{PlayerName}member today.'
    search=index(entry('Main_1',english,'Người {PlayerName} mới.'))
    found=search.resolve('We finally discovered a new GuildBobmember today.','dialogue')
    assert found and found.vietnamese=='Người Bob mới.'
    assert not search.resolve('We finally discovered a new GuildBobmember someday.','dialogue')
    ambiguous=index(entry('Main_1','There you are at last, {PlayerName}.','Một {PlayerName}'),
                    entry('Main_2','There you are at last, {PlayerName}.','Hai {PlayerName}'))
    assert ambiguous._player_template('There you are at last, Bob.','dialogue') is None
    exact=index(entry('Main_1','There you are at last, {PlayerName}.','Tên {PlayerName}'),
                entry('Main_2','There you are at last, Bob.','Câu Bob khác'))
    assert exact.resolve('There you are at last, Bob.','dialogue').vietnamese=='Câu Bob khác'


def test_losing_speaker_discovers_document_without_waiting_for_wide_tick(monkeypatch):
    from ludiglot.core import auto_wuwa as module
    clock=[100.0]; monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    image,block=visual_fixture()
    search=index(entry('Side_1',block.source,block.target),
        entry('InfoDisplay_1_Title','Survey Team Diary','Nhật ký'),
        entry('InfoDisplay_1_Text','First long independent paragraph about the survey team expedition. Second long independent paragraph about the forest and research. Third long independent paragraph about their ecological discoveries.','Toàn văn'))
    engine=Engine(dict([crop_response('dialogue',[box(block.source,x=640,y=890,w=430,h=32)])]))
    processor=AutoWuwaProcessor(search)
    processor.process(image,engine,AUTO_DEFAULTS,backend='test')
    engine.responses.update(dict([crop_response('title',[box('Survey Team Diary',x=650,y=100)]),
        crop_response('document',[box(text,x=460,y=220+i*50,w=900,h=30) for i,text in enumerate([
            'First long independent paragraph about the survey team expedition.',
            'Second long independent paragraph about the forest and research.',
            'Third long independent paragraph about their ecological discoveries.'])])]))
    clock[0]+=.25
    frame=processor.process(Image.new('RGB',image.size,'gray'),engine,AUTO_DEFAULTS,backend='test')
    assert frame.document and frame.document.target=='Toàn văn'
