from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
import html
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QTextDocument
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ludiglot.core.app_paths import AppPaths
from ludiglot.version import APP_VERSION
from ludiglot.core.usage_help import MISSING_ENGLISH_DATA, USAGE_GUIDE
from ludiglot.core.config import load_config
from ludiglot.core.desktop_models import AUTO_DEFAULTS, OcrRegion, display_signature, migrate_desktop_settings, count_translation_coverage, normalize_auto_settings
from ludiglot.core.display_shaper import DisplayPreferences, shape_translation_display
from ludiglot.core.matcher import TextMatcher
from ludiglot.core.overlay_runtime import OverlayRuntimeCallbacks, create_overlay_ocr_engine
from ludiglot.core.preferences import ConfigJsonStore, atomic_write_json
from ludiglot.core.skill_param_resolver import SkillParamResolver
from ludiglot.ui.desktop_session import DesktopCaptureSession, SessionCallbacks
from ludiglot.ui.qt_capture_adapter import QtCaptureAdapter
from ludiglot.ui.hotkey_registrar import HotkeyBinding
from ludiglot.ui.qt_hotkey_adapter import WindowsNativeHotkeyAdapter
from ludiglot.ui.subtitle_overlay import SubtitleOverlay
from ludiglot.ui.subtitle_overlay import SubtitleOverlay as AutoFallbackOverlay
from ludiglot.core.auto_wuwa import AutoFrameResult, AutoPresenceTracker, AutoTextIndex, AutoWuwaProcessor
from ludiglot.ui.auto_overlay import AutoInlineOverlay, DocumentReader, logical_frame


class DesktopController(QObject):
    status_changed = pyqtSignal(str)
    regions_changed = pyqtSignal()
    preview_ready = pyqtSignal(object)
    history_changed = pyqtSignal()
    data_changed = pyqtSignal()
    log_message = pyqtSignal(str)
    _status = pyqtSignal(str)
    _initialized = pyqtSignal(object)
    _scan_result = pyqtSignal(object)
    _scan_preview = pyqtSignal(object)
    _session_state = pyqtSignal(object)
    _job_finished = pyqtSignal(object)

    def __init__(self, paths: AppPaths, *, auto_initialize: bool = True, auto_only: bool = False) -> None:
        super().__init__()
        self.auto_only = auto_only
        self.paths = paths
        paths.logs.mkdir(parents=True, exist_ok=True)
        self.store = ConfigJsonStore(paths.config_path)
        self._ensure_configuration()
        if not self.store.load_raw().get("desktop_config_version"):
            shutil.copy2(paths.config_path, paths.config_path.with_name("settings.before-desktop.json"))
        self.raw = migrate_desktop_settings(self.store.load_raw())
        if auto_only:
            if not self.raw.get('echo_ui_version'):
                backup = paths.config_path.with_name('settings.before-echo.json')
                if not backup.exists():
                    shutil.copy2(paths.config_path, backup)
            self.raw.update(echo_ui_version=1, regions=[], special_region_id='', profiles={},
                            hotkey_toggle='', hotkey_exception='', hotkey_exception_select='')
            self.raw['auto_wuwa'].update(mode='auto',show_bounds=False)
            self.raw.setdefault('story_memory', [])
            if not isinstance(self.raw['story_memory'], list):
                self.raw['story_memory'] = []
        self.store.update_values(**self.raw)
        self.regions = [OcrRegion.from_mapping(r, i) for i, r in enumerate(self.raw["regions"])]
        self.special_region_id = self.raw["special_region_id"]
        self.status = "Đang chuẩn bị OCR và dữ liệu…"
        self.ready = False
        self.history: list[dict] = []
        self.logs: list[str] = []
        self.data_summary = "Chưa tải dữ liệu."
        self.update_summary = "Chưa kiểm tra bản dịch mới."
        self.latest_release = None
        self.update_available = False
        self.app_version = APP_VERSION
        self.latest_app_release = None
        self.app_update_available = False
        self.app_update_summary = 'Chưa kiểm tra cập nhật app.'
        self._launch_update_check_done = False
        self.engine = self.matcher = self.param_resolver = None
        self._hotkeys = None
        self._hotkey_edit_depth = 0
        self._capturing_screenshot = False
        self._run_intent = False
        self._auto_revision = 0
        self.auto_processor = None
        self.auto_tracker = AutoPresenceTracker()
        self._last_auto_frame = None
        self._auto_history_signature = None
        self._auto_errors = 0
        self.hotkey_error = ""
        self._blocks: dict[str, dict] = {}
        self._displayed: list[dict] = []
        self._display_signature = None
        self._manual_display = False
        self._manual_region_id = None
        self._closed = False
        self._maintenance = False
        self._checking_updates = False
        self._init_token = 0
        self._job_cancel = threading.Event()
        self._focus_suspended = False
        self._edit_resume = False
        self._screen_fingerprint = None
        self._bound_windows = []
        self._logger = logging.getLogger("ludiglot.desktop." + uuid4().hex)
        handler = RotatingFileHandler(paths.logs / "desktop.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self.log_message.connect(self._record_log)
        self._status.connect(self._set_status)
        self._initialized.connect(self._on_initialized)
        self._scan_result.connect(self._on_result)
        self._scan_preview.connect(self._on_preview)
        self._session_state.connect(self._on_session_state)
        self._job_finished.connect(self._on_job_finished)
        self.config = self._load_config()
        self.capture_adapter = QtCaptureAdapter(self.config, self.log_message.emit)
        self._window_bounds = None
        self._last_capture_bounds = None
        self._background_hidden = False
        if hasattr(self.capture_adapter, "configure_window"):
            try:
                self.capture_adapter.configure_window(self.raw.get("window_capture", {}))
            except Exception as exc:
                self.log_message.emit(f"Chụp cửa sổ chưa sẵn sàng: {exc}")
        self.overlay = SubtitleOverlay(self.raw["overlay"])
        self.overlay.geometry_changed.connect(lambda geometry: self.update_overlay_settings(**geometry))
        self.overlay.capture_exclusion_changed.connect(self._on_capture_exclusion)
        self.auto_overlay = AutoInlineOverlay()
        self.auto_reader = DocumentReader()
        self.auto_fallback = AutoFallbackOverlay({"alignment": "left", "background": "black", "background_opacity": 95})
        self.auto_reader.geometry_changed.connect(lambda g: self.update_auto_settings(reader_geometry=g))
        self.auto_reader.font_changed.connect(lambda size: self.update_auto_settings(reader_font_size=size))
        for window in (self.auto_overlay, self.auto_reader, self.auto_fallback):
            window.capture_exclusion_changed.connect(self._auto_capture_changed)
        self.session = self._make_session()
        self._sync_session_regions()
        self._register_hotkeys()
        self.environment_timer = QTimer(self)
        self.environment_timer.timeout.connect(self._check_environment)
        self.environment_timer.start(1500)
        if auto_initialize:
            QTimer.singleShot(0, self.initialize)
            QTimer.singleShot(2500, lambda: self.check_updates(force=False))

    def _ensure_configuration(self) -> None:
        if self.paths.config_path.exists():
            # Never silently replace malformed user settings.
            json.loads(self.paths.config_path.read_text(encoding="utf-8-sig"))
            return
        raw = {
            "data_root": str(self.paths.data_root / "data"),
            "db_path": str(self.paths.data_root / "cache" / "game_text_db.json"),
            "image_path": str(self.paths.data_root / "cache" / "capture.png"),
            "auto_rebuild_db": False, "ocr_backend": "windows", "ocr_lang": "en",
            "ocr_mode": "cpu", "play_audio": False, "extract_audio": False,
            "use_game_paks": False, "game_platform": "Windows", "game_server": "OS",
            "game_languages": ["en", "zh-Hans"], "window_title": "Wuthering Waves",
            "translation_provider": None,
        }
        atomic_write_json(self.paths.config_path, raw)

    def _load_config(self):
        cfg = load_config(self.paths.config_path, validate_data=False, base_root=self.paths.config_base)
        cfg.auto_rebuild_db = False
        cfg.ocr_lang = "en"
        cfg.capture_mode = "region"
        cfg.ocr_debug_dump_input = False
        if cfg.ocr_backend == "auto":
            cfg.ocr_backend = "windows"
        try:
            from ludiglot.core.translation_update import TranslationUpdater
            manifest = TranslationUpdater(self.paths.updates).active_manifest()
            if manifest:
                cfg.db_path = Path(manifest["cache_path"])
                cfg.wahu_db_path = Path(manifest["database_path"]) if manifest.get("database_path") else None
                cfg.translation_provider = "wahu" if cfg.wahu_db_path else None
                if manifest.get("official_data_root"):
                    cfg.data_root = Path(manifest["official_data_root"])
        except (FileNotFoundError, KeyError):
            pass
        return cfg

    def _make_session(self):
        identity = uuid4().hex
        self._session_identity = identity
        def tagged(signal, payload):
            signal.emit({**payload, "session_id": identity})
        return DesktopCaptureSession(
            config_provider=lambda: self.config, ocr_engine_provider=lambda: self.engine,
            matcher_provider=lambda: self.matcher, capture_adapter=self.capture_adapter,
            callbacks=SessionCallbacks(status=self._status.emit, log=self.log_message.emit, error=self._status.emit,
                result=lambda p: tagged(self._scan_result, p), preview=lambda p: tagged(self._scan_preview, p), state_changed=lambda p: tagged(self._session_state, p)),
            auto_processor_provider=lambda: self.auto_processor,
            auto_settings_provider=lambda: {**self.raw["auto_wuwa"], "generation": self.session.generation},
        )

    def initialize(self, resume_after: bool = False) -> None:
        self.ready = False
        self._init_token += 1
        token = self._init_token
        self._set_status("Đang tải dữ liệu và chuẩn bị Windows OCR…")
        self.session.pause()
        self._clear_auto()

        def worker():
            try:
                cfg = self._load_config()
                if not cfg.db_path.is_file():
                    raise FileNotFoundError(MISSING_ENGLISH_DATA)
                db = json.loads(cfg.db_path.read_text(encoding="utf-8"))
                if not isinstance(db, dict) or not db:
                    raise ValueError("Dữ liệu tra cứu trống hoặc không hợp lệ.")
                if cfg.wahu_db_path:
                    from ludiglot.adapters.wuthering_waves.wahu import WahuTranslationProvider
                    import sqlite3
                    try:
                        repaired = WahuTranslationProvider(cfg.wahu_db_path, require_source_english=True).enrich(db, source_moves_only=True)
                        self.log_message.emit(f"Phục hồi {repaired.verified_source_moves:,} liên kết bản dịch từ lịch sử nguồn English/Chinese đã xác nhận (không sửa dữ liệu game).")
                    except (OSError, sqlite3.DatabaseError) as exc:
                        self.log_message.emit(f"Không phục hồi liên kết nguồn bản dịch; giữ cache đang dùng: {exc}")
                from ludiglot.core.light_ocr import LightDialogueOcr
                fallback = LightDialogueOcr(self.paths.resource_root/'models'/'light_ocr', self.log_message.emit)
                fallback.prepare_runtime()
                engine = create_overlay_ocr_engine(cfg, OverlayRuntimeCallbacks(log=self.log_message.emit))
                engine.initialize()
                if cfg.ocr_backend == "windows" and getattr(engine, "_windows_ocr", None) is None:
                    raise RuntimeError("Windows OCR English chưa sẵn sàng. Cài ngôn ngữ English (United States) trong Windows rồi mở lại app.")
                matcher = TextMatcher(db, gender_preference=cfg.gender_preference)
                matcher.set_logger(self.log_message.emit)
                params = SkillParamResolver(cfg.data_root / "ConfigDB" / "db_skill.db", logger=self.log_message.emit) if cfg.data_root else None
                auto_processor = AutoWuwaProcessor(AutoTextIndex(db), fallback=fallback)
                self._initialized.emit({"token": token, "config": cfg, "engine": engine, "matcher": matcher,
                    "params": params, "auto_processor": auto_processor, "count": len(db), "coverage": count_translation_coverage(db), "resume": resume_after})
            except Exception as exc:
                self._initialized.emit({"token": token, "error": str(exc)})
        threading.Thread(target=worker, daemon=True).start()

    def _on_initialized(self, payload):
        if self._closed or payload["token"] != self._init_token:
            return
        if "error" in payload:
            self._maintenance = False
            self._set_status(payload["error"])
            self.log_message.emit(payload["error"])
            self.data_changed.emit()
            return
        self.config, self.engine, self.matcher = payload["config"], payload["engine"], payload["matcher"]
        self.param_resolver = payload["params"]
        self.auto_processor = payload.get("auto_processor")
        self.capture_adapter.config = self.config
        self.ready = True
        self._maintenance = False
        self.data_summary = f"{payload['count']:,} nhóm tra cứu • OCR English • tên tiếng Anh\nDữ liệu: {self.config.db_path}"
        coverage = payload.get("coverage", {})
        self.data_summary += f"\n{coverage.get('vietnamese', 0):,} câu Việt • {coverage.get('english_fallback', 0):,} câu fallback English"
        self.data_summary += "\nNguồn game: " + str(self.raw.get("official_source_version") or "dữ liệu cục bộ; làm mới sau khi game cập nhật")
        try:
            manifest = self._updater().active_manifest()
            if manifest:
                self.data_summary += "\nBản Việt: " + str(manifest.get("release_tag") or "nhập offline / dữ liệu cục bộ")
                for warning in manifest.get("warnings", []):
                    self.data_summary += "\n⚠ " + re.sub(r'\bWAHU\b', 'bản dịch', str(warning), flags=re.I)
            else:
                self.data_summary += "\nBản Việt cục bộ sẵn có (chưa có manifest release)."
        except Exception as exc:
            self.log_message.emit(str(exc))
        key = self.raw.get("hotkey_capture") or "nút Bắt đầu"
        self._set_status(self.hotkey_error or (self.update_summary if self.latest_release else f"Sẵn sàng. Chọn game rồi dùng {key} để bắt đầu."))
        self._sync_session_regions()
        self.data_changed.emit()
        if payload.get("resume"):
            self.resume_realtime()

    @property
    def has_english_data(self):
        return self.config.db_path.is_file()

    def show_usage_guide(self):
        key = self.raw.get('hotkey_screenshot') or 'nút Chụp kèm bản dịch'
        QMessageBox.information(None, 'Hướng dẫn sử dụng Ossmoc Echo', USAGE_GUIDE +
            f'\n\nChụp kèm bản dịch: dùng {key}. Ảnh tự vào clipboard để Ctrl+V; '
            'PNG được lưu thêm trong userdata/screenshots cạnh app. Không cần dừng dịch.')

    def capture_translated_screenshot(self):
        """Export only the selected game, without disturbing OCR or focus."""
        if self._closed or self._capturing_screenshot:
            return None
        if not self.window_capture_mode:
            self._set_status('Chọn game trước khi chụp kèm bản dịch.')
            return None
        self._capturing_screenshot = True
        try:
            from ludiglot.ui.translated_screenshot import compose_game_image, save_screenshot_png
            # WGC already owns copied pixels; timeout zero never blocks the UI
            # waiting for a minimized/closed window or falls back to desktop.
            game = self.capture_adapter.window_capture.image(timeout=0)
            bounds = game.info.get('capture_bounds')
            if not bounds:
                raise ValueError('Chưa xác định được khung game. Hãy thử chụp lại.')
            layers = []
            skipped_geometry = self._last_capture_bounds != bounds and bool(self.auto_overlay.auto_blocks or self.auto_reader.document_id)
            # A moved/resized game must not reuse geometry from an older frame.
            # Background-hidden layers can be exported from the app's button,
            # but explicit user hiding/dismissal is always respected.
            if not self.overlay.user_hidden and self._last_capture_bounds == bounds:
                if self.auto_overlay.auto_blocks:
                    layers.append((self.auto_overlay.grab().toImage(), bounds))
                reader = self.auto_reader
                if reader.document_id and reader.dismissed_id != reader.document_id:
                    physical = reader.region_bounds_physical()
                    if physical:
                        layers.append((reader.grab().toImage(), physical))
            image = compose_game_image(game, bounds, layers)
            # Clipboard comes first: disk failures must not prevent Ctrl+V.
            QApplication.clipboard().setImage(image)
            try:
                path = save_screenshot_png(image, self.paths.data_root/'screenshots')
            except Exception as exc:
                self._set_status(f'Đã copy ảnh vào clipboard — Ctrl+V để dán. Chưa lưu được PNG: {exc}')
                return image
            self._set_status('Đã copy ảnh vào clipboard — Ctrl+V để dán. Đã lưu PNG.'+
                (' Khung game vừa đổi: ảnh này chưa kèm lớp dịch, chờ lượt dịch mới rồi chụp lại.' if skipped_geometry else ''))
            self.log_message.emit('Đã lưu ảnh chụp theo yêu cầu: '+str(path))
            return image
        except Exception as exc:
            self._set_status('Không chụp được ảnh game; clipboard cũ được giữ. '+str(exc))
            return None
        finally:
            self._capturing_screenshot = False

    @property
    def translation_version_summary(self):
        try:
            manifest = self._updater().active_manifest() or {}
        except Exception:
            manifest = {}
        detected = manifest.get('detected_game_version')
        game_data = (f'{detected} (tự nhận diện)' if detected and self.has_english_data
                     else self.raw.get('official_source_version') or
                     ('đã chuẩn bị (tự động)' if self.has_english_data else 'chưa chuẩn bị'))
        return (f"Bản dịch đang dùng: {manifest.get('release_tag') or 'chưa xác định'}\n"
                f"WuWa được bản dịch hỗ trợ: {manifest.get('translation_game_version') or 'chưa xác định'}\n"
                f"Dữ liệu game: {game_data}")

    @property
    def auto_mode(self):
        return self.raw["auto_wuwa"].get("mode") == "auto"

    @property
    def window_capture_mode(self):
        return self.raw.get("window_capture", {}).get("mode") == "window"

    def choose_game_window(self):
        from ludiglot.core.window_capture import list_windows
        from ludiglot.ui.game_picker import GamePicker
        windows = list_windows()
        if not windows:
            self._set_status("Không thấy cửa sổ ứng dụng. Mở game rồi thử lại.")
            return
        picker = GamePicker(windows, list_windows)
        if not picker.exec() or picker.selected_target is None:
            return
        target = picker.selected_target
        from ludiglot.core.window_capture import physical_geometry
        previous = copy.deepcopy(self.raw.get("window_capture", {"mode": "desktop"}))
        try:
            bounds, _ = physical_geometry(target)
            settings = {"mode": "window", "target": target.identity(), "anchor": bounds}
            self.stop_realtime()
            self.capture_adapter.configure_window(settings, target)
            self.raw["window_capture"] = settings
            self.store.update_values(window_capture=settings)
            self._window_bounds = bounds
            self._auto_revision += 1
            self._sync_session_regions()
            self.data_changed.emit()
            self._set_status("Đã kết nối game. Bấm Bắt đầu dịch.")
        except Exception as exc:
            # Restore the previous explicitly chosen source, never silently
            # leave the runtime source different from the saved configuration.
            try:
                self.capture_adapter.configure_window(previous)
            except Exception as restore_error:
                self.log_message.emit(f"Nguồn ảnh cũ chưa sẵn sàng: {restore_error}")
            self._set_status(f"Không chụp được ứng dụng: {exc}. Hãy chọn lại.")

    def use_desktop_capture(self):
        if self.auto_only:
            self._set_status('Chọn Wuthering Waves trong mục Tổng quát.')
            return
        self.stop_realtime()
        self.raw["window_capture"] = {"mode": "desktop"}
        self.capture_adapter.configure_window(self.raw["window_capture"])
        self.store.update_values(window_capture=self.raw["window_capture"])
        self._window_bounds = None
        self._background_hidden = False
        self._auto_revision += 1
        self._sync_session_regions()
        self.data_changed.emit()
        self._set_status("Đã chọn chụp màn hình. Chế độ này đọc desktop đang hiển thị; chọn ứng dụng để OCR khi Alt+Tab.")

    def _game_bounds(self):
        if self.window_capture_mode:
            try:
                return self.capture_adapter.window_capture.bounds()
            except Exception:
                return self._window_bounds or self.raw["window_capture"].get("anchor")
        return self.raw["auto_wuwa"].get("bounds")

    def _auto_region(self):
        settings = self.raw["auto_wuwa"]
        return OcrRegion("__auto_wuwa__", "Auto WuWa", "realtime" if self.auto_mode else "manual",
                         bounds=self._game_bounds(), screen=None if self.window_capture_mode else settings.get("screen"), revision=self._auto_revision)

    def update_auto_settings(self, **changes):
        allowed = {k: v for k, v in changes.items() if k in AUTO_DEFAULTS}
        if self.auto_only and 'mode' in allowed:
            allowed['mode'] = 'auto'
        if "mode" in allowed and allowed["mode"] not in ("regions", "auto"):
            return
        settings = {**self.raw["auto_wuwa"], **allowed}
        for key in ("font_size", "choice_font_size", "reader_font_size", "outline_width", "background_opacity"):
            if key in allowed:
                low, high = (0, 100) if key == "background_opacity" else (0, 8) if key == "outline_width" else (14, 64)
                try:
                    settings[key] = max(low, min(high, int(allowed[key])))
                except (TypeError, ValueError, OverflowError):
                    return
        from PyQt6.QtGui import QColor
        for key in ("background_color", "text_color", "outline_color"):
            if key in allowed and not QColor(str(allowed[key])).isValid():
                return
        structural = bool(set(allowed) & {"mode", "bounds", "screen", "dialogue", "walking", "choices", "document", "glossary", "transition"})
        if structural:
            self.session.pause()
            self._run_intent = False
            self._auto_revision += 1
            self._clear_auto()
        self.raw["auto_wuwa"] = settings
        self.store.update_values(auto_wuwa=settings)
        if structural:
            self._sync_session_regions()
        elif self._last_auto_frame:
            self._display_auto(self._last_auto_frame)
        self.data_changed.emit()

    def set_dialogue_context(self, key):
        key = str(key).strip()
        # Validation is read-only: the shared worker adopts the saved hint on
        # its next frame, so an in-flight job is never mutated by the GUI.
        if key and (not self.auto_processor or not self.auto_processor.index.valid_context(key)):
            self._set_status("ID ngữ cảnh chưa có bản Việt đã xác nhận trong dữ liệu đang dùng.")
            return False
        import re
        from ludiglot.core.story_catalog import dialogue_scene
        if key and not dialogue_scene(key):
            self._set_status("ID này không phải đoạn hội thoại Main/Side được hỗ trợ.")
            return False
        self._auto_revision += 1
        self._clear_auto()
        self.update_auto_settings(context_hint=key)
        self._sync_session_regions()
        self._set_status("Đã đặt gợi ý ngữ cảnh; vẫn cần OCR xác nhận." if key else "Đã xóa gợi ý ngữ cảnh.")
        return True

    @property
    def story_options(self):
        return self.auto_processor.index.story_catalog if self.auto_processor else ()

    def choose_story(self, group=''):
        from ludiglot.core.story_catalog import dialogue_scene
        options = {option.key:option.seed for option in self.story_options}
        memories = self.raw.get('story_memory', [])
        for memory in memories:
            scene = dialogue_scene(memory.get('key','')) if isinstance(memory,dict) else None
            if scene and memory.get('kind')=='side':
                options.setdefault(scene[0],memory['key'])
        if group and (group not in options or not self.auto_processor
                      or not self.auto_processor.index.valid_context(options[group])):
            self._set_status('Phần cốt truyện này chưa có dữ liệu hợp lệ. Hãy chọn Tự nhận diện.')
            return False
        self._auto_revision += 1
        self._clear_auto()
        self.update_auto_settings(story_group=group, context_hint=options.get(group,''))
        self._sync_session_regions()
        self._set_status('Đã chọn cốt truyện; app vẫn tự xác nhận câu trong game.' if group else 'Đã bật tự nhận diện cốt truyện.')
        return True

    def _remember_story(self, block):
        from ludiglot.core.story_catalog import dialogue_scene, story_label
        scene = dialogue_scene(block.identity[1])
        if not scene:
            return
        prefix = scene[0]
        memory = {'group':prefix,'key':block.identity[1],
                  'label':story_label(prefix)[0], 'kind':'side' if prefix.startswith('Side_') else 'main',
                  'excerpt':self._plain(block.source)[:180]}
        memories = self.raw.get('story_memory', [])
        memories = [m for m in memories if isinstance(m,dict) and m.get('group')!=prefix]
        self.raw['story_memory'] = ([memory]+memories)[:10]

    def select_auto_screen(self, index):
        if self.window_capture_mode:
            self._set_status("Auto đang dùng toàn bộ cửa sổ đã chọn. Bấm Dùng màn hình để chuyển về khung cố định.")
            return
        import mss
        with mss.mss() as capture:
            monitors = capture.monitors[1:]
            if index is None or not 0 <= int(index) < len(monitors):
                self._set_status("Không tìm được màn hình Auto.")
                return
            bounds = dict(monitors[int(index)])
        self.update_auto_settings(bounds=bounds, screen=self._screen_info(bounds))

    def select_auto_frame(self):
        if self.window_capture_mode:
            self._set_status("Auto đang đọc toàn bộ cửa sổ game, không cần khoanh khung. Vùng quét tay khoanh ở trang Vùng OCR.")
            return
        self.stop_realtime()
        snapshot = self.capture_adapter.capture_desktop_snapshot()
        selected = self.capture_adapter.select_region(snapshot)
        if selected:
            bounds = {k: int(getattr(selected, k)) for k in ("left", "top", "width", "height")}
            screen = self._screen_info(bounds)
            if screen and bounds["left"] >= screen["left"] and bounds["top"] >= screen["top"] and bounds["left"]+bounds["width"] <= screen["left"]+screen["width"] and bounds["top"]+bounds["height"] <= screen["top"]+screen["height"]:
                self.update_auto_settings(bounds=bounds, screen=screen)
            else:
                self._set_status("Khung Auto cần nằm trọn trên một màn hình.")

    def preview_auto(self):
        if not self.ready or self._maintenance or not self.auto_processor:
            self._set_status("OCR/dữ liệu Auto chưa sẵn sàng.")
            return
        self._sync_session_regions()
        if self._region_problem(self._auto_region()):
            self._set_status("Chọn lại màn hình/khung Auto trước khi quét thử.")
            return
        self.session.request_scan("__auto_wuwa__", preview=True)

    def _clear_auto(self):
        self._auto_errors = 0
        self.auto_tracker.reset()
        self._last_auto_frame = None
        self._last_capture_bounds = None
        self.auto_overlay.auto_blocks = ()
        self.auto_overlay.reset_presentation()
        self.auto_overlay.hide()
        self.auto_reader.set_document(None, {}, {})
        self.auto_fallback.set_content([])
        self.auto_fallback.hide()

    def _auto_capture_changed(self, confirmed):
        if self.window_capture_mode:
            return  # HWND capture does not composite other windows into game pixels.
        if not confirmed and self.auto_mode and self.session.running:
            self.session.pause()
            self._run_intent = False
            self._clear_auto()
            self._set_status("Không xác nhận được capture exclusion. Auto đã dừng; dùng vùng quét tay ngoài phụ đề.")

    def _auto_safe_to_start(self):
        if self.auto_only and not self.window_capture_mode:
            self._set_status('Chọn game trước khi bắt đầu dịch.')
            return False
        region = self._auto_region()
        self.overlay.close()
        problem = self._region_problem(region)
        if problem:
            self._set_status("Auto: " + problem)
            return False
        if self.window_capture_mode:
            return True
        # Never capture another visible app-owned overlay without verification.
        self.auto_overlay.set_frame(region.bounds, (), self.raw["auto_wuwa"])
        self.auto_overlay.show()
        if not self.auto_overlay.verify_capture_exclusion():
            self.auto_overlay.hide()
            self._set_status("Không loại được overlay khỏi ảnh chụp; Auto chưa được bật. Dùng vùng quét tay.")
            return False
        return True

    def _display_auto(self, frame):
        bounds = self._last_capture_bounds or self._game_bounds()
        if not bounds:
            return
        if self.window_capture_mode:
            import win32gui
            target = self.capture_adapter.window_capture.target
            foreground = win32gui.GetForegroundWindow()
            own_reader = self.auto_reader.isVisible() and foreground == int(self.auto_reader.winId())
            self._background_hidden = not target or (foreground != target.hwnd and not own_reader)
        hidden = self.overlay.user_hidden or self._background_hidden
        self.auto_overlay.set_frame(bounds, frame.blocks if not frame.document else (), self.raw["auto_wuwa"])
        if not hidden and self.auto_overlay.auto_blocks:
            self.auto_overlay.show()
        else:
            self.auto_overlay.hide()
        self.auto_reader.set_document(frame.document, bounds, self.raw["auto_wuwa"], hidden=hidden)
        # No top fallback, including answers. If a target cannot fit safely,
        # leave English visible and report in the control panel instead.
        self.auto_fallback.user_hidden = hidden
        self.auto_fallback.set_content([])
        self.auto_fallback.hide()
        if self.window_capture_mode:
            return
        for window in (self.auto_overlay, self.auto_reader, self.auto_fallback):
            if window.isVisible() and not window.capture_excluded:
                if not window.verify_capture_exclusion():
                    self._auto_capture_changed(False)
                    return

    def _record_log(self, message):
        self.logs.append(message)
        self.logs[:] = self.logs[-500:]
        self._logger.info(message)

    def _set_status(self, message):
        translations = {"捕获中…": "Đang chụp vùng OCR…", "就绪": "Sẵn sàng", "OCR 未识别到文本": "Không tìm thấy chữ trong vùng OCR", "未提取到可用文本": "Chưa tìm được câu khớp đủ tin cậy"}
        self.status = translations.get(message, message)
        self.status_changed.emit(self.status)

    def _find_region(self, region_id):
        if region_id == "__auto_wuwa__":
            return self._auto_region()
        return next((r for r in self.regions if r.id == region_id), None)

    def _save_regions(self, *, register=True):
        self.regions = [replace(r, order=i) for i, r in enumerate(self.regions)]
        values = {"regions": [r.to_dict() for r in self.regions], "special_region_id": self.special_region_id}
        self.raw.update(values)
        self.store.update_values(**values)
        self._sync_session_regions()
        if register:
            self._register_hotkeys()
        self.regions_changed.emit()
        if not self._manual_display:
            self._render_realtime()

    def add_region(self, name="Vùng OCR", mode="realtime"):
        region = OcrRegion(uuid4().hex, name, "manual" if mode == "manual" else "realtime", order=len(self.regions))
        self.regions.append(region)
        if mode == "manual" and not self.special_region_id:
            self.special_region_id = region.id
        self._save_regions()
        return region.id

    def update_region(self, region_id, **changes):
        region = self._find_region(region_id)
        if not region:
            return
        allowed = {k: v for k, v in changes.items() if k in {"name", "mode", "enabled", "bounds", "screen", "hotkey"}}
        candidate = OcrRegion.from_mapping({**region.to_dict(), **allowed, "revision": region.revision + 1}, region.order)
        if "hotkey" in allowed:
            from ludiglot.ui.hotkey_editor import canonical_hotkey
            try:
                candidate = replace(candidate, hotkey=canonical_hotkey(candidate.hotkey))
            except ValueError as exc:
                self._set_status(str(exc))
                return False
        if candidate.hotkey and not self._hotkey_valid(candidate.hotkey, excluded_region=region_id):
            return False
        candidates = [candidate if r.id == region_id else r for r in self.regions]
        if candidate.hotkey != region.hotkey and not self._register_hotkeys(regions=candidates, rollback=True):
            return False
        self.regions = candidates
        self._blocks.pop(region_id, None)
        if self._manual_region_id == region_id:
            self.clear_output()
        self._save_regions(register=candidate.hotkey == region.hotkey)
        return True

    def region_hotkey_action(self, region_id):
        region = self._find_region(region_id)
        if not region:
            return
        if region.mode == "manual":
            if not region.enabled:
                self._set_status(f"{region.name}: vùng đang tắt.")
                return
            self.scan_region(region_id)
            return
        if not region.enabled and self._region_problem(region):
            self._set_status(f"{region.name}: {self._region_problem(region)}")
            return
        was_running = self.session.running
        self.update_region(region_id, enabled=not region.enabled)
        # set_regions invalidates stale work but keeps global running/hold state.
        # Restart only a previously active regional loop, never a paused hold.
        if was_running and not self.session.pinned:
            self.session.resume_intent()
        self._set_status(f"{region.name}: {'đã tắt' if region.enabled else 'đã bật'} vùng realtime.")

    def delete_region(self, region_id):
        self.regions = [r for r in self.regions if r.id != region_id]
        self._blocks.pop(region_id, None)
        if self._manual_region_id == region_id:
            self.clear_output()
        if self.special_region_id == region_id:
            self.special_region_id = next((r.id for r in self.regions if r.mode == "manual"), "")
        self._save_regions()

    def move_region(self, region_id, delta):
        index = next((i for i, r in enumerate(self.regions) if r.id == region_id), -1)
        target = index + delta
        if index >= 0 and 0 <= target < len(self.regions):
            self.regions[index], self.regions[target] = self.regions[target], self.regions[index]
            self._save_regions()

    def set_special_region(self, region_id):
        region = self._find_region(region_id)
        if region and region.mode == "manual":
            self.special_region_id = region_id
            self.raw["special_region_id"] = region_id
            self.store.update_values(special_region_id=region_id)

    def _screen_info(self, bounds):
        import mss
        cx, cy = bounds["left"] + bounds["width"] / 2, bounds["top"] + bounds["height"] / 2
        with mss.mss() as capture:
            for i, screen in enumerate(capture.monitors[1:]):
                if screen["left"] <= cx < screen["left"] + screen["width"] and screen["top"] <= cy < screen["top"] + screen["height"]:
                    qt = QGuiApplication.screens()
                    return {**screen, "name": qt[i].name() if i < len(qt) else str(i), "dpr": qt[i].devicePixelRatio() if i < len(qt) else 1}
        return None

    def select_region(self, region_id):
        region = self._find_region(region_id)
        if not region:
            return
        running = self.session.running
        pinned = self.session.pinned
        self.session.pause()
        try:
            snapshot = self.capture_adapter.capture_desktop_snapshot()
            selected = self.capture_adapter.select_region(snapshot)
            if selected:
                bounds = {key: int(getattr(selected, key)) for key in ("left", "top", "width", "height")}
                if self.window_capture_mode:
                    current = self.capture_adapter.window_capture.bounds()
                    anchor = self.raw["window_capture"]["anchor"]
                    bounds["left"] += anchor["left"] - current["left"]
                    bounds["top"] += anchor["top"] - current["top"]
                self.update_region(region_id, bounds=bounds, screen=self._screen_info(bounds))
                self._set_status("Đã lưu vùng OCR. Dùng Quét thử để kiểm tra.")
        except Exception as exc:
            self._set_status(f"Không khoanh được vùng: {exc}")
        finally:
            if running and not pinned:
                self.session.resume()

    def _region_problem(self, region):
        if not region.capture_region:
            return "chưa khoanh vùng"
        if self.window_capture_mode:
            if region.id == "__auto_wuwa__":
                return "" if self.raw["window_capture"].get("anchor") else "chưa chọn ứng dụng game"
            from ludiglot.core.window_capture import local_crop_box
            anchor = self.raw["window_capture"].get("anchor")
            try:
                local_crop_box(region.capture_region, anchor, (anchor["width"], anchor["height"]))
            except Exception:
                return "vùng nằm ngoài cửa sổ đã chọn; khoanh lại trên ảnh game"
            return ""
        current = self._screen_info(region.bounds)
        if not current:
            return "màn hình không còn hoặc vùng nằm ngoài màn hình"
        if region.id == "__auto_wuwa__":
            b = region.bounds
            if b["left"] < current["left"] or b["top"] < current["top"] or b["left"] + b["width"] > current["left"] + current["width"] or b["top"] + b["height"] > current["top"] + current["height"]:
                return "khung Auto cần nằm trọn trên một màn hình"
        if region.screen and any(region.screen.get(k) != current.get(k) for k in ("name", "left", "top", "width", "height", "dpr")):
            return "màn hình/độ phân giải/DPI đã đổi; cần khoanh lại"
        if not self.overlay.capture_excluded:
            a, b = region.bounds, self.overlay.region_bounds_physical()
            if b and a["left"] < b["left"] + b["width"] and b["left"] < a["left"] + a["width"] and a["top"] < b["top"] + b["height"] and b["top"] < a["top"] + a["height"]:
                return "phụ đề đang chồng vùng OCR; hãy đổi vị trí phụ đề vì capture exclusion chưa được xác nhận"
        return ""

    def _on_capture_exclusion(self, confirmed):
        if self.window_capture_mode:
            return
        if not confirmed:
            conflicts = [r.name for r in self.regions if r.enabled and r.capture_region and "chồng vùng" in self._region_problem(r)]
            if conflicts:
                self.session.pause()
                self._set_status("Không loại được phụ đề khỏi ảnh chụp. Đặt phụ đề ngoài vùng: " + ", ".join(conflicts))
        self._sync_session_regions()

    def _sync_session_regions(self):
        effective = []
        for region in (() if self.auto_only else self.regions):
            try:
                problem = self._region_problem(region) if region.enabled else ""
            except Exception:
                problem = "không kiểm tra được màn hình"
            effective.append(replace(region, mode="manual" if self.auto_mode and region.mode == "realtime" else region.mode,
                                     enabled=region.enabled and not problem))
        auto = self._auto_region()
        try:
            valid = auto.capture_region is not None and not self._region_problem(auto)
        except Exception:
            valid = False
        effective.append(replace(auto, enabled=valid))
        self.session.set_regions(effective)

    def toggle_realtime(self):
        if not self.ready or self._maintenance:
            self._set_status("OCR/dữ liệu chưa sẵn sàng. Xem trang Dữ liệu.")
            return
        if self.session.pinned:
            self.resume_realtime()
        elif self.session.running:
            self.stop_realtime()
        else:
            if self.auto_mode:
                self.resume_realtime()
                return
            self._sync_session_regions()
            valid = [r for r in self.regions if r.enabled and r.mode == "realtime" and not self._region_problem(r)]
            if not valid:
                self._set_status("Không có vùng realtime hợp lệ. Khoanh lại vùng hoặc kiểm tra vị trí phụ đề.")
                return
            self.session.start()
            self._run_intent = True

    def stop_realtime(self):
        self._run_intent = False
        self.session.pause()
        self._clear_auto()

    def resume_realtime(self):
        if not self.ready or self._maintenance:
            return
        self._manual_display = False
        self._manual_region_id = None
        self._clear_auto()
        if self.auto_mode:
            if not self.auto_processor or not self._auto_safe_to_start():
                return
            self._display_signature = None
            self.overlay.set_content([])
        else:
            self._render_realtime()
        self._sync_session_regions()
        self._run_intent = self.session.resume()

    def scan_special(self):
        if not self.special_region_id:
            self._set_status("Chưa có vùng đặc biệt. Thêm một vùng chế độ Phím tắt.")
            return
        self.scan_region(self.special_region_id)

    def scan_region(self, region_id, preview=False):
        region = self._find_region(region_id)
        if not region:
            return
        if not region.enabled:
            self._set_status(f"{region.name}: vùng đang tắt; bật lại trước khi quét.")
            return
        if not region.capture_region:
            self.select_region(region_id)
            region = self._find_region(region_id)
        if not self.ready or self._maintenance:
            self._set_status("OCR/dữ liệu chưa sẵn sàng.")
            return
        problem = self._region_problem(region)
        if problem:
            self._set_status(f"{region.name}: {problem}")
            return
        self.session.request_scan(region_id, preview=preview)

    def _valid_envelope(self, payload):
        region = self._find_region(payload.get("region_id"))
        return not self._closed and region and region.enabled and payload.get("session_id") == self._session_identity and payload.get("generation") == self.session.generation and payload.get("revision") == region.revision

    def _plain(self, text):
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        return html.unescape(re.sub(r"<[^>]+>", "", text))

    def _shape(self, result, region):
        safe = copy.deepcopy(result)
        for field in ("matches", "items"):
            for item in safe.get(field, []):
                if isinstance(item, dict):
                    item["official_cn"] = item.get("official_vi") or item.get("official_en") or ""
        if safe.get("matches"):
            safe["_official_cn"] = safe["matches"][0].get("official_cn", "")
        preferences = DisplayPreferences(gender_preference=self.config.gender_preference, font_en="Segoe UI", font_cn="Segoe UI", target_language="vi")
        model = shape_translation_display(safe, preferences=preferences, param_resolver=self.param_resolver)
        return {"name": region.name, "source": self._plain(model.source.display_text), "target": self._plain(model.target.display_text)}

    def _on_result(self, payload):
        if not self._valid_envelope(payload):
            return
        if payload["region_id"] == "__auto_wuwa__":
            if payload.get("status") == "capture_unavailable":
                self._clear_auto()
                return
            if payload.get("error") and self.auto_mode and self.session.running:
                self._auto_errors += 1
                if self._auto_errors >= 3:
                    self.stop_realtime()
                    self._set_status("OCR Auto lỗi liên tiếp; đã bỏ lớp che và tạm dừng. Kiểm tra OCR rồi Tiếp tục.")
                return
            frame = (payload.get("result") or {}).get("auto_frame")
            if not self.auto_mode or not isinstance(frame, AutoFrameResult) or self._manual_display or not self.session.running:
                return
            self._auto_errors = 0
            submitted = time.perf_counter()
            self._last_capture_bounds = (payload.get("result") or {}).get("capture_bounds")
            frame = self.auto_tracker.update(frame)
            self._last_auto_frame = frame
            self._display_auto(frame)
            timing = (payload.get('result') or {}).get('timings')
            if timing:
                ui_ms = 1000*(time.perf_counter()-submitted)
                queued_ms = max(0.0, 1000*(submitted-timing.get('queued_at', submitted)))
                self.last_auto_timings = {**timing, 'queue_ms':queued_ms, 'ui_submit_ms':ui_ms,
                    'capture_to_submit_ms':timing.get('worker_ms',0)+queued_ms+ui_ms}
                # Bound logs without screenshots/OCR text. This measures GUI
                # submission, not monitor scanout or time since game text began.
                if submitted >= getattr(self, '_next_timing_log', 0):
                    self._next_timing_log = submitted+5
                    self.log_message.emit('[Auto timing] '+ ' '.join(f'{k}={self.last_auto_timings.get(k,0):.1f}'
                        for k in ('capture_ms','windows_ocr_ms','cpu_ocr_ms','lookup_ms','queue_ms','ui_submit_ms','capture_to_submit_ms')))
            accepted = [b for b in frame.blocks if b.kind in {"dialogue", "choices"}]
            if accepted:
                import re
                key = accepted[-1].identity[1]
                if (self.auto_processor and self.auto_processor.index.valid_context(key)
                        and key != self.raw["auto_wuwa"].get("context_hint")):
                    self.raw["auto_wuwa"]["context_hint"] = key
                    self._remember_story(accepted[-1])
                    self.store.update_values(auto_wuwa=self.raw["auto_wuwa"], story_memory=self.raw.get('story_memory',[]))
                    self.data_changed.emit()
            signature = (tuple((b.kind, b.order, b.target) for b in frame.blocks),
                         (frame.document.identity, frame.document.target) if frame.document else None)
            if signature != self._auto_history_signature and (frame.blocks or frame.document):
                self._auto_history_signature = signature
                text = frame.document.target if frame.document else "\n\n".join(b.target for b in frame.blocks)
                self.history.append({"time": datetime.now().strftime("%H:%M:%S"), "text": text})
                self.history[:] = self.history[-100:]
                self.history_changed.emit()
            labels = {"document": "tài liệu có cuộn", "glossary": "thông tin Glossary", "choices": "đáp án/tương tác", "dialogue": "hội thoại", "transition": "chuyển cảnh"}
            self._set_status("Auto WuWa: " + (", ".join(labels[k] for k in frame.detected_kinds) or "chưa thấy nội dung cần dịch") +
                             (" — chưa có bản Việt khớp chắc chắn" if frame.detected_kinds and not frame.blocks and not frame.document else "") +
                             (" — có đoạn không vừa vùng chữ; giữ English, thử giảm cỡ chữ" if self.auto_overlay.overflow else ""))
            return
        region = self._find_region(payload["region_id"])
        block = self._shape(payload["result"], region)
        if not block["target"]:
            return
        if payload["mode"] == "manual":
            self._clear_auto()
            self._manual_display = True
            self._manual_region_id = region.id
            self._render([block])
        elif not self._manual_display:
            self._blocks[region.id] = block
            self._render_realtime()

    def _on_preview(self, payload):
        if self._valid_envelope(payload):
            if payload.get("region_id") == "__auto_wuwa__":
                frame = (payload.get("result") or {}).get("auto_frame")
                if isinstance(frame, AutoFrameResult):
                    matches = [{"official_en": b.source, "official_vi": b.target, "text_key": b.identity[1]} for b in frame.blocks]
                    if frame.document:
                        matches.append({"official_en": "Tài liệu đã xác nhận", "official_vi": frame.document.target, "text_key": frame.document.identity[1]})
                    evidence = "\n".join(f"{kind}: {text}" for kind, text in frame.observed_texts)
                    payload = {**payload, "ocr": "Auto: " + ", ".join(frame.detected_kinds) + "\n" + "\n".join(frame.diagnostics) + "\n" + evidence, "result": {"matches": matches}}
            self.preview_ready.emit(payload)

    def _on_session_state(self, state):
        if state.get("session_id") != self._session_identity or state.get("generation") != self.session.generation:
            return
        if self._manual_display and not state.get("pinned") and not state.get("manual_pending"):
            self._manual_display = False
            self._manual_region_id = None
            self._render_realtime()

    def _render_realtime(self):
        if self.auto_mode and not self._manual_display:
            self._render([])
            return
        self._render([self._blocks[r.id] for r in self.regions if r.enabled and r.mode == "realtime" and r.id in self._blocks])

    def _render(self, blocks):
        signature = display_signature(blocks, bool(self.raw["overlay"].get("show_source")))
        # Refresh invisible source metadata without repainting equal subtitles.
        self._displayed = blocks
        if signature == self._display_signature:
            return
        self._display_signature = signature
        self.overlay.set_content(blocks)
        if blocks:
            self.overlay.show_output()
            text = "\n\n".join(b["target"] for b in blocks)
            self.history.append({"time": datetime.now().strftime("%H:%M:%S"), "text": text})
            self.history[:] = self.history[-100:]
            self.history_changed.emit()

    def update_overlay_settings(self, **changes):
        self.raw["overlay"].update(changes)
        self.store.update_values(overlay=self.raw["overlay"])
        self.overlay.apply_settings(self.raw["overlay"])
        self._display_signature = None
        if self._displayed:
            self.overlay.set_content(self._displayed)

    def set_overlay_edit(self, enabled):
        if enabled:
            self._edit_resume = self.session.running and not self.session.pinned
            self.session.pause()
        self.overlay.set_edit_mode(enabled)
        if not enabled:
            self._sync_session_regions()
            if self._edit_resume:
                self.session.resume()
            self._edit_resume = False

    def toggle_overlay(self):
        self.overlay.toggle_hidden()
        if self.overlay.user_hidden:
            self.auto_overlay.hide(); self.auto_reader.hide(); self.auto_fallback.hide()
        elif self.auto_mode and self._last_auto_frame:
            self._display_auto(self._last_auto_frame)

    def clear_output(self):
        self._clear_auto()
        self._blocks.clear()
        self._manual_display = False
        self._manual_region_id = None
        self.session.clear()
        self._display_signature = None
        self._render([])

    def reset_overlay_position(self):
        self.overlay.reset_position()

    def preview_overlay(self):
        self.overlay.set_content([{"name": "Xem thử", "source": "Welcome to Wuthering Waves, Rover!", "target": "Chào mừng đến với Wuthering Waves, Rover!\nJinhsi và Changli vẫn dùng tên tiếng Anh."}])
        self.overlay.user_hidden = False
        self.overlay.show_output()

    def copy_translation(self):
        if self.auto_mode and not self._manual_display and self._last_auto_frame:
            frame = self._last_auto_frame
            text = (frame.document.title + "\n\n" + frame.document.target) if frame.document else "\n\n".join(b.target for b in frame.blocks)
        else:
            text = "\n\n".join(b["target"] for b in self._displayed)
        QApplication.clipboard().setText(text)

    def _hotkey_valid(self, hotkey, excluded_region=None, all_keys=None):
        from ludiglot.ui.qt_hotkey_adapter import parse_win_hotkey
        spec = parse_win_hotkey(hotkey)
        if not spec:
            self._set_status(f"Phím tắt không hợp lệ: {hotkey}")
            return False
        keys = all_keys if all_keys is not None else [self.raw.get(k, "") for k in ("hotkey_capture", "hotkey_screenshot", "hotkey_toggle", "hotkey_exception", "hotkey_exception_select")] + [r.hotkey for r in self.regions if r.id != excluded_region and r.hotkey]
        if any(parse_win_hotkey(str(key)) == spec for key in keys if key):
            self._set_status(f"Phím tắt bị trùng: {hotkey}")
            return False
        return True

    def save_hotkeys(self, mapping):
        from ludiglot.ui.hotkey_editor import canonical_hotkey
        try:
            mapping = {k: canonical_hotkey(v) for k, v in mapping.items() if k in ("hotkey_capture", "hotkey_screenshot", "hotkey_toggle", "hotkey_exception", "hotkey_exception_select")}
        except ValueError as exc:
            self._set_status(str(exc))
            return False
        checked = []
        proposed = {**self.raw, **mapping}
        for key in ("hotkey_capture", "hotkey_screenshot", "hotkey_toggle", "hotkey_exception", "hotkey_exception_select"):
            value = proposed.get(key, "")
            if value and not self._hotkey_valid(value, all_keys=checked + [r.hotkey for r in self.regions if r.hotkey]):
                return False
            checked.append(value)
        if not self._register_hotkeys(raw=proposed, rollback=True):
            return False
        self.raw.update(mapping)
        self.store.update_values(**mapping)
        self.regions_changed.emit()
        self._set_status("Đã lưu phím tắt.")
        return True

    def update_runtime_settings(self, **values):
        allowed = {}
        if "realtime_interval_ms" in values:
            interval = max(100, min(5000, int(values["realtime_interval_ms"])))
            allowed["realtime_interval_ms"] = interval
            self.config.realtime_interval_ms = interval
        if "pause_on_focus_loss" in values:
            allowed["pause_on_focus_loss"] = bool(values["pause_on_focus_loss"])
        self.raw.update(allowed)
        self.store.update_values(**allowed)
        self._set_status("Đã lưu cài đặt realtime.")

    def suspend_hotkeys(self):
        self._hotkey_edit_depth += 1
        if self._hotkeys:
            self._hotkeys.stop()
            self._hotkeys = None

    def resume_hotkeys(self):
        self._hotkey_edit_depth = max(0, self._hotkey_edit_depth - 1)
        if not self._hotkey_edit_depth and not self._closed:
            self._register_hotkeys()

    def _bindings(self, raw, regions):
        if self.auto_only:
            return tuple(HotkeyBinding(name,str(raw.get(key)),callback) for name,key,callback in (
                ('capture','hotkey_capture',self.toggle_realtime),
                ('screenshot','hotkey_screenshot',self.capture_translated_screenshot)) if raw.get(key))
        bindings = [HotkeyBinding(name, str(raw.get(key) or ""), callback) for name, key, callback in (
            ("capture", "hotkey_capture", self.toggle_realtime), ("screenshot", "hotkey_screenshot", self.capture_translated_screenshot), ("toggle", "hotkey_toggle", self.toggle_overlay),
            ("exception", "hotkey_exception", self.scan_special), ("exception_select", "hotkey_exception_select", lambda: self.select_region(self.special_region_id))
        ) if raw.get(key)]
        bindings += [HotkeyBinding("region_" + r.id, r.hotkey, lambda rid=r.id: self.region_hotkey_action(rid)) for r in regions if r.hotkey]
        return tuple(bindings)

    def _register_hotkeys(self, *, raw=None, regions=None, rollback=False):
        if self._closed:
            return False
        if self._hotkey_edit_depth:
            # Editors restore bindings on focus-out before submitting changes.
            if rollback:
                self._set_status("Kết thúc ghi tổ hợp trước khi lưu phím.")
                return False
            return True
        self.hotkey_error = ""
        if self._hotkeys:
            self._hotkeys.stop()
            self._hotkeys = None
        bindings = self._bindings(raw if raw is not None else self.raw, regions if regions is not None else self.regions)
        try:
            adapter = WindowsNativeHotkeyAdapter(no_repeat=True)
            self._hotkeys = adapter.register(bindings) if bindings else None
            if bindings and self._hotkeys is None:
                self.hotkey_error = getattr(adapter, "last_error", "") or "Không đăng ký được phím tắt: kiểm tra phím đang bị app khác dùng."
        except Exception as exc:
            self.hotkey_error = f"Phím tắt không sẵn sàng: {exc}"
        if self.hotkey_error:
            error = self.hotkey_error
            if rollback:
                old = self._bindings(self.raw, self.regions)
                adapter = WindowsNativeHotkeyAdapter(no_repeat=True)
                self._hotkeys = adapter.register(old) if old else None
                if old and self._hotkeys is None:
                    error += " Không khôi phục được đăng ký cũ; cấu hình cũ vẫn được giữ."
            elif self.auto_only:
                # A newly introduced screenshot shortcut must not take away
                # the user's existing start/pause shortcut on startup conflict.
                prior = tuple(b for b in bindings if b.name != 'screenshot')
                if prior:
                    self._hotkeys = WindowsNativeHotkeyAdapter(no_repeat=True).register(prior)
                    if self._hotkeys:
                        error += ' Phím bắt đầu/tạm dừng vẫn dùng được; có thể chụp bằng nút trong app hoặc đổi phím chụp.'
            self._set_status(error)
            self.log_message.emit(error)
            return False
        return True

    def save_profile(self, name):
        name = name.strip()
        if name:
            self.raw.setdefault("profiles", {})[name] = {"regions": [r.to_dict() for r in self.regions], "special_region_id": self.special_region_id,
                                                        "auto_wuwa": copy.deepcopy(self.raw["auto_wuwa"]),
                                                        "window_capture": copy.deepcopy(self.raw.get("window_capture", {"mode":"desktop"}))}
            self.store.update_values(profiles=self.raw["profiles"])
            self.regions_changed.emit()

    def load_profile(self, name):
        profile = self.raw.get("profiles", {}).get(name)
        if not isinstance(profile, dict) or not isinstance(profile.get("regions"), list):
            return
        data = migrate_desktop_settings({"regions": profile["regions"]})
        regions = [OcrRegion.from_mapping(r, i) for i, r in enumerate(data["regions"])]
        if not self._validate_region_hotkeys(regions) or not self._register_hotkeys(regions=regions, rollback=True):
            return False
        self.session.pause()
        self.regions = regions
        if isinstance(profile.get("window_capture"), dict):
            settings = copy.deepcopy(profile["window_capture"])
            try:
                self.capture_adapter.configure_window(settings)
            except Exception as exc:
                self._set_status(f"Nguồn ảnh profile chưa sẵn sàng: {exc}. Chọn lại ứng dụng game.")
            self.raw["window_capture"] = settings
            self._window_bounds = None
            self.store.update_values(window_capture=settings)
        if isinstance(profile.get("auto_wuwa"), dict):
            self.raw["auto_wuwa"] = normalize_auto_settings(profile["auto_wuwa"])
            self.store.update_values(auto_wuwa=self.raw["auto_wuwa"])
            self._auto_revision += 1
        self.special_region_id = profile.get("special_region_id", "")
        self.clear_output()
        self._save_regions(register=False)
        self.data_changed.emit()

    def _validate_region_hotkeys(self, regions):
        from ludiglot.ui.qt_hotkey_adapter import parse_win_hotkey
        specs = set()
        values = [self.raw.get(k, "") for k in ("hotkey_capture", "hotkey_screenshot", "hotkey_toggle", "hotkey_exception", "hotkey_exception_select")]
        values += [r.hotkey for r in regions if r.hotkey]
        for value in values:
            if not value:
                continue
            spec = parse_win_hotkey(value)
            if spec is None or spec in specs:
                self._set_status(f"Phím tắt profile không hợp lệ hoặc bị trùng: {value}")
                return False
            specs.add(spec)
        return True

    def export_profile(self):
        path, _ = QFileDialog.getSaveFileName(None, "Xuất profile", "profile.json", "JSON (*.json)")
        if path:
            atomic_write_json(Path(path), {"regions": [r.to_dict() for r in self.regions], "special_region_id": self.special_region_id,
                                           "auto_wuwa": self.raw["auto_wuwa"], "window_capture": self.raw.get("window_capture", {"mode":"desktop"})})

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(None, "Nhập profile", "", "JSON (*.json)")
        if not path:
            return
        try:
            profile = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(profile, dict) or not isinstance(profile.get("regions"), list):
                raise ValueError("Profile không hợp lệ")
            candidates = [OcrRegion.from_mapping(r, i) for i, r in enumerate(migrate_desktop_settings({"regions": profile["regions"]})["regions"])]
            if not self._validate_region_hotkeys(candidates):
                return
            name = Path(path).stem
            previous = copy.deepcopy(self.raw.get("profiles", {}))
            self.raw.setdefault("profiles", {})[name] = profile
            if self.load_profile(name) is False:
                self.raw["profiles"] = previous
                return
            self.store.update_values(profiles=self.raw["profiles"])
        except Exception as exc:
            self._set_status(f"Không nhập được profile: {exc}")

    def open_logs(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.logs)))

    def show_region_bounds(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel
        was_running = self.session.running and not self.session.pinned
        self.session.pause()
        generation = self.session.generation
        for window in self._bound_windows:
            window.close()
        self._bound_windows = []
        for i, region in enumerate(self.regions):
            if not region.capture_region:
                continue
            b = region.bounds
            # Physical desktop pixels → Qt logical coordinates through the matching screen.
            screen = next((s for s in QGuiApplication.screens() if not region.screen or s.name() == region.screen.get("name")), QGuiApplication.primaryScreen())
            scale = screen.devicePixelRatio() or 1
            g = screen.geometry()
            saved = region.screen or {"left": int(g.x() * scale), "top": int(g.y() * scale)}
            window = QLabel(f"{i+1}. {region.name}")
            window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput)
            window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            window.setStyleSheet("border: 2px solid #2dd4bf; color: white; background: rgba(0,0,0,30);")
            window.setGeometry(int(g.x() + (b["left"] - saved["left"]) / scale), int(g.y() + (b["top"] - saved["top"]) / scale), int(b["width"] / scale), int(b["height"] / scale))
            window.show()
            self._bound_windows.append(window)
        def finish():
            self._close_bounds()
            if not self._closed and was_running and self.session.generation == generation:
                self.session.resume()
        QTimer.singleShot(4000, finish)

    def _close_bounds(self):
        for window in self._bound_windows:
            window.close()
        self._bound_windows = []

    def _check_environment(self):
        try:
            fingerprint = tuple((s.name(), s.geometry().getRect(), s.devicePixelRatio()) for s in QGuiApplication.screens())
            if self._screen_fingerprint is not None and fingerprint != self._screen_fingerprint:
                self.session.pause()
                self._run_intent = False
                self._auto_revision += 1
                self._clear_auto()
                self._sync_session_regions()
                self._set_status("Màn hình thay đổi. Chọn lại game để kiểm tra vị trí bản dịch." if self.auto_only else "Màn hình/DPI thay đổi. Kiểm tra và khoanh lại các vùng OCR.")
            self._screen_fingerprint = fingerprint
            if self.window_capture_mode or self.auto_mode:
                import win32gui
                foreground = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(foreground)
                own_reader = self.auto_reader.isVisible() and (foreground == int(self.auto_reader.winId()) or self.auto_reader.isActiveWindow())
                service = getattr(self.capture_adapter, "window_capture", None)
                target = getattr(service, "target", None)
                game_foreground = foreground == target.hwnd if target else str(self.config.window_title or "Wuthering Waves").casefold() in title.casefold()
                hidden = not game_foreground and not own_reader
                changed = hidden != self._background_hidden
                self._background_hidden = hidden
                if hidden:
                    for window in (self.auto_overlay, self.auto_reader, self.auto_fallback):
                        window.hide()
                elif changed and self._last_auto_frame and self.session.running:
                    self._display_auto(self._last_auto_frame)
                if self.window_capture_mode:
                    try:
                        bounds = service.bounds()
                    except Exception:
                        self._clear_auto()
                        return  # Keep run intent armed while minimized/closed.
                    if self._window_bounds is not None and bounds != self._window_bounds:
                        self._window_bounds = bounds
                        self._auto_revision += 1
                        self._clear_auto()
                        self._sync_session_regions()
                    self._window_bounds = bounds
        except Exception as exc:
            self.log_message.emit(f"Kiểm tra màn hình: {exc}")

    def _updater(self):
        from ludiglot.core.translation_update import TranslationUpdater
        tool = (Path(__file__).resolve().parents[3] / "tools" / "FModelCLI.exe")
        import sys
        if getattr(sys, "frozen", False):
            tool = Path(sys.executable).parent / "tools" / "FModelCLI.exe"
        return TranslationUpdater(self.paths.updates, tool_path=tool, source_version=self.raw.get("official_source_version"), source_data_root=self.config.data_root, database_path=self.config.wahu_db_path)

    def _app_updater(self):
        import sys
        from ludiglot.core.app_update import AppUpdater
        application = Path(sys.executable).parent if getattr(sys, 'frozen', False) else self.paths.resource_root
        return AppUpdater(application)

    def _start_job(self, kind, work, *, pause=False):
        if self._maintenance or (kind == "check" and self._checking_updates):
            self._set_status("Một tác vụ đang chạy; hãy đợi hoàn tất.")
            return
        if kind == "check":
            self._checking_updates = True
        else:
            self._maintenance = True
        self._job_cancel.clear()
        self.data_changed.emit()
        running = self.session.running
        if pause:
            self.session.pause()
            self._clear_auto()
        def worker():
            try:
                result = work()
                self._job_finished.emit({"kind": kind, "result": result, "resume": running})
            except Exception as exc:
                self._job_finished.emit({"kind": kind, "error": str(exc), "resume": running if pause else False})
        threading.Thread(target=worker, daemon=True).start()

    def check_updates(self, force=True):
        if self._checking_updates:
            return
        if not force and self._launch_update_check_done:
            return
        if self._maintenance:
            if not force:
                QTimer.singleShot(2000, lambda: self.check_updates(force=False))
            return
        self._launch_update_check_done = True
        self._set_status("Đang kiểm tra cập nhật app và bản dịch…")
        def work():
            # One endpoint may fail without hiding the other endpoint's result.
            results = {}
            for name, factory in (('translation', self._updater), ('app', self._app_updater)):
                try:
                    results[name] = factory().check_latest()
                except Exception as exc:
                    results[name + '_error'] = str(exc)
            return results
        self._start_job("check", work)

    def download_app_update(self):
        import sys
        if not self.latest_app_release:
            self.check_updates()
            return
        if not self.latest_app_release.available:
            self._set_status(self.latest_app_release.summary)
            return
        if not getattr(sys, 'frozen', False):
            self._set_status('Bản chạy từ source: tải EXE mới từ GitHub Releases.')
            QDesktopServices.openUrl(QUrl('https://github.com/ossmoc/ossmoc-echo/releases/latest'))
            return
        application = Path(sys.executable).parent.resolve()
        game = self.raw.get('game_install_root')
        if game and application.is_relative_to(Path(game).resolve()):
            self._set_status('Hãy chuyển Ossmoc Echo ra ngoài thư mục game trước khi cập nhật app.')
            return
        release = self.latest_app_release
        if QMessageBox.question(None, 'Cập nhật Ossmoc Echo',
                f'Tải {release.tag} ({release.size / 1024**2:.0f} MiB)?\n'
                'Sau khi tải và kiểm tra xong, app sẽ đóng và mở lại.\n'
                'Giữ nguyên cấu hình, tiến trình và dữ liệu bản dịch.') != QMessageBox.StandardButton.Yes:
            return
        self._start_job('app-install', lambda: self._app_updater().prepare(release,
                        cancel=self._job_cancel, progress=self._status.emit))

    def _refresh_update_status(self, manifest=None):
        if self.latest_release is None:
            self.update_available = False
            return
        from ludiglot.core.translation_update import translation_update_status
        try:
            installed = self._updater().active_manifest() if manifest is None else manifest
            self.update_available, self.update_summary = translation_update_status(self.latest_release, installed)
        except Exception as exc:
            self.update_available = False
            self.update_summary = "Không xác định được bản dịch đang dùng"
            self.log_message.emit(str(exc))

    def download_update(self):
        if not self.has_english_data:
            self._set_status(MISSING_ENGLISH_DATA)
            self.data_changed.emit()
            return
        if not self.latest_release:
            self.check_updates()
            return
        self._refresh_update_status()
        if not self.update_available:
            self._set_status(self.update_summary)
            self.data_changed.emit()
            return
        size = sum(a.size for a in self.latest_release.assets if a.name.startswith("App-Dich") or a.name == "WuWaVH_EN_99_P.pak")
        if QMessageBox.question(None, "Cập nhật bản dịch", f"Tải {self.latest_release.tag} (tối đa {size / 1024**2:.0f} MiB)?\nChỉ lưu trong dữ liệu app, không cài vào game.") != QMessageBox.StandardButton.Yes:
            return
        self._start_job("install", lambda: self._updater().install_release(self.latest_release, self.config.db_path, progress=self._status.emit, cancel=self._job_cancel))

    def import_translation(self):
        path, _ = QFileDialog.getOpenFileName(None, "Nhập bản dịch offline", "", "Bản dịch (*.db *.zip)")
        if path:
            self._start_job("install", lambda: self._updater().import_database(Path(path), self.config.db_path, progress=self._status.emit, cancel=self._job_cancel))

    def restore_translation(self):
        if QMessageBox.question(None, "Phục hồi", "Trở lại bộ dữ liệu trước lần cập nhật gần nhất?") == QMessageBox.StandardButton.Yes:
            self._start_job("install", lambda: self._updater().rollback(), pause=True)

    def cancel_update(self):
        self._job_cancel.set()
        self._set_status("Đã yêu cầu hủy; dữ liệu đang dùng được giữ nguyên.")

    def _on_job_finished(self, payload):
        if self._closed:
            return
        if payload["kind"] == "check":
            self._checking_updates = False
        else:
            self._maintenance = False
        if "error" in payload:
            if payload["kind"] == "check":
                self.update_available = False
                self.update_summary = "Không kiểm tra được cập nhật"
                self.app_update_available = False
                self.app_update_summary = 'Không kiểm tra được cập nhật app.'
                self._set_status(self.update_summary)
                self.log_message.emit(payload["error"])
                self.data_changed.emit()
                return
            self._set_status("Cập nhật app thất bại; giữ bản đang dùng. " + payload["error"] if payload['kind']=='app-install'
                             else "Tác vụ dữ liệu thất bại; giữ bản cũ. " + payload["error"])
            self.log_message.emit(self.status)
            if payload.get("resume"):
                self.session.resume()
            self.data_changed.emit()
            return
        if payload["kind"] == "check":
            result = payload['result']
            result = result if isinstance(result, dict) else {'translation': result}  # Existing API callers.
            if 'translation' in result:
                release = result['translation']
                self.latest_release = release
                self.raw.update(translation_checked_at=time.time(), latest_translation_release=release.to_dict())
                self.store.update_values(translation_checked_at=self.raw['translation_checked_at'], latest_translation_release=release.to_dict())
                self._refresh_update_status()
            elif 'translation_error' in result:
                self.latest_release = None
                self.update_available = False
                self.update_summary = 'Không kiểm tra được cập nhật bản dịch.'
                self.log_message.emit(result['translation_error'])
            if 'app' in result:
                self.latest_app_release = result['app']
                self.app_update_available = self.latest_app_release.available
                self.app_update_summary = self.latest_app_release.summary
            elif 'app_error' in result:
                self.latest_app_release = None
                self.app_update_available = False
                self.app_update_summary = 'Không kiểm tra được cập nhật app.'
                self.log_message.emit(result['app_error'])
            self._set_status(self.app_update_summary if self.app_update_available else self.update_summary)
            self.data_changed.emit()
        elif payload['kind'] == 'app-install':
            try:
                self._app_updater().launch(payload['result'])
            except Exception as exc:
                self._set_status('Không thay được app; giữ bản đang dùng. ' + str(exc))
                self.log_message.emit(self.status)
                self.data_changed.emit()
                return
            self._set_status('Đang đóng app để cài bản mới…')
            QApplication.instance().quit()
        else:
            result = payload["result"]
            self.session.close()
            self._blocks.clear()
            self._manual_display = False
            self._manual_region_id = None
            self.session = self._make_session()
            self._sync_session_regions()
            changes = {"db_path": str(result.cache_path), "wahu_db_path": str(result.database_path) if result.database_path else None, "translation_provider": "wahu" if result.database_path else None, "auto_rebuild_db": False}
            manifest = result.manifest
            self._refresh_update_status(manifest)
            if manifest.get("official_data_root"):
                changes["data_root"] = manifest["official_data_root"]
            if "source_version" in manifest:
                changes["official_source_version"] = manifest["source_version"]
            if payload['kind'] == 'rescan':
                changes.update(game_install_root=manifest['game_install_root'],
                               use_game_paks=True, game_version=None)
            self.raw.update(changes)
            self.raw["auto_wuwa"]["context_hint"] = ""
            self.raw['auto_wuwa']['story_group'] = ''
            self.raw['story_memory'] = []
            changes['story_memory'] = []
            changes["auto_wuwa"] = self.raw["auto_wuwa"]
            self.store.update_values(**changes)
            self.initialize(resume_after=payload.get("resume", False))

    def rescan_game(self):
        if self._maintenance:
            return
        root = QFileDialog.getExistingDirectory(None,
            "Chọn thư mục Wuthering Waves Game", str(self.raw.get('game_install_root') or ''))
        if not root:
            return
        from ludiglot.core.game_pak_update import resolve_game_root, GamePakUpdateError
        try:
            game = resolve_game_root(Path(root))
        except (OSError, GamePakUpdateError) as exc:
            self._set_status(str(exc))
            return
        if self.paths.data_root.resolve().is_relative_to(game):
            self._set_status("Không được đặt thư mục dữ liệu app trong thư mục game. Hãy chuyển app ra ngoài.")
            return
        # Stage only: a failed import must not persist an unusable folder or
        # replace a working cache. Ignore legacy manually entered versions.
        cfg = replace(self._load_config(), game_install_root=game,
                      use_game_paks=True, game_version=None,
                      game_platform='Windows', game_server=self.raw.get('game_server') or 'OS',
                      game_languages=['en', 'zh-Hans'])
        snapshot = copy.deepcopy(self.raw)
        self._set_status('Đang chuẩn bị dữ liệu English bằng bộ khóa mới nhất…')
        def work():
            from ludiglot.core.game_pak_update import update_from_game_paks, read_launcher_game_version
            from ludiglot.core.translation_update import TranslationUpdateCancelled
            stage = self.paths.updates / "generations" / ("game-" + uuid4().hex)
            stage.mkdir(parents=True)
            stage_config = stage / "settings.json"
            cache = stage / "cache.json"
            raw = dict(snapshot)
            for key in ("data_root", "en_json", "zh_json", "db_path", "wahu_db_path", "image_path"):
                value = getattr(cfg, key, None)
                if value:
                    raw[key] = str(value)
            raw.update(data_root=str(stage / "data"), db_path=str(cache), extract_audio=False,
                       auto_rebuild_db=False, game_install_root=str(game), use_game_paks=True,
                       game_version=None, game_platform=cfg.game_platform,
                       game_server=cfg.game_server, game_languages=cfg.game_languages)
            atomic_write_json(stage_config, raw)
            staged_cfg = replace(cfg, data_root=stage / "data", db_path=cache, extract_audio=False)
            def progress(message):
                self._status.emit(message)
                if self._job_cancel.is_set():
                    raise TranslationUpdateCancelled("Đã hủy quét lại dữ liệu game")
            update_from_game_paks(staged_cfg, stage_config, cache, progress=progress)
            with cache.open(encoding="utf-8") as stream:
                data = json.load(stream)
                if (not isinstance(data, dict) or len(data) < cfg.min_db_entries
                        or sum(isinstance(row, dict) and bool(str(row.get('official_en') or '').strip())
                               for entry in data.values()
                               if isinstance(entry, dict) and isinstance(entry.get('matches'), list)
                               for row in entry['matches']) < cfg.min_db_entries):
                    raise ValueError("Dữ liệu game quét được quá ít; giữ bộ dữ liệu đang dùng.")
                del data
            updater = self._updater()
            current = updater.active_manifest()
            baseline = {"schema_version": 1, "generation_id": "baseline", "cache_path": str(cfg.db_path),
                        "database_path": str(cfg.wahu_db_path) if cfg.wahu_db_path else None,
                        "official_data_root": str(cfg.data_root), "source_version": snapshot.get("official_source_version"),
                        "release_tag": None, "variant": "en", "source_kind": "existing_baseline"}
            metadata = {key: current[key] for key in ("release_tag", "release", "variant", "translation_game_version", "hashes", "canonical_en_path") if key in current}
            # The rolling feed cannot prove a numeric source version. Keep
            # exact source-English checks enabled rather than guessing one.
            metadata.update(official_data_root=str(stage / "data"), source_version=None,
                            game_install_root=str(game), detected_game_version=read_launcher_game_version(game))
            return updater.activate_prepared(cache, cfg.wahu_db_path, metadata=metadata, baseline=baseline if cfg.db_path.is_file() else None, cancel=self._job_cancel)
        self._start_job("rescan", work, pause=True)

    def close(self):
        self._closed = True
        self._init_token += 1
        self._job_cancel.set()
        self.environment_timer.stop()
        self.session.close()
        service = getattr(self.capture_adapter, "window_capture", None)
        if service:
            service.close()
        self._clear_auto()
        self.auto_overlay.close(); self.auto_fallback.close(); self.auto_reader.hide()
        if self._hotkeys:
            self._hotkeys.stop()
        self.overlay.close()
        self._close_bounds()
        for handler in self._logger.handlers:
            handler.close()
