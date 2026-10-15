from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from ludiglot.core.app_paths import AppPaths
from ludiglot.core.preferences import atomic_write_json
from ludiglot.core.usage_help import USAGE_GUIDE


def run_desktop(config_path: Path | None = None, smoke_report: Path | None = None, qa_images=()) -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Ossmoc Echo")
    app.setFont(QFont("Segoe UI", 10))
    app.setQuitOnLastWindowClosed(False)
    paths = AppPaths.discover(config_path)
    key = "Ludiglot-" + hashlib.sha256(str(paths.config_path).casefold().encode()).hexdigest()[:16]
    socket = QLocalSocket()
    if smoke_report is None:
        socket.connectToServer(key)
        if socket.waitForConnected(500):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(500)
            return
    server = QLocalServer(app)
    if smoke_report is None and not server.listen(key):
        QLocalServer.removeServer(key)
        if not server.listen(key):
            QMessageBox.critical(None, "Ossmoc Echo", "Không tạo được khóa một phiên chạy; hãy kiểm tra app đã mở.")
            return
    try:
        from ludiglot.ui.desktop_controller import DesktopController
        from ludiglot.ui.control_panel import ControlPanel
        controller = DesktopController(paths, auto_only=True)
        panel = ControlPanel(controller)
    except Exception as exc:
        if smoke_report:
            atomic_write_json(smoke_report, {"success": False, "error": str(exc)})
        else:
            QMessageBox.critical(None, "Không mở được Ossmoc Echo", f"{exc}\nCấu hình: {paths.config_path}\nNếu cấu hình hỏng, giữ bản sao trước khi chỉnh sửa.")
        return

    def show_panel():
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def connection():
        peer = server.nextPendingConnection()
        if peer:
            peer.readyRead.connect(lambda: (peer.readAll(), show_panel(), peer.disconnectFromServer()))
            if peer.bytesAvailable():
                show_panel()
                peer.disconnectFromServer()
    server.newConnection.connect(connection)
    tray = QSystemTrayIcon(app)
    tray.setIcon(app.style().standardIcon(app.style().StandardPixmap.SP_ComputerIcon))
    tray.setToolTip("Ossmoc Echo — Wuthering Waves")
    menu = QMenu()
    for title, callback in (("Mở Ossmoc Echo", show_panel), ("Bắt đầu / tạm dừng dịch", controller.toggle_realtime)):
        menu.addAction(title).triggered.connect(callback)
    menu.addSeparator()
    menu.addAction("Thoát Ossmoc Echo").triggered.connect(panel._quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: show_panel() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
    tray.show()
    app.aboutToQuit.connect(controller.close)
    panel.show()
    if not controller.raw.get("desktop_onboarding_done") and not smoke_report:
        def onboarding():
            QMessageBox.information(panel, "Bắt đầu với Ossmoc Echo", USAGE_GUIDE)
            controller.raw["desktop_onboarding_done"] = True
            controller.store.update_values(desktop_onboarding_done=True)
        QTimer.singleShot(400, onboarding)
    if smoke_report:
        smoke_report.parent.mkdir(parents=True, exist_ok=True)
        completed = False
        def finish(timed_out=False):
            nonlocal completed
            if completed:
                return
            if not controller.ready and not timed_out:
                return
            completed = True
            controller.preview_overlay()
            def report():
                ocr_check = {"success": False}
                if controller.ready:
                    try:
                        import re
                        from PIL import Image, ImageDraw, ImageFont
                        candidate = next(m for group in controller.matcher.db.values() for m in group.get("matches", [])
                            if m.get("official_vi") and re.fullmatch(r"[A-Za-z][A-Za-z0-9 ,.!?'\-]{25,80}", m.get("official_en", "")))
                        picture = Image.new("RGB", (1800, 150), "white")
                        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 36)
                        ImageDraw.Draw(picture).text((20, 30), candidate["official_en"], font=font, fill="black")
                        recognized = controller.engine.recognize_pipeline(picture, backend="windows")
                        matched = controller.matcher.match(recognized.lines)
                        from ludiglot.core.desktop_models import OcrRegion
                        shaped = controller._shape(matched, OcrRegion("smoke", "Kiểm thử")) if matched else {}
                        ocr_check = {"success": shaped.get("target") == controller._plain(candidate["official_vi"]),
                            "source": candidate["official_en"], "recognized": recognized.lines, "target": shaped.get("target")}
                    except Exception as exc:
                        ocr_check["error"] = str(exc)
                controller.overlay.verify_capture_exclusion()
                panel.grab().save(str(smoke_report.with_suffix(".panel.png")))
                ui_check = {'app_version':controller.app_version,
                            'english_data_note':panel.english_data_note.text(),
                            'has_english_data':controller.has_english_data,
                            'app_update_summary':controller.app_update_summary,
                            'translation_label':panel.translation_version_label.text().split('\n')[0],
                            'pages':[panel.navigation.item(i).text() for i in range(panel.navigation.count())],
                            'brand':panel.windowTitle(), 'manual_regions_removed':not hasattr(panel,'region_table'),
                            'only_echo_hotkeys':all(b.name in {'capture','screenshot'} for b in controller._bindings(controller.raw,controller.regions)),
                            'auto_only':controller.auto_only and controller.auto_mode}
                for page_number in range(panel.pages.count()):
                    panel.navigation.setCurrentRow(page_number); app.processEvents()
                    panel.grab().save(str(smoke_report.with_suffix(f'.page-{page_number+1}.png')))
                panel.navigation.setCurrentRow(0)
                from ludiglot.core.window_capture import WindowTarget
                from ludiglot.ui.game_picker import GamePicker
                picker=GamePicker([WindowTarget(1,1,'Wuthering Waves','Client.exe','Game'),
                    WindowTarget(2,2,'Other window','Other.exe','Other')],lambda:[])
                picker.show(); app.processEvents(); picker.grab().save(str(smoke_report.with_suffix('.game-picker.png')))
                ui_check['game_picker_count']=picker.windows.count(); picker.hide(); picker.deleteLater()
                controller.overlay.grab().save(str(smoke_report.with_suffix(".overlay.png")))
                auto_check = {"success": False}
                if controller.ready:
                    try:
                        panel.hide()
                        from ludiglot.ui.desktop_smoke import check_auto_windows
                        auto_check = check_auto_windows(controller, smoke_report, qa_images)
                    except Exception as exc:
                        auto_check["error"] = str(exc)
                atomic_write_json(smoke_report, {"success": controller.ready, "ui_check":ui_check,"status": controller.status, "regions": len(controller.regions), "capture_excluded": controller.overlay.capture_excluded, "ocr_check": ocr_check, "auto_check": auto_check, "hotkey_error": controller.hotkey_error, "frozen": bool(getattr(sys, "frozen", False)), "config": str(paths.config_path), "summary": controller.data_summary})
                panel._quit()
            QTimer.singleShot(650, report)
        controller.data_changed.connect(finish)
        QTimer.singleShot(45000, lambda: finish(True))
    app.exec()
    tray.hide()
    server.close()

