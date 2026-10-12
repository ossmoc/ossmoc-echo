"""Controller integration without native windows, hotkeys, OCR, or network."""
from __future__ import annotations

import copy
from dataclasses import replace
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from ludiglot.core.app_paths import AppPaths
from ludiglot.core.desktop_models import OcrRegion
from ludiglot.core.preferences import atomic_write_json
from ludiglot.ui.desktop_session import DesktopCaptureSession
from ludiglot.ui import desktop_controller as controller_module


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class FakeOverlay:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.geometry_changed = FakeSignal()
        self.capture_exclusion_changed = FakeSignal()
        self.capture_excluded = True
        self.user_hidden = False
        self.visible = False
        self.blocks = []
        self.set_calls = []
        self.edit_mode = False

    def set_content(self, blocks):
        self.blocks = copy.deepcopy(blocks)
        self.set_calls.append(copy.deepcopy(blocks))
        if not blocks:
            self.visible = False

    def show_output(self):
        if not self.user_hidden and self.blocks:
            self.visible = True

    def toggle_hidden(self):
        self.user_hidden = not self.user_hidden
        self.visible = bool(self.blocks) and not self.user_hidden

    def apply_settings(self, settings):
        self.settings.update(settings)

    def set_edit_mode(self, enabled):
        self.edit_mode = enabled

    def region_bounds_physical(self):
        return None

    def close(self):
        self.visible = False


class FakeCapture:
    def __init__(self, config, log):
        self.config = config
        self.selected = None

    def capture_desktop_snapshot(self):
        return "snapshot"

    def select_region(self, snapshot):
        return self.selected


class FakeHotkeys:
    def __init__(self, **_kwargs):
        self.bindings = ()
        self.stopped = False

    def register(self, bindings):
        self.bindings = bindings
        return self

    def stop(self):
        self.stopped = True


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def controller(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(controller_module, "SubtitleOverlay", FakeOverlay)
    monkeypatch.setattr(controller_module, "QtCaptureAdapter", FakeCapture)
    monkeypatch.setattr(controller_module, "WindowsNativeHotkeyAdapter", FakeHotkeys)
    # Use the actual scheduler state machine while leaving its worker deferred.
    monkeypatch.setattr(controller_module, "DesktopCaptureSession",
        lambda **kwargs: DesktopCaptureSession(**kwargs, thread_starter=lambda target: None))
    screen = {"name": "screen-test", "left": 0, "top": 0, "width": 1920, "height": 1080, "dpr": 1}
    monkeypatch.setattr(controller_module.DesktopController, "_screen_info", lambda self, bounds: dict(screen))
    data = tmp_path / "userdata"
    paths = AppPaths(tmp_path, data, data / "config" / "settings.json", data)
    regions = [OcrRegion("first", "Hội thoại", bounds={"left": 0, "top": 0, "width": 100, "height": 50}),
               OcrRegion("second", "Lựa chọn", bounds={"left": 100, "top": 0, "width": 100, "height": 50}, order=1),
               OcrRegion("special", "Vùng đặc biệt", mode="manual", bounds={"left": 200, "top": 0, "width": 100, "height": 50}, order=2)]
    atomic_write_json(paths.config_path, {"unknown": {"keep": 42}, "auto_rebuild_db": False,
        "db_path": str(data / "cache" / "db.json"), "regions": [r.to_dict() for r in regions],
        "special_region_id": "special", "pause_on_focus_loss": False})
    value = controller_module.DesktopController(paths, auto_initialize=False)
    value.environment_timer.stop()
    value.ready = True
    yield value
    value.close()


def envelope(controller, region_id="first", target="Xin chào", source="Hello", *, mode="realtime", **changes):
    region = controller._find_region(region_id)
    payload = {"region_id": region_id, "revision": region.revision if region else 0,
        "generation": controller.session.generation, "session_id": controller._session_identity,
        "mode": mode, "result": {"_matched_key": "key", "_score": 0.99, "_ocr_text": source,
            "matches": [{"text_key": "id-1", "official_en": source, "official_vi": target,
                         "official_cn": "This must not leak from legacy Chinese"}]}}
    payload.update(changes)
    return payload


def pin(controller, region_id="special", target="Thông tin đặc biệt"):
    assert controller.session.request_scan(region_id)
    job = controller.session._pending.pop(("manual", region_id))
    payload = envelope(controller, region_id, target, mode="manual")
    controller.session._finish(job, payload["result"], "image", "OCR", "success")


def test_initialization_migrates_without_loading_engine_or_replacing_unknown_settings(controller):
    assert controller.engine is None
    assert controller.matcher is None
    assert controller.raw["unknown"] == {"keep": 42}
    assert controller.store.load_raw()["unknown"] == {"keep": 42}
    assert controller.raw["overlay"]["background"] == "none"
    assert len(controller._hotkeys.bindings) == 4


@pytest.mark.parametrize('hidden', [False, True])
def test_dialogue_and_answer_overflow_never_open_top_box_even_if_already_visible(monkeypatch, hidden):
    from types import SimpleNamespace
    from ludiglot.core.auto_wuwa import AutoFrameResult, AutoTextBlock
    from ludiglot.core.desktop_models import AUTO_DEFAULTS

    dialogue = AutoTextBlock('dialogue', (480,890,970,72), 'English', 'Bản Việt dài', ('db','Main_1'))
    choice = replace(dialogue, kind='choices', order=2)
    inline = SimpleNamespace(auto_blocks=(), overflow=(), capture_excluded=True,
        show=lambda: None, hide=lambda: None, isVisible=lambda: False)
    def set_frame(bounds, blocks, settings):
        inline.auto_blocks = inline.overflow = blocks
    inline.set_frame = set_frame
    reader = SimpleNamespace(set_document=lambda *args, **kwargs: None, isVisible=lambda: False)
    fallback = FakeOverlay({})
    fallback.visible = True
    fallback.blocks = [{'target':'Đáp án cũ không được hiện lại'}]
    fallback.hide = lambda: setattr(fallback, 'visible', False)
    fallback.isVisible = lambda: False
    panel = SimpleNamespace(_last_capture_bounds={'left':0,'top':0,'width':1920,'height':1080},
        window_capture_mode=False, _background_hidden=False, overlay=SimpleNamespace(user_hidden=hidden),
        auto_overlay=inline, auto_reader=reader, auto_fallback=fallback, raw={'auto_wuwa':AUTO_DEFAULTS})
    monkeypatch.setattr(controller_module, 'logical_frame', lambda bounds: (0,0,1920,1080))
    controller_module.DesktopController._display_auto(panel, AutoFrameResult(blocks=(dialogue, choice)))
    assert fallback.blocks == [] and not fallback.visible
    controller_module.DesktopController._display_auto(panel, AutoFrameResult(blocks=(dialogue,)))
    assert fallback.blocks == [] and not fallback.visible


def test_auto_frame_must_be_fully_inside_one_screen(controller):
    controller.raw["auto_wuwa"]["bounds"] = {"left": -10, "top": 0, "width": 1920, "height": 1080}
    assert "trọn" in controller._region_problem(controller._auto_region())


def test_realtime_groups_in_region_order_not_arrival_order(controller):
    controller._on_result(envelope(controller, "second", "Chọn đáp án"))
    controller._on_result(envelope(controller, "first", "Xin chào"))
    assert [b["target"] for b in controller.overlay.blocks] == ["Xin chào", "Chọn đáp án"]
    controller.move_region("second", -1)
    assert [b["target"] for b in controller.overlay.blocks] == ["Chọn đáp án", "Xin chào"]


def test_disabled_region_removed_and_queued_results_rejected(controller):
    controller._on_result(envelope(controller, "first", "Một"))
    controller._on_result(envelope(controller, "second", "Hai"))
    old = envelope(controller, "first", "Không được hiện")
    controller.update_region("first", enabled=False)
    controller._on_result(old)
    assert [b["target"] for b in controller.overlay.blocks] == ["Hai"]
    assert "first" not in controller._blocks
    assert controller.session._regions["first"].enabled is False


def test_dedup_final_translation_ignores_raw_ocr_and_hidden_source(controller):
    original = envelope(controller, target="Jinhsi đang chờ", source="Jinhsi is waiting")
    controller._on_result(original)
    count = len(controller.overlay.set_calls)
    noisy = copy.deepcopy(original)
    noisy["result"]["_ocr_text"] = "Jinh5i is wait1ng"
    controller._on_result(noisy)
    changed_source = envelope(controller, target="Jinhsi đang chờ", source="Jinhsi waits")
    controller._on_result(changed_source)
    assert len(controller.overlay.set_calls) == count
    assert len(controller.history) == 1
    assert controller._displayed[0]["source"] == "Jinhsi waits"
    controller.update_overlay_settings(show_source=True)
    assert controller.overlay.blocks[0]["source"] == "Jinhsi waits"


def test_visible_source_change_repaints_when_enabled(controller):
    controller.update_overlay_settings(show_source=True)
    controller._on_result(envelope(controller, target="Xin chào", source="Hello"))
    before = len(controller.overlay.set_calls)
    controller._on_result(envelope(controller, target="Xin chào", source="Welcome"))
    assert len(controller.overlay.set_calls) == before + 1


@pytest.mark.parametrize("changes", [
    {"session_id": "old-session"}, {"generation": -1}, {"revision": 99}, {"region_id": "deleted"},
])
def test_stale_result_and_preview_envelopes_are_rejected(controller, changes):
    received = []
    controller.preview_ready.connect(received.append)
    payload = envelope(controller, **changes)
    controller._on_result(payload)
    controller._on_preview(payload)
    assert controller.overlay.blocks == []
    assert received == []
    assert controller.history == []


def test_current_preview_is_forwarded_without_overlay_or_history_mutation(controller):
    received = []
    controller.preview_ready.connect(received.append)
    payload = envelope(controller, mode="preview", image="image", ocr="OCR text")
    controller._on_preview(payload)
    assert received == [payload]
    assert controller.overlay.blocks == []
    assert controller.history == []


def test_manual_hold_preserves_realtime_cache_and_resume_restores_it(controller):
    controller._on_result(envelope(controller, "first", "Hội thoại đang có"))
    controller._on_result(envelope(controller, "second", "Lựa chọn đang có"))
    pin(controller)
    assert controller.session.pinned is True
    assert controller._manual_display is True
    assert controller._manual_region_id == "special"
    assert [b["target"] for b in controller.overlay.blocks] == ["Thông tin đặc biệt"]
    # Even a maliciously queued realtime callback of the current generation
    # must not overwrite a manual result in the UI layer.
    controller._on_result(envelope(controller, "first", "Không được đè"))
    assert controller.overlay.blocks[0]["target"] == "Thông tin đặc biệt"
    controller.resume_realtime()
    assert [b["target"] for b in controller.overlay.blocks] == ["Hội thoại đang có", "Lựa chọn đang có"]
    assert controller.session.running is True
    assert controller.session.pinned is False
    assert controller._manual_display is False
    assert controller._manual_region_id is None


def test_stale_session_state_cannot_release_manual_hold(controller):
    pin(controller)
    current = {"session_id": controller._session_identity, "generation": controller.session.generation,
               "pinned": False, "manual_pending": False}
    controller._on_session_state({**current, "session_id": "old-session"})
    controller._on_session_state({**current, "generation": -1})
    assert controller._manual_display is True
    assert controller.overlay.blocks[0]["target"] == "Thông tin đặc biệt"


def test_new_result_does_not_undo_user_hidden_overlay(controller):
    controller._on_result(envelope(controller, target="Câu đầu"))
    assert controller.overlay.visible is True
    controller.toggle_overlay()
    controller._on_result(envelope(controller, target="Câu mới"))
    assert controller.overlay.blocks[0]["target"] == "Câu mới"
    assert controller.overlay.user_hidden is True
    assert controller.overlay.visible is False
    controller.toggle_overlay()
    assert controller.overlay.visible is True


def test_clear_invalidates_in_flight_result_and_unpins(controller):
    pin(controller)
    stale = envelope(controller, "special", "Không được hiện lại", mode="manual")
    old_generation = controller.session.generation
    controller.clear_output()
    assert controller.session.generation > old_generation
    assert controller.session.pinned is False
    assert controller._manual_region_id is None
    assert controller._manual_display is False
    controller._on_result(stale)
    assert controller.overlay.blocks == []
    assert controller._blocks == {}


def test_clear_while_realtime_preserves_running_intent(controller):
    controller.session.start()
    controller._on_result(envelope(controller, target="Đang chạy"))
    stale = envelope(controller, target="Trễ")
    controller.clear_output()
    assert controller.session.running is True
    controller._on_result(stale)
    assert controller.overlay.blocks == []


def test_delete_realtime_removes_only_its_block_and_rejects_pending_result(controller):
    controller._on_result(envelope(controller, "first", "Một"))
    controller._on_result(envelope(controller, "second", "Hai"))
    stale = envelope(controller, "first", "Trễ")
    controller.delete_region("first")
    controller._on_result(stale)
    assert [b["target"] for b in controller.overlay.blocks] == ["Hai"]
    assert "first" not in controller.session._regions


def test_delete_manual_region_clears_hold(controller):
    pin(controller)
    stale = envelope(controller, "special", "Không được hiện lại", mode="manual")
    controller.delete_region("special")
    controller._on_result(stale)
    assert controller.overlay.blocks == []
    assert controller.session.pinned is False
    assert controller._manual_display is False
    assert controller.special_region_id == ""


def test_edit_manual_region_clears_previous_hold_and_rejects_old_revision(controller):
    pin(controller)
    stale = envelope(controller, "special", "Trễ", mode="manual")
    controller.update_region("special", bounds={"left": 250, "top": 10, "width": 100, "height": 50})
    controller._on_result(stale)
    assert controller.overlay.blocks == []
    assert controller.session.pinned is False
    assert controller._find_region("special").revision == 1


def test_load_profile_clears_manual_state_and_rejects_previous_same_id_results(controller):
    pin(controller)
    stale = envelope(controller, "special", "Old profile", mode="manual")
    regions = [replace(r, name=r.name + " mới").to_dict() for r in controller.regions]
    controller.raw["profiles"]["Bố cục mới"] = {"regions": regions, "special_region_id": "special"}
    controller.load_profile("Bố cục mới")
    controller._on_result(stale)
    assert controller.overlay.blocks == []
    assert controller.session.pinned is False
    assert controller.session.running is False
    assert controller._manual_display is False
    assert controller._manual_region_id is None
    assert controller._find_region("special").name.endswith("mới")
    assert controller.store.load_raw()["regions"][0]["name"].endswith("mới")


def test_cancel_region_selection_keeps_bounds_and_manual_hold(controller):
    pin(controller)
    before = controller._find_region("special")
    controller.capture_adapter.selected = None
    controller.select_region("special")
    assert controller._find_region("special") == before
    assert controller.session.pinned is True
    assert controller.overlay.blocks[0]["target"] == "Thông tin đặc biệt"


def test_official_english_fallback_never_leaks_chinese(controller):
    payload = envelope(controller, target="", source="Hello Jinhsi and Changli")
    controller._on_result(payload)
    assert controller.overlay.blocks[0]["target"] == "Hello Jinhsi and Changli"


def test_history_is_bounded_to_100_results(controller):
    for index in range(110):
        controller._on_result(envelope(controller, target=f"Câu {index}"))
    assert len(controller.history) == 100
    assert controller.history[0]["text"] == "Câu 10"
    assert controller.history[-1]["text"] == "Câu 109"


def test_hotkey_collision_is_visible_and_does_not_persist(controller):
    original = controller._find_region("first")
    controller.update_region("first", hotkey="alt+w")
    assert controller._find_region("first") == original
    assert "trùng" in controller.status
    assert controller.store.load_raw()["regions"][0]["hotkey"] == ""


def test_open_check_does_not_skip_recent_cached_release(controller, monkeypatch):
    from types import SimpleNamespace
    import time
    controller.raw["translation_checked_at"] = time.time()
    marker = object()
    monkeypatch.setattr(controller, "_updater", lambda: SimpleNamespace(check_latest=lambda: marker))
    monkeypatch.setattr(controller, '_app_updater', lambda: SimpleNamespace(check_latest=lambda: 'app release'))
    calls = []
    monkeypatch.setattr(controller, "_start_job", lambda kind, work: calls.append((kind, work())))
    controller.check_updates(force=False)
    assert calls == [("check", {'translation': marker, 'app': 'app release'})]
    controller.check_updates(force=False)
    assert len(calls) == 1


def test_current_release_has_short_status_and_never_prompts_download(controller, monkeypatch):
    from types import SimpleNamespace
    from ludiglot.core.translation_update import ReleaseInfo
    monkeypatch.setattr(controller, "_updater", lambda: SimpleNamespace(active_manifest=lambda: {"release_tag": "v3.0.16"}))
    release = ReleaseInfo("v3.0.16", body="Wuthering Waves 3.7\nLong README must not show", game_version="3.7")
    controller._on_job_finished({"kind": "check", "result": release})
    assert controller.update_summary == "Đã là bản mới nhất Wuthering Waves 3.7"
    assert controller.update_available is False
    monkeypatch.setattr(controller_module.QMessageBox, "question", lambda *_: pytest.fail("Already current: no download dialog"))
    controller.download_update()
    assert controller.status == controller.update_summary


def test_new_release_has_one_line_and_does_not_trust_cached_latest(controller, monkeypatch):
    from types import SimpleNamespace
    from ludiglot.core.translation_update import ReleaseInfo
    controller.raw["latest_translation_release"] = {"tag": "v3.0.16"}
    monkeypatch.setattr(controller, "_updater", lambda: SimpleNamespace(active_manifest=lambda: {"release_tag": "v3.0.15"}))
    controller._on_job_finished({"kind": "check", "result": ReleaseInfo("v3.0.16", body="README", game_version="3.7")})
    assert controller.update_summary == "Có bản cập nhật mới"
    assert controller.update_available is True


def test_joint_check_keeps_app_result_when_translation_network_fails(controller, monkeypatch):
    from types import SimpleNamespace
    from ludiglot.core.app_update import AppRelease
    def offline(): raise OSError('offline translation')
    app_release = AppRelease('v1.0.2', '', 10, '')
    monkeypatch.setattr(controller, '_updater', lambda: SimpleNamespace(check_latest=offline))
    monkeypatch.setattr(controller, '_app_updater', lambda: SimpleNamespace(check_latest=lambda: app_release))
    monkeypatch.setattr(controller, '_start_job', lambda kind, work: controller._on_job_finished({'kind':kind,'result':work()}))
    controller.check_updates()
    assert controller.app_update_available and 'v1.0.2' in controller.app_update_summary
    assert not controller.update_available
    assert 'Không kiểm tra' in controller.update_summary


def test_joint_check_keeps_translation_result_when_app_network_fails(controller, monkeypatch):
    from types import SimpleNamespace
    from ludiglot.core.translation_update import ReleaseInfo
    def offline(): raise OSError('offline app')
    monkeypatch.setattr(controller, '_updater', lambda: SimpleNamespace(
        check_latest=lambda: ReleaseInfo('v3.0.16', game_version='3.7'), active_manifest=lambda:{'release_tag':'v3.0.16'}))
    monkeypatch.setattr(controller, '_app_updater', lambda: SimpleNamespace(check_latest=offline))
    monkeypatch.setattr(controller, '_start_job', lambda kind, work: controller._on_job_finished({'kind':kind,'result':work()}))
    controller.check_updates()
    assert not controller.update_available and 'Đã là bản mới nhất' in controller.update_summary
    assert not controller.app_update_available and 'Không kiểm tra' in controller.app_update_summary


def test_translation_label_uses_friendly_name(controller, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(controller, '_updater', lambda:SimpleNamespace(active_manifest=lambda:{'release_tag':'v3.0.16'}))
    assert 'Bản dịch đang dùng: v3.0.16' in controller.translation_version_summary
    assert 'WAHU đang dùng' not in controller.translation_version_summary


def test_realtime_hotkey_can_turn_off_and_back_on_last_region_without_stopping_intent(controller):
    controller.update_region("first", hotkey="Alt+R")
    controller.update_region("second", enabled=False)
    controller.session.start()
    controller.region_hotkey_action("first")
    assert not controller._find_region("first").enabled
    assert controller.session.running
    assert any(b.name=="region_first" for b in controller._hotkeys.bindings)
    controller.region_hotkey_action("first")
    assert controller._find_region("first").enabled
    assert controller.session.running


def test_region_hotkey_does_not_resume_a_paused_loop_or_manual_hold(controller):
    controller.region_hotkey_action("first")
    assert not controller.session.running
    pin(controller)
    controller.region_hotkey_action("second")
    assert controller.session.pinned
    assert controller._manual_display
    assert controller.overlay.blocks[0]["target"]=="Thông tin đặc biệt"


def test_manual_shortcut_and_disabled_region_route_correctly(controller):
    controller.update_region("special", enabled=False)
    controller.region_hotkey_action("special")
    assert "đang tắt" in controller.status
    assert not controller.session._pending
    controller.update_region("special", enabled=True)
    controller.region_hotkey_action("special")
    assert ("manual","special") in controller.session._pending


def test_failed_region_hotkey_registration_keeps_saved_value_and_restores_bindings(controller,monkeypatch):
    class RejectNew(FakeHotkeys):
        last_error="Phím đang được app khác dùng"
        def register(self,bindings):
            if any(b.hotkey=="Alt+R" for b in bindings):
                return None
            return super().register(bindings)
    monkeypatch.setattr(controller_module,"WindowsNativeHotkeyAdapter",RejectNew)
    before=controller.store.load_raw()
    assert controller.update_region("first",hotkey="alt+r") is False
    assert controller._find_region("first").hotkey==""
    assert controller.store.load_raw()==before
    assert controller._hotkeys is not None
    assert "app khác" in controller.status


def test_failed_global_hotkey_registration_never_persists_new_config(controller,monkeypatch):
    class RejectNew(FakeHotkeys):
        last_error="Windows từ chối tổ hợp"
        def register(self,bindings):
            if any(b.hotkey=="Alt+R" for b in bindings):
                return None
            return super().register(bindings)
    monkeypatch.setattr(controller_module,"WindowsNativeHotkeyAdapter",RejectNew)
    before=controller.store.load_raw()
    assert controller.save_hotkeys({"hotkey_capture":"alt+r"}) is False
    assert controller.store.load_raw()==before
    assert controller.raw["hotkey_capture"]=="alt+w"
    assert controller._hotkeys is not None


def test_profile_collision_with_disabled_region_keeps_current_regions(controller):
    before=controller.regions[:]
    controller.raw["profiles"]["bad"]={"regions":[replace(before[0],hotkey="alt+w",enabled=False).to_dict()]}
    assert controller.load_profile("bad") is False
    assert controller.regions==before
    assert "trùng" in controller.status


def test_valid_profile_keeps_region_shortcuts_and_auto_settings(controller):
    region=replace(controller.regions[0],hotkey="Alt+R",enabled=False)
    controller.raw["profiles"]["new"]={"regions":[region.to_dict()],"auto_wuwa":{"mode":"auto","font_size":32}}
    controller.load_profile("new")
    assert controller.regions[0].hotkey=="Alt+R"
    assert controller.raw["auto_wuwa"]["font_size"]==32
    assert any(b.name=="region_first" for b in controller._hotkeys.bindings)


def test_auto_is_a_virtual_region_and_does_not_enable_legacy_realtime(controller):
    controller.update_auto_settings(mode="auto",bounds={"left":0,"top":0,"width":1920,"height":1080})
    assert controller._find_region("__auto_wuwa__")
    assert controller.session._regions["__auto_wuwa__"].mode=="realtime"
    assert all(r.mode=="manual" for key,r in controller.session._regions.items() if key!="__auto_wuwa__")
    assert not controller.session.running


def test_auto_result_from_old_generation_is_rejected(controller,monkeypatch):
    from ludiglot.core.auto_wuwa import AutoFrameResult
    controller.update_auto_settings(mode="auto",bounds={"left":0,"top":0,"width":1920,"height":1080})
    payload=envelope(controller,"__auto_wuwa__",result={"auto_frame":AutoFrameResult()})
    controller.session.pause()
    monkeypatch.setattr(controller,"_display_auto",lambda _:pytest.fail("Stale Auto result rendered"))
    controller._on_result(payload)
    assert controller._last_auto_frame is None


def test_manual_context_persists_without_resuming_and_rejects_old_job(controller):
    from ludiglot.core.auto_wuwa import AutoTextIndex,AutoWuwaProcessor
    key='Main_Mengzhou_3_6_20_1'
    item={'text_key':key,'source_json':'lang_multi_text_1sthalf.db','official_en':'A verified English sentence that establishes the scene.',
          'official_vi':'Bản Việt','translation_source':'wahu_verified_source_move'}
    controller.auto_processor=AutoWuwaProcessor(AutoTextIndex({'a':{'matches':[item]}}))
    controller.update_auto_settings(mode='auto',bounds={'left':0,'top':0,'width':1920,'height':1080})
    generation=controller.session.generation
    assert controller.set_dialogue_context(key)
    assert controller.session.generation>generation
    assert not controller.session.running
    assert controller.store.load_raw()['auto_wuwa']['context_hint']==key
    assert not controller.set_dialogue_context('Main_Mengzhou_3_6_21_1')
    assert controller.raw['auto_wuwa']['context_hint']==key
    assert controller.set_dialogue_context('')
    assert controller.store.load_raw()['auto_wuwa']['context_hint']==''


def test_accepted_auto_frame_remembers_context_but_never_unverified_text(controller,monkeypatch):
    from ludiglot.core.auto_wuwa import AutoTextIndex,AutoWuwaProcessor,AutoFrameResult,AutoTextBlock
    key='Main_Mengzhou_3_6_20_1'
    text='A verified English sentence that establishes the scene.'
    item={'text_key':key,'source_json':'lang_multi_text_1sthalf.db','official_en':text,
          'official_vi':'Bản Việt','translation_source':'wahu_verified_source_move'}
    controller.auto_processor=AutoWuwaProcessor(AutoTextIndex({'a':{'matches':[item]}}))
    controller.update_auto_settings(mode='auto',bounds={'left':0,'top':0,'width':1920,'height':1080})
    controller.session.resume()
    monkeypatch.setattr(controller,'_display_auto',lambda _:None)
    frame=AutoFrameResult(blocks=(AutoTextBlock('dialogue',(450,890,1000,32),text,'Bản Việt',('lang_multi_text_1sthalf.db',key)),),detected_kinds=('dialogue',),present_kinds=('dialogue',))
    controller._on_result(envelope(controller,'__auto_wuwa__',result={'auto_frame':frame}))
    assert controller.store.load_raw()['auto_wuwa']['context_hint']==key
    bad=AutoFrameResult(detected_kinds=('dialogue',),present_kinds=('dialogue',),observed_texts=(('dialogue','Unverified unrelated text'),))
    controller._on_result(envelope(controller,'__auto_wuwa__',result={'auto_frame':bad}))
    assert controller.store.load_raw()['auto_wuwa']['context_hint']==key


def test_auto_timing_is_numeric_bounded_logging_and_not_ocr_or_screenshot_content(controller,monkeypatch):
    from ludiglot.core.auto_wuwa import AutoFrameResult
    controller.update_auto_settings(mode='auto',bounds={'left':0,'top':0,'width':1920,'height':1080})
    controller.session.resume()
    monkeypatch.setattr(controller,'_display_auto',lambda _:None)
    monkeypatch.setattr(controller_module.time,'perf_counter',lambda:100.0)
    messages=[]; controller.log_message.connect(messages.append)
    payload=envelope(controller,'__auto_wuwa__',result={'auto_frame':AutoFrameResult(),
        'timings':{'capture_ms':10.0,'lookup_ms':5.0,'worker_ms':80.0,'queued_at':99.99}})
    controller._on_result(payload); controller._on_result(payload)
    assert controller.last_auto_timings['capture_to_submit_ms']==pytest.approx(90)
    timing=[m for m in messages if m.startswith('[Auto timing]')]
    assert len(timing)==1 and 'capture_ms=10.0' in timing[0]


def test_hotkey_recording_suspends_and_restores_existing_global_bindings(controller):
    old=controller._hotkeys
    controller.suspend_hotkeys()
    assert old.stopped and controller._hotkeys is None
    controller.resume_hotkeys()
    assert controller._hotkeys is not None


def test_repeated_auto_ocr_errors_stop_and_remove_masks(controller):
    controller.update_auto_settings(mode="auto",bounds={"left":0,"top":0,"width":1920,"height":1080})
    controller.session.resume()
    payload = envelope(controller,"__auto_wuwa__",result=None,error="OCR unavailable")
    controller._on_result(payload)
    assert controller.session.running
    controller._on_result(payload)
    assert controller.session.running
    controller._on_result(payload)
    assert not controller.session.running and controller._last_auto_frame is None
    assert "lỗi liên tiếp" in controller.status


def test_auto_alt_tab_keeps_scheduler_running_even_with_legacy_pause_flag(controller,monkeypatch):
    import win32gui
    controller.raw["pause_on_focus_loss"] = True
    controller.update_auto_settings(mode="auto",bounds={"left":0,"top":0,"width":1920,"height":1080})
    controller.session.resume()
    monkeypatch.setattr(win32gui,"GetForegroundWindow",lambda:123)
    monkeypatch.setattr(win32gui,"GetWindowText",lambda _:"An unrelated app")
    controller._check_environment()
    assert controller.session.running
    assert controller._background_hidden


def test_window_capture_waiting_does_not_count_as_an_ocr_error(controller):
    controller.update_auto_settings(mode="auto",bounds={"left":0,"top":0,"width":1920,"height":1080})
    controller.session.resume()
    for _ in range(5):
        controller._on_result(envelope(controller,"__auto_wuwa__",result=None,error="waiting for frame",status="capture_unavailable"))
    assert controller.session.running
    assert controller._auto_errors == 0


def test_window_mode_auto_uses_selected_client_not_old_screen_bounds(controller):
    controller.raw["window_capture"] = {"mode":"window", "anchor":{"left":200,"top":100,"width":1280,"height":720}}
    assert controller._auto_region().bounds == controller.raw["window_capture"]["anchor"]
    assert not controller._region_problem(controller._auto_region())
    assert "ngoài cửa sổ" in controller._region_problem(controller.regions[0])


def test_window_selection_persists_explicit_identity_and_keeps_old_regions(controller, monkeypatch):
    from ludiglot.core import window_capture
    target = window_capture.WindowTarget(123,456,"Wuthering Waves","D:/Games/Client.exe","GameWindow")
    bounds = {"left":0,"top":0,"width":1920,"height":1080}
    old_regions = list(controller.regions)
    monkeypatch.setattr(window_capture,"list_windows",lambda: [target])
    monkeypatch.setattr(window_capture,"physical_geometry",lambda _: (bounds, []))
    from types import SimpleNamespace
    from ludiglot.ui import game_picker
    monkeypatch.setattr(game_picker,'GamePicker',lambda *_:SimpleNamespace(exec=lambda:1,selected_target=target))
    configured = []
    controller.capture_adapter.configure_window = lambda settings, selected=None: configured.append((settings, selected))
    controller.choose_game_window()
    assert controller.store.load_raw()["window_capture"] == {"mode":"window","target":target.identity(),"anchor":bounds}
    assert configured[-1][1] == target
    assert controller.regions == old_regions
    assert not controller.session.running


def test_canceled_application_selection_leaves_capture_and_run_intent_alone(controller, monkeypatch):
    from ludiglot.core import window_capture
    target = window_capture.WindowTarget(123,456,"Wuthering Waves","D:/Games/Client.exe","GameWindow")
    monkeypatch.setattr(window_capture,"list_windows",lambda: [target])
    from types import SimpleNamespace
    from ludiglot.ui import game_picker
    monkeypatch.setattr(game_picker,'GamePicker',lambda *_:SimpleNamespace(exec=lambda:0,selected_target=None))
    controller.session.resume()
    before = controller.raw["window_capture"]
    controller.choose_game_window()
    assert controller.raw["window_capture"] == before
    assert controller.session.running


def test_failed_window_selection_restores_the_previous_explicit_source(controller, monkeypatch):
    from ludiglot.core import window_capture
    target = window_capture.WindowTarget(123,456,"Wuthering Waves","D:/Games/Client.exe","GameWindow")
    monkeypatch.setattr(window_capture,"list_windows",lambda: [target])
    monkeypatch.setattr(window_capture,"physical_geometry",lambda _: ({"left":0,"top":0,"width":1920,"height":1080}, []))
    from types import SimpleNamespace
    from ludiglot.ui import game_picker
    monkeypatch.setattr(game_picker,'GamePicker',lambda *_:SimpleNamespace(exec=lambda:1,selected_target=target))
    attempted = []
    def configure(settings, selected=None):
        attempted.append(settings)
        if selected:
            raise RuntimeError("WGC unsupported")
    controller.capture_adapter.configure_window = configure
    before = controller.store.load_raw()["window_capture"]
    controller.choose_game_window()
    assert attempted[-1] == before
    assert controller.store.load_raw()["window_capture"] == before
    assert "WGC unsupported" in controller.status


def test_echo_migration_removes_active_manual_regions_and_old_hotkeys_but_preserves_style(controller):
    controller.store.update_values(auto_wuwa={'font_size':38,'background_color':'#334455'},hotkey_capture='Ctrl+F8')
    echo=controller_module.DesktopController(controller.paths,auto_initialize=False,auto_only=True)
    echo.environment_timer.stop()
    try:
        assert echo.regions==[] and echo.special_region_id=='' and echo.auto_mode
        assert echo.raw['auto_wuwa']['font_size']==38 and echo.raw['auto_wuwa']['background_color']=='#334455'
        assert echo.raw['unknown']=={'keep':42}
        assert [binding.name for binding in echo._bindings(echo.raw,[]) ]==['capture']
        assert echo.raw['hotkey_capture']=='Ctrl+F8' and not echo.raw['hotkey_exception']
        assert (controller.paths.config_path.parent/'settings.before-echo.json').is_file()
        assert not echo.session.running
        echo.update_auto_settings(mode='regions'); assert echo.auto_mode
        echo.use_desktop_capture(); assert 'Chọn Wuthering Waves' in echo.status
    finally:
        echo.close()


def test_echo_requires_game_and_only_schedules_auto(controller):
    controller.auto_only=True; controller.update_auto_settings(mode='auto')
    controller._sync_session_regions()
    assert list(controller.session._regions)==['__auto_wuwa__']
    assert not controller._auto_safe_to_start() and 'Chọn game' in controller.status


def test_story_choice_and_side_memory_persist_without_guessing_or_resuming(controller):
    from ludiglot.core.auto_wuwa import AutoTextIndex,AutoWuwaProcessor,AutoTextBlock
    main='Main_Mengzhou_3_6_20_1'; side='Side_DXTS_4_5'
    values=[{'text_key':key,'source_json':'lang.db','official_en':'Distinct verified words '+key,
             'official_vi':'Bản Việt','translation_source':'wahu_exact_id_reviewed'} for key in (main,side)]
    controller.auto_processor=AutoWuwaProcessor(AutoTextIndex({'a':{'matches':values}}))
    assert controller.choose_story('Main_Mengzhou_3_6')
    assert controller.store.load_raw()['auto_wuwa']['story_group']=='Main_Mengzhou_3_6'
    assert not controller.session.running
    assert not controller.choose_story('Main_Unknown_9_9')
    block=AutoTextBlock('dialogue',(500,890,800,32),'Find the missing letter.','Tìm thư',('lang.db',side))
    controller._remember_story(block); controller._remember_story(block)
    assert len(controller.raw['story_memory'])==1
    assert controller.choose_story('Side_DXTS')
    assert controller.raw['auto_wuwa']['context_hint']==side
    assert controller.choose_story('') and not controller.raw['auto_wuwa']['story_group']
