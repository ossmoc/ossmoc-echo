"""Export game pixels plus Qt-rendered translation; never disable exclusion."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QRectF, QSaveFile, QIODevice
from PyQt6.QtGui import QImage, QPainter


def compose_game_image(game, bounds: dict, layers=()) -> QImage:
    """Composite physical screen rectangles, independent of each Qt layer DPR.

    Draw at the original destination then clip to the game image. Intersecting
    the destination first would stretch an off-screen reader into the crop.
    No desktop capture, widget visibility/focus changes, or OCR calls here.
    """
    width, height = bounds['width'], bounds['height']
    if game.size != (width, height) or width <= 0 or height <= 0:
        raise ValueError('Kích thước ảnh game đã thay đổi. Hãy thử chụp lại.')
    rgba = game.convert('RGBA')
    output = QImage(rgba.tobytes(), width, height, width*4, QImage.Format.Format_RGBA8888).copy()
    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        for image, physical in layers:
            if image.isNull() or physical['width'] <= 0 or physical['height'] <= 0:
                continue
            source = image.copy()
            source.setDevicePixelRatio(1)
            destination = QRectF(physical['left']-bounds['left'], physical['top']-bounds['top'],
                                 physical['width'], physical['height'])
            painter.drawImage(destination, source)
    finally:
        painter.end()
    return output


def save_screenshot_png(image: QImage, directory: Path) -> Path:
    """Unique atomic output, only when explicitly requested by the user."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%fZ')
    path = directory / f'OssmocEcho-{stamp}-{uuid4().hex[:6]}.png'
    output = QSaveFile(str(path))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    if not image.save(output, 'PNG'):
        output.cancelWriting()
        raise OSError('Không ghi được ảnh PNG.')
    if not output.commit():
        raise OSError(output.errorString())
    return path
