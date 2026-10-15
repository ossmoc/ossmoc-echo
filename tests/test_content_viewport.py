from types import SimpleNamespace
import numpy as np
from PIL import Image, ImageDraw
import pytest
from ludiglot.core.content_viewport import letterbox_bounds,ViewportTracker
from ludiglot.core.auto_wuwa import AutoTextIndex,AutoWuwaProcessor,SEARCH_BANDS
from ludiglot.core.desktop_models import AUTO_DEFAULTS


def scene(width=1920,height=1080,bar=135):
    image=Image.new('RGB',(width,height),'black')
    pixels=np.zeros((height-2*bar,width,3),dtype='uint8')
    pixels[:,:,0]=30+np.arange(width,dtype='uint16')%100
    pixels[:,:,1]=60
    pixels[:,:,2]=90
    image.paste(Image.fromarray(pixels),(0,bar))
    return image


def test_balanced_bars_need_two_observations_and_reset_on_resize():
    image=scene()
    assert letterbox_bounds(image)==(0,135,1920,810)
    tracker=ViewportTracker()
    assert tracker.update(image)==(0,0,1920,1080)
    assert tracker.update(image)==(0,135,1920,810)
    resized=scene(1280,720,90)
    assert tracker.update(resized)==(0,0,1280,720)
    assert tracker.update(resized)==(0,90,1280,540)
    assert tracker.update(scene(bar=0))==(0,0,1920,1080)


@pytest.mark.parametrize('color',['black','#050608','#111112'])
def test_dark_scene_and_text_on_black_are_not_letterbox(color):
    image=Image.new('RGB',(1920,1080),color)
    ImageDraw.Draw(image).text((800,900),'Subtitles on a dark scene',fill='white')
    assert letterbox_bounds(image)==(0,0,1920,1080)


def test_asymmetric_black_area_is_not_cropped():
    image=scene()
    ImageDraw.Draw(image).rectangle((0,945,1919,1030),fill='#123456')
    assert letterbox_bounds(image)==(0,0,1920,1080)


def test_report_geometry_is_recognized_and_mapped_back_to_client():
    image=scene()
    # The actual report places speaker at 742..770 and dialogue at 803..854.
    draw=ImageDraw.Draw(image)
    for x in range(860,1050,20):
        draw.rectangle((x,745,x+10,768),fill='#ffe76b')
    english='This is a verified dialogue line in the letterboxed conversation.'
    database={'line':{'matches':[{'source_json':'lang_multi_text.db','text_key':'Main_Test_1_1_1_1',
        'official_en':english,'official_vi':'Đây là bản dịch được xác nhận.','translation_source':'wahu_exact_id_reviewed'}]}}
    class Engine:
        def recognize_pipeline(self,crop,**_):
            fx,fy,fw,fh=SEARCH_BANDS['dialogue']
            origin=(round(fx*1920),round(fy*810))
            expected=(round((fx+fw)*1920)-origin[0],round((fy+fh)*810)-origin[1])
            boxes=[]
            if crop.size==expected:
                x,y=500-origin[0],803-135-origin[1]
                boxes=[{'text':english,'box':[[x,y],[x+920,y],[x+920,y+30],[x,y+30]]}]
            return SimpleNamespace(boxes=boxes)
    processor=AutoWuwaProcessor(AutoTextIndex(database))
    processor.process(image,Engine(),AUTO_DEFAULTS,backend='test',force=True)
    result=processor.process(image,Engine(),AUTO_DEFAULTS,backend='test',force=True)
    assert result.blocks and result.blocks[0].bounds==(500,803,920,30)
    assert result.blocks[0].viewport==(0,135,1920,810)
    # Source text alone must not remove WuWa's gold-speaker gate.
    ImageDraw.Draw(image).rectangle((700,730,1200,780),fill='#334455')
    result=processor.process(image,Engine(),AUTO_DEFAULTS,backend='test',force=True)
    assert not result.blocks
