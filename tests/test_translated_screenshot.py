import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PIL import Image
from PyQt6.QtGui import QImage,QColor
from PyQt6.QtWidgets import QApplication
from ludiglot.ui.translated_screenshot import compose_game_image,save_screenshot_png


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def layer(width,height,color,dpr=1):
    result=QImage(width,height,QImage.Format.Format_RGBA8888)
    result.fill(QColor(color)); result.setDevicePixelRatio(dpr)
    return result


@pytest.mark.parametrize('dpr',[1,1.25,1.5,2])
def test_physical_frame_alpha_and_dpi_do_not_shift_overlay(app,dpr):
    bounds={'left':-1920,'top':80,'width':100,'height':80}
    physical={'left':-1900,'top':100,'width':40,'height':20}
    image=compose_game_image(Image.new('RGB',(100,80),'red'),bounds,
        [(layer(round(40*dpr),round(20*dpr),'#8000ff00',dpr),physical)])
    assert image.size().width()==100 and image.size().height()==80
    assert image.pixelColor(10,10)==QColor('red')
    pixel=image.pixelColor(30,25)
    assert abs(pixel.red()-127)<=1 and abs(pixel.green()-128)<=1 and pixel.alpha()==255
    assert image.pixelColor(61,25)==QColor('red')


def test_clipped_reader_keeps_original_mapping_not_stretched_to_crop(app):
    bounds={'left':0,'top':0,'width':40,'height':20}
    reader=layer(40,20,'green')
    for x in range(20):
        for y in range(20):reader.setPixelColor(x,y,QColor('blue'))
    physical={'left':-20,'top':0,'width':40,'height':20}
    image=compose_game_image(Image.new('RGB',(40,20),'red'),bounds,[(reader,physical)])
    assert image.pixelColor(0,10)==QColor('green')
    assert image.pixelColor(19,10)==QColor('green')
    assert image.pixelColor(20,10)==QColor('red')


def test_empty_or_outside_layer_cannot_replace_game(app):
    bounds={'left':0,'top':0,'width':40,'height':20}
    image=compose_game_image(Image.new('RGB',(40,20),'red'),bounds,
        [(QImage(),bounds),(layer(10,10,'green'),{'left':100,'top':100,'width':10,'height':10})])
    assert image.pixelColor(10,10)==QColor('red')


def test_size_mismatch_fails_closed(app):
    with pytest.raises(ValueError):
        compose_game_image(Image.new('RGB',(40,20)),{'left':0,'top':0,'width':50,'height':20})


def test_pngs_round_trip_are_unique_and_atomic(app,tmp_path):
    image=layer(40,20,'#123456')
    first=save_screenshot_png(image,tmp_path/'screenshots')
    second=save_screenshot_png(image,tmp_path/'screenshots')
    assert first!=second and QImage(str(first)).pixelColor(10,10)==QColor('#123456')
    assert sorted(p.suffix for p in first.parent.iterdir())==['.png','.png']
