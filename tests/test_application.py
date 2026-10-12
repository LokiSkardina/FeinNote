# Responsible for: portable paths, tray lifecycle and microphone audio retention tests.

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import test_iteration as fixtures
from core.config_manager import ConfigManager
from core.recording_storage import RecordingStorage
from utils import paths
from PyQt6.QtWidgets import QSystemTrayIcon


class ApplicationTests(unittest.TestCase):
    """Use isolated app data, fake capture, and a fake operating-system tray."""

    @classmethod
    def setUpClass(cls):
        """Reuse the test QApplication without contacting models or providers."""
        fixtures.FeatureTests.setUpClass()

    def setUp(self):
        """Create project-local fixtures and prevent shared QApplication termination."""
        self.fixture = fixtures.FeatureTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.config = self.fixture.config
        self.controller = self.fixture.controller()
        self.controller.recording_storage = RecordingStorage(self.root)
        self.controller.load_selected_model = lambda: True
        self.windows = []
        self.quit_patch = patch("PyQt6.QtWidgets.QApplication.quit")
        self.quit = self.quit_patch.start()
        self.addCleanup(self.quit_patch.stop)
        self.tray_patch = patch("app_window.QSystemTrayIcon")
        self.tray_class = self.tray_patch.start()
        self.tray_class.isSystemTrayAvailable.return_value = True
        self.tray_class.ActivationReason = QSystemTrayIcon.ActivationReason
        self.addCleanup(self.tray_patch.stop)

    def tearDown(self):
        """Complete requested shutdown and dispose only this test's temporary data."""
        for window in self.windows:
            window.request_exit()
            self.fixture.wait_for(lambda: window._shutdown_complete)
            window.deleteLater()
        self.fixture.app.processEvents()
        self.fixture.tearDown()

    def window(self):
        """Construct the real main View with fake engine and tray adapters."""
        import app_window
        with patch.object(app_window, "AppController", return_value=self.controller):
            window = app_window.WhisperNoteApp()
        self.windows.append(window)
        window.show()
        self.fixture.app.processEvents()
        return window

    def test_settings_persist_reload_and_invalid_legacy_values(self):
        """Application toggles survive reload and invalid legacy values remain off."""
        self.assertEqual(self.config.get_application_settings(), ConfigManager.APPLICATION_DEFAULTS)
        for name in ConfigManager.APPLICATION_DEFAULTS:
            self.controller.set_application_setting(name, True)
        loaded = ConfigManager(str(self.root / "config.json"))
        self.assertTrue(all(loaded.get_application_settings().values()))
        self.config.set_section("application", {"minimize_to_tray": "false"})
        self.assertFalse(any(self.config.get_application_settings().values()))

    def test_minimize_off_and_on(self):
        """Normal minimize stays visible; enabled tray minimize hides the window."""
        window = self.window()
        window.showMinimized()
        self.fixture.app.processEvents()
        self.assertFalse(window._hidden_in_tray)
        self.assertTrue(window.isVisible())
        window.showNormal()
        self.controller.set_application_setting("minimize_to_tray", True)
        window.showMinimized()
        self.fixture.app.processEvents()
        self.assertTrue(window._hidden_in_tray)
        self.assertFalse(window.isVisible())
        window.restore_from_tray()
        self.assertTrue(window.isVisible())
        self.assertFalse(window.isMinimized())

    def test_close_to_tray_and_explicit_exit(self):
        """Close hides; the tray Exit action bypasses that preference permanently."""
        self.controller.set_application_setting("close_to_tray", True)
        window = self.window()
        window.close()
        self.assertTrue(window._hidden_in_tray)
        self.assertFalse(window._exit_requested)
        self.assertFalse(self.controller.is_shutting_down)
        self.assertEqual([action.text() for action in window._tray_menu.actions() if action.isVisible() and not action.isSeparator()],
                         ["Open FeinNote", "Start recording", "Copy last note", "Exit"])
        window._tray_exit.trigger()
        self.fixture.wait_for(lambda: window._shutdown_complete)
        self.assertTrue(window._exit_requested)
        self.quit.assert_called_once()
        self.tray_class.return_value.hide.assert_called()

    def test_close_without_tray_and_unavailable_tray(self):
        """An unavailable tray must not leave an inaccessible hidden application."""
        self.tray_class.isSystemTrayAvailable.return_value = False
        self.controller.set_application_setting("close_to_tray", True)
        window = self.window()
        window.close()
        self.fixture.wait_for(lambda: window._shutdown_complete)
        self.assertFalse(window._hidden_in_tray)
        self.quit.assert_called_once()

    def test_close_off_exits(self):
        """The default X action performs controlled shutdown."""
        window = self.window()
        window.close()
        self.fixture.wait_for(lambda: window._shutdown_complete)
        self.quit.assert_called_once()

    def test_repeated_hide_restore_and_double_click(self):
        """Show/hide and activation reuse exactly one icon and context menu."""
        self.controller.set_application_setting("close_to_tray", True)
        window = self.window()
        for _ in range(4):
            window.close()
            window._tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
            self.assertTrue(window.isVisible())
        self.tray_class.assert_called_once()
        self.tray_class.return_value.setContextMenu.assert_called_once()

    def test_exit_drains_active_work(self):
        """Exit is remembered while a worker finishes and does not fall back to tray."""
        window = self.window()
        self.controller.is_processing = True
        window.request_exit()
        self.fixture.app.processEvents()
        self.assertFalse(window._shutdown_complete)
        self.controller.is_processing = False
        self.fixture.wait_for(lambda: window._shutdown_complete)
        self.quit.assert_called_once()

    def recording(self):
        """Create an owned WAV fixture without opening a microphone."""
        source = Path(self.controller.recording_storage.create_temporary())
        source.write_bytes(b"audio fixture")
        return source

    def transcribe(self, source, recording=True):
        """Run the real threaded controller pipeline with a fake loaded engine."""
        self.controller.engine.transcribe.return_value = {"text": "Recorded text.", "duration": 2}
        self.assertTrue(self.controller._start_transcription(str(source), 2, recording=recording))
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        return self.fixture.repo.read_note(self.controller._raw_note_path)

    def test_audio_off_discards_only_after_success(self):
        """Successful raw note saving permits removal of owned temporary audio."""
        source = self.recording()
        data = self.transcribe(source)
        self.assertFalse(source.exists())
        self.assertIsNone(data["audio_path"])
        self.assertEqual(data["raw_text"], "Recorded text.")

    def test_audio_on_persists_portable_reference_and_unique_files(self):
        """Retained WAV files and note references survive repository reload."""
        self.controller.set_application_setting("keep_recording_audio", True)
        references = []
        for _ in range(2):
            source = self.recording()
            data = self.transcribe(source)
            self.assertFalse(source.exists())
            self.assertFalse(Path(data["audio_path"]).is_absolute())
            self.assertTrue(data["audio_path"].startswith("recordings/inbox/"))
            self.assertEqual((self.root / data["audio_path"]).read_bytes(), b"audio fixture")
            disk = json.loads(Path(self.controller._raw_note_path).read_text(encoding="utf-8"))
            self.assertEqual(disk["audio_path"], data["audio_path"])
            references.append(data["audio_path"])
        self.assertNotEqual(*references)

    def test_imported_file_is_not_copied_or_removed(self):
        """The retention toggle affects microphone input only."""
        self.controller.set_application_setting("keep_recording_audio", True)
        source = self.root / "import.wav"
        source.write_bytes(b"external fixture")
        data = self.transcribe(source, recording=False)
        self.assertTrue(source.exists())
        self.assertIsNone(data["audio_path"])
        self.assertFalse((self.root / "recordings").exists())

    def test_failed_save_preserves_audio(self):
        """A storage failure must not delete the only microphone recording."""
        source = self.recording()
        self.controller._pipeline_recording = str(source)
        with patch.object(self.fixture.repo, "create_note", side_effect=OSError("disk full")):
            self.controller._on_transcription_result({"text": "source", "duration": 2})
        self.assertTrue(source.exists())

    def test_old_note_audio_is_nullable(self):
        """Legacy notes lacking audio_path still open and retain it through edits."""
        path = self.root / "notes" / "legacy.json"
        path.write_text(json.dumps(dict(full_text="old", metadata="old", timestamp=100)), encoding="utf-8")
        self.assertIsNone(self.fixture.repo.read_note(str(path))["audio_path"])
        self.fixture.repo.update_note_text(str(path), "edited")
        self.assertIsNone(self.fixture.repo.read_note(str(path))["audio_path"])

    def test_portable_paths_ignore_working_directory_and_bundle(self):
        """Persistent data follows the executable, resources follow the bundle."""
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(Path(paths.get_app_root()), Path(__file__).resolve().parents[1])
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(self.root / "FeinNote.exe")), patch.object(sys, "_MEIPASS", str(self.root / "bundle"), create=True):
                self.assertEqual(Path(paths.get_data_path("config.json")), self.root / "config.json")
                self.assertEqual(Path(paths.get_resource_path("icon.ico")), self.root / "bundle" / "icon.ico")
        finally:
            os.chdir(original)

    def test_application_controls_reload(self):
        """Application widgets reflect controller changes without rebuilding the dialog."""
        from widgets.settings_dialog import SettingsDialog
        from utils.themes import dark_theme
        window = self.window()
        dialog = SettingsDialog(window, theme=dark_theme)
        dialog._keep_audio_toggle.setChecked(True)
        self.assertTrue(self.config.get_application_settings()["keep_recording_audio"])
        self.controller.set_application_setting("keep_recording_audio", False)
        self.assertFalse(dialog._keep_audio_toggle.isChecked())
        self.assertEqual(dialog.theme_selector.count(), 2)
        dialog.close()

    def test_autosave_preserves_empty_and_whitespace_edits(self):
        """Clearing a note or editing whitespace must survive reopening it."""
        path = self.fixture.note("Original")
        self.fixture.notes.select_note(path)
        for text in ("", "  edited\n\n"):
            self.fixture.notes.set_text_getter(lambda: text)
            self.fixture.notes.force_save()
            self.assertEqual(self.fixture.repo.read_note(path)["full_text"], text)
            self.assertEqual(self.fixture.repo.read_note(path)["raw_text"], "Original")

    def test_nonfinite_legacy_timestamp_does_not_break_history(self):
        """An invalid sortable timestamp must not prevent other notes loading."""
        path = Path(self.fixture.note("Broken date"))
        data = json.loads(path.read_text(encoding="utf-8"))
        data["timestamp"] = "Infinity"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.fixture.note("Valid date")
        self.assertEqual(len(self.fixture.repo.load_notes("inbox")), 2)

    def test_watchdog_and_queued_completion_emit_once(self):
        """A late queued completion cannot transcribe watchdog-recovered audio twice."""
        recording = self.controller.recording_controller
        source = self.root / "watchdog.wav"
        source.write_bytes(b"x" * 33000)
        recording.recorder.output_filename = str(source)
        recording.recorder.is_running.return_value = False
        recording.recording_ready.disconnect()
        ready = Mock()
        recording.recording_ready.connect(ready)
        recording._on_stop_timeout()
        recording._on_recording_finished(str(source))
        self.assertEqual(ready.call_count, 1)

    def test_paused_recording_drains_and_discards_microphone_frames(self):
        """Pause must drain live input without storing paused or in-flight frames."""
        from core.recorder import AudioRecorder
        with patch("core.recorder.pyaudio.PyAudio") as audio:
            audio.return_value.get_sample_size.return_value = 2
            recorder = AudioRecorder()
            recorder._is_recording = True
            recorder._is_paused = True
            stream = audio.return_value.open.return_value
            def read(*args, **kwargs):
                recorder._is_recording = False
                return b"paused input"
            stream.read.side_effect = read
            # The old loop sleeps forever while paused; fail instead of hanging.
            with patch("core.recorder.time.sleep", side_effect=AssertionError("Input was not drained")):
                recorder._record_loop()
            stream.read.assert_called_once()
            self.assertEqual(recorder.frames, [])

    def test_watchdog_keeps_live_capture_busy(self):
        """A timed-out but live recorder must still block a new audio workflow."""
        recording = self.controller.recording_controller
        recording.state = recording.RECORDING
        recording.is_stopping = True
        recording.recorder.is_running.return_value = True
        recording._on_stop_timeout()
        self.assertEqual(recording.state, recording.RECORDING)
        self.assertTrue(recording.is_stopping)
        self.assertFalse(recording.start())

    def test_resume_keeps_only_unpaused_frames(self):
        """Discard paused input including the read overlapping a resume event."""
        from core.recorder import AudioRecorder
        with patch("core.recorder.pyaudio.PyAudio") as audio:
            audio.return_value.get_sample_size.return_value = 2
            recorder = AudioRecorder()
            recorder._is_recording = True
            recorder._is_paused = True
            chunks = iter((b"paused", b"resuming", b"speech", b"stopping"))
            def read(*args, **kwargs):
                chunk = next(chunks)
                if chunk == b"resuming":
                    recorder.resume_recording()
                elif chunk == b"stopping":
                    recorder.stop_recording()
                return chunk
            audio.return_value.open.return_value.read.side_effect = read
            with patch.object(recorder, "_save_to_file_safely", return_value=None):
                recorder._record_loop()
            self.assertEqual(recorder.frames, [b"speech"])

    def test_folder_lucide_name_and_legacy_aliases(self):
        """Persist only icon names; older aliases and missing assets stay readable."""
        from core.notes.folder_manager import FolderManager
        from utils.icon_manager import resolve_name
        fid = self.fixture.repo.folders.save_folder("Games", "#3b82f6", "gamepad-2", {"mode": "disabled", "value": 30})
        reopened = FolderManager(self.fixture.repo.notes_dir)
        self.assertEqual(next(f for f in reopened.list_folders() if f["id"] == fid)["icon"], "gamepad-2")
        self.assertEqual(resolve_name("work"), "briefcase-business")
        self.assertEqual(resolve_name("unknown-icon-name"), "folder")
        with self.assertRaises(ValueError):
            reopened.save_folder("Unsafe", "#3b82f6", "../icon", {"mode": "disabled", "value": 30})

    def test_lucide_lookup_theme_color_and_lazy_picker(self):
        """Render known/fallback icons and filter a catalog without eager SVG reads."""
        from utils import icon_manager as icons
        from utils.themes import light_theme, dark_theme
        from widgets.icon_picker import IconPicker, IconListModel
        self.assertGreater(len(icons.available_icons()), 1000)
        self.assertFalse(icons.get_icon("gamepad-2").isNull())
        for name in ("settings", "mic", "upload", "copy", "download", "trash-2", "wand-sparkles", "save", "rotate-ccw", "server", "pause", "play", "square", "x"):
            self.assertNotEqual(icons.resolve_name(name), "folder", name)
        self.assertEqual(icons.get_icon("../missing").cacheKey(), icons.get_icon("folder").cacheKey())
        light = icons.get_pixmap("folder", theme=light_theme).toImage()
        dark = icons.get_pixmap("folder", theme=dark_theme).toImage()
        self.assertNotEqual(light, dark)
        with patch("widgets.icon_picker.get_icon") as render:
            model = IconListModel("#3b82f6")
            model.filter("gamepad")
            self.assertIn("gamepad-2", model.names)
            render.assert_not_called()
        picker = IconPicker("gamepad-2", "#3b82f6", dark_theme)
        self.assertEqual(picker.selected_name(), "gamepad-2")
        picker.search.setText("no-such-icon-123")
        self.assertEqual(picker.model.rowCount(), 0)
        self.assertFalse(picker.ok_button.isEnabled())
        picker.close()

    def test_recording_folder_structure_and_legacy_audio(self):
        """New audio uses folder IDs; renames and legacy flat references stay valid."""
        self.controller.set_application_setting("keep_recording_audio", True)
        fid = self.fixture.folder()
        self.controller.select_folder(fid)
        data = self.transcribe(self.recording())
        self.assertTrue(data["audio_path"].startswith(f"recordings/{fid}/"))
        self.fixture.repo.folders.save_folder("Renamed", "#3b82f6", "folder", {"mode": "disabled", "value": 30}, fid)
        self.assertTrue((self.root / data["audio_path"]).exists())
        legacy = self.root / "recordings" / "old.wav"
        legacy.write_bytes(b"legacy")
        note, path = self.fixture.repo.create_note("Old", "metadata", 1, audio_path="recordings/old.wav")
        self.assertEqual((self.root / self.fixture.repo.read_note(path)["audio_path"]).read_bytes(), b"legacy")
        source = self.recording()
        with self.assertRaises(ValueError):
            self.controller.recording_storage.retain(source, "../outside")
        self.assertTrue(source.exists())

    def test_tray_record_pause_resume_stop_pipeline(self):
        """Tray actions drive the existing recording controller and transcription."""
        window = self.window()
        recording = self.controller.recording_controller
        recording.recorder.is_running.return_value = False
        self.controller.config_manager.set_language("en")
        with patch.object(recording, "is_microphone_available", return_value=True):
            window._tray_start.trigger()
        recording.recorder.start_recording.assert_called_once()
        self.assertEqual(recording.state, recording.RECORDING)
        self.assertFalse(window._tray_start.isVisible())
        window._tray_pause.trigger()
        self.assertEqual(recording.state, recording.PAUSED)
        self.assertEqual(window._tray_pause.text(), "Resume recording")
        window._tray_pause.trigger()
        self.assertEqual(recording.state, recording.RECORDING)
        window._tray_stop.trigger()
        recording.recorder.manual_stop.assert_called_once()
        self.assertFalse(window._tray_stop.isEnabled())
        source = self.recording()
        source.write_bytes(b"x" * 33000)
        self.controller.engine.transcribe.return_value = {"text": "Tray transcript", "duration": 2}
        recording._on_recording_finished(str(source))
        self.assertFalse(window._tray_start.isEnabled())
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.fixture.repo.read_note(self.controller._raw_note_path)["full_text"], "Tray transcript")
        self.assertTrue(window._tray_start.isEnabled())

    def test_tray_cancel_cleans_only_cancelled_session(self):
        """Cancel uses existing ownership cleanup and never creates a note."""
        window = self.window()
        recording = self.controller.recording_controller
        recording.recorder.is_running.return_value = False
        self.controller.config_manager.set_language("en")
        with patch.object(recording, "is_microphone_available", return_value=True):
            window._tray_start.trigger()
        source = self.recording()
        recording.recorder.output_filename = str(source)
        window._tray_cancel.trigger()
        self.assertFalse(window._tray_start.isEnabled())
        recording._on_recording_finished(str(source))
        window._refresh_tray_menu()
        self.assertTrue(window._tray_start.isEnabled())
        self.assertFalse(source.exists())
        self.assertEqual(self.fixture.repo.load_notes(), [])

    def test_copy_latest_final_note_across_folders(self):
        """Copy resolves the newest surviving final text, not the selected folder."""
        from PyQt6.QtWidgets import QApplication
        window = self.window()
        self.assertFalse(window._tray_copy.isEnabled())
        self.fixture.note("Older", timestamp=1)
        fid = self.fixture.folder()
        path = self.fixture.note("Raw", folder=fid, timestamp=2)
        self.fixture.repo.update_note_text(path, "Refined final")
        window._refresh_tray_menu()
        window._tray_copy.trigger()
        self.assertEqual(QApplication.clipboard().text(), "Refined final")
        self.fixture.repo.delete_note(path)
        window._tray_copy.trigger()
        self.assertEqual(QApplication.clipboard().text(), "Older")

    def test_settings_labels_theme_values_and_spin_controls(self):
        """Settings retain config values and keyboard spin input without native arrows."""
        from widgets.settings_dialog import SettingsDialog
        from PyQt6.QtWidgets import QAbstractSpinBox
        from utils.themes import dark_theme
        window = self.window()
        dialog = SettingsDialog(window, theme=dark_theme, current_theme="dark")
        self.assertEqual(dialog.theme_selector.currentText(), "Dark")
        self.assertEqual(dialog.theme_selector.currentData(), "dark")
        panel = dialog.notes_panel
        panel.mode.setCurrentIndex(panel.mode.findData("count"))
        self.assertEqual(panel.value_label.text(), "Number of notes to keep")
        panel.mode.setCurrentIndex(panel.mode.findData("days"))
        self.assertEqual(panel.value_label.text(), "Delete notes older than (days)")
        for spin in dialog.findChildren(QAbstractSpinBox):
            self.assertEqual(spin.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.NoButtons)
        before = panel.value.value()
        panel.value.stepUp()
        self.assertEqual(panel.value.value(), before + 1)
        dialog.close()

    def test_copy_latest_uses_subsecond_creation_time(self):
        """Two notes recorded within one second must copy the newer creation."""
        from datetime import datetime
        for microsecond, text in ((900000, "Newer"), (100000, "Older")):
            self.fixture.repo.create_note(text, "metadata", 2000000, now=datetime.fromtimestamp(2000000).replace(microsecond=microsecond))
        self.assertEqual(self.fixture.notes.latest_note_text(), "Newer")

    def settings_dialog(self):
        """Open Settings against isolated controller state and return its real widgets."""
        from widgets.settings_dialog import SettingsDialog
        from utils.themes import dark_theme
        window = self.window()
        dialog = SettingsDialog(window, theme=dark_theme)
        window.settings_dialog = dialog
        dialog.show()
        self.fixture.app.processEvents()
        return window, dialog

    def test_settings_wheel_scrolls_page_without_changing_fields(self):
        """Closed combos and spin fields cannot change values from page scrolling."""
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QWheelEvent
        from PyQt6.QtWidgets import QApplication, QScrollArea
        from PyQt6.QtTest import QTest
        _, dialog = self.settings_dialog()
        dialog._toggle_advanced_section()
        self.fixture.app.processEvents()
        area = dialog.stack.widget(0).findChild(QScrollArea)
        for field in (dialog.model_selector, dialog.adv_temperature, dialog.adv_beam_size):
            area.verticalScrollBar().setValue(0)
            before = field.currentText() if hasattr(field, "currentText") else field.value()
            event = QWheelEvent(QPointF(5, 5), QPointF(field.mapToGlobal(QPoint(5, 5))), QPoint(), QPoint(0, -120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(field, event)
            self.assertEqual(field.currentText() if hasattr(field, "currentText") else field.value(), before)
            self.assertGreater(area.verticalScrollBar().value(), 0)
        before = dialog.adv_beam_size.value()
        QTest.keyClick(dialog.adv_beam_size, Qt.Key.Key_Up)
        self.assertEqual(dialog.adv_beam_size.value(), before + 1)

    def test_settings_dropdown_click_selection_still_works(self):
        """Explicit popup clicks remain usable despite the wheel guard."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        _, dialog = self.settings_dialog()
        combo = dialog.model_selector
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
        self.fixture.app.processEvents()
        self.assertTrue(combo.view().isVisible())
        index = combo.model().index(1, 0)
        QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=combo.view().visualRect(index).center())
        self.assertEqual(combo.currentIndex(), 1)

    def test_advanced_dependencies_preserve_inactive_values(self):
        """Temperature changes only usage states, not the user's beam/best values."""
        _, dialog = self.settings_dialog()
        dialog.adv_beam_size.setValue(7)
        dialog.adv_best_of.setValue(8)
        for temperature in (0.0, 0.5, 0.0):
            dialog.adv_temperature.setValue(temperature)
            self.assertEqual(dialog.adv_beam_size.isEnabled(), temperature == 0)
            self.assertEqual(dialog.adv_best_of.isEnabled(), temperature > 0)
            self.assertEqual(dialog.adv_beam_size.value(), 7)
            self.assertEqual(dialog.adv_best_of.value(), 8)
        stored = self.config.get_advanced_transcription_options()
        self.assertEqual((stored["beam_size"], stored["best_of"]), (7, 8))
        self.assertIn("Best Of", dialog.adv_dependency_hint.text())

    def test_missing_model_requires_consent_before_any_loading(self):
        """A rejected acquisition cannot mutate selection, language or start a worker."""
        selection = {"engine": "openai-whisper", "model": "large-v3", "device_mode": "cpu"}
        self.controller.engine.is_model_loaded.return_value = False
        previous = self.config.get_model()
        with patch.object(self.controller.model_registry, "is_model_present", return_value=False), patch.object(self.controller, "_start_model_load", return_value=True) as load:
            self.assertFalse(self.controller.request_transcription_change(selection))
            load.assert_not_called()
            self.assertEqual(self.config.get_model(), previous)
            self.assertTrue(self.controller.request_transcription_change(selection, allow_download=True))
            load.assert_called_once()

    def test_model_confirmation_cancel_restore_and_accept(self):
        """Cancel restores Settings; confirmation explicitly authorizes acquisition."""
        window, dialog = self.settings_dialog()
        dialog.modelChangeRequested.connect(window.on_model_changed)
        self.controller.engine.is_model_loaded.return_value = False
        previous = self.config.get_model()
        with patch.object(self.controller.model_registry, "is_model_present", return_value=False), patch.object(self.controller, "_start_model_load", return_value=True) as load:
            with patch.object(window, "_confirm_model_download", return_value=False) as confirm:
                dialog.model_selector.setCurrentText("large-v3")
                confirm.assert_called_once()
                load.assert_not_called()
                self.assertEqual(dialog.model_selector.currentText(), previous)
            with patch.object(window, "_confirm_model_download", return_value=True):
                dialog.model_selector.setCurrentText("large-v3")
                load.assert_called_once()

    def test_cached_model_switch_does_not_prompt(self):
        """Existing cached models can use the established loading path directly."""
        window, dialog = self.settings_dialog()
        dialog.modelChangeRequested.connect(window.on_model_changed)
        self.controller.engine.is_model_loaded.return_value = False
        with patch.object(self.controller.model_registry, "is_model_present", return_value=True), patch.object(window, "_confirm_model_download") as confirm, patch.object(self.controller, "_start_model_load", return_value=True) as load:
            dialog.model_selector.setCurrentText("large-v3")
            confirm.assert_not_called()
            load.assert_called_once()

    def test_configuration_warning_preserves_runtime_status(self):
        """Informational decoding warnings never overwrite recording/progress status."""
        window = self.window()
        window.update_status_display("Transcription")
        with patch.object(window, "update_status_display") as update:
            self.controller.transcription_warning.emit("Temperature is > 0; beam size will be ignored.")
            update.assert_not_called()

    def test_delayed_tooltip_cancels_and_restarts_hover(self):
        """One timer waits, cancels early leaves, and displays only the latest target."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtTest import QTest
        _, dialog = self.settings_dialog()
        manager = dialog.interactions
        self.assertEqual(manager.timer.interval(), 2000)
        with patch("widgets.themed_controls.QToolTip.showText") as show, patch("widgets.themed_controls.QToolTip.hideText") as hide:
            manager.eventFilter(dialog.engine_selector, QEvent(QEvent.Type.Enter))
            self.assertTrue(manager.timer.isActive())
            QTest.qWait(20)
            show.assert_not_called()
            manager.eventFilter(dialog.engine_selector, QEvent(QEvent.Type.Leave))
            self.assertFalse(manager.timer.isActive())
            self.assertIsNone(manager.target)
            manager.eventFilter(dialog.engine_selector, QEvent(QEvent.Type.Enter))
            manager.eventFilter(dialog.model_selector, QEvent(QEvent.Type.Enter))
            self.assertIs(manager.target, dialog.model_selector)
            manager.timer.stop()
            with patch("widgets.themed_controls.QApplication.widgetAt", return_value=dialog.model_selector):
                manager._show()
            show.assert_called_once()
            manager.eventFilter(dialog.model_selector, QEvent(QEvent.Type.Leave))
            hide.assert_called()
            self.assertTrue(manager.eventFilter(dialog.engine_selector, QEvent(QEvent.Type.ToolTip)))
