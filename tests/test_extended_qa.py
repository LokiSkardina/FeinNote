# Responsible for: isolated GUI workflows and loopback HTTP regression research.

import json
import os
import socket
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler, build_opener

from tests import test_application as application
from tests import test_iteration as fixtures
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PyQt6.QtTest import QTest, QSignalSpy
from PyQt6.QtWidgets import QApplication, QMessageBox
from core.config_manager import ConfigManager
from core.notes.notes_repository import NotesRepository
from core.postprocessing.providers.openai_compatible import OpenAICompatibleProvider, NoRedirect
from core.transcription.model_registry import ModelRegistry
from utils.icon_manager import get_icon, resolve_name


class LoopbackServer:
    """Serve scripted responses on an ephemeral loopback port; record text payloads."""

    def __init__(self):
        """Start a local HTTP server without credentials or external connections."""
        self.status, self.delay, self.invalid_json = 200, 0, False
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            """Respond only to the fake models and completions endpoints."""

            def log_message(self, *_args):
                """Suppress request logging, including headers and payloads."""

            def do_GET(self):
                """Handle a models request and record that no body was sent."""
                self.respond(None)

            def do_POST(self):
                """Decode the exact JSON body for privacy assertions."""
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.respond(json.loads(body))

            def respond(self, body):
                """Apply the requested failure/delay and send a bounded response."""
                owner.requests.append((self.command, self.path, body))
                time.sleep(owner.delay)
                value = ({'data': [{'id': 'fake-model'}]} if self.command == 'GET'
                         else {'choices': [{'message': {'content': 'Edited text.'}}]})
                payload = b'not-json' if owner.invalid_json else json.dumps(value).encode()
                try:
                    self.send_response(owner.status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Expected when the timeout test closes its socket early.

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def close(self):
        """Stop the listener and join its thread before fixture disposal."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class ExtendedQATests(unittest.TestCase):
    """Use real widgets/controllers with temporary storage and fake hardware."""

    @classmethod
    def setUpClass(cls):
        """Reuse the existing suite's offscreen QApplication and DLL import order."""
        application.ApplicationTests.setUpClass()
        # Older fixtures leave deferred widget deletions and feedback callbacks.
        # Drain them under a capture hook, before attributing new errors to a test.
        import sys
        cls.inherited_callback_errors = []
        with patch.object(sys, 'excepthook', side_effect=lambda *args: cls.inherited_callback_errors.append(str(args[1]))):
            QTest.qWait(2300)

    def setUp(self):
        """Compose existing fixtures without inheriting and duplicating their tests."""
        self.harness = application.ApplicationTests()
        self.harness.setUp()
        self.fixture = self.harness.fixture
        self.controller = self.harness.controller
        self.config = self.harness.config
        self.root = self.harness.root
        # Keep the catalog, but fake cache and device availability for all GUI tests.
        self.registry = ModelRegistry(str(self.root / 'models'))
        self.registry.is_model_present = Mock(return_value=True)
        self.registry.is_cuda_available = Mock(return_value=True)
        self.controller.model_registry = self.registry
        self.engine_patch = patch.object(self.controller, '_create_engine', side_effect=self.fake_engine)
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)
        self.errors = []
        import sys
        self.hook_patch = patch.object(sys, 'excepthook', side_effect=lambda *args: self.errors.append(args))
        self.hook_patch.start()
        self.addCleanup(self.hook_patch.stop)

    def tearDown(self):
        """Drain asynchronous work before disposing project-local temporary files."""
        try:
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
            # Finish normal saved-feedback callbacks while their widgets still exist.
            if self.harness.windows:
                self.fixture.notes.autosave.force_save()
            QTest.qWait(1700)
            self.harness.tearDown()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertEqual([str(error[1]) for error in self.errors], [], 'Unhandled Qt callback exception')
        finally:
            self.harness.doCleanups()

    @staticmethod
    def fake_engine(_engine_id, device):
        """Return an in-memory engine whose model load never accesses files/network."""
        engine = Mock()
        engine.device = device
        engine.is_model_loaded.return_value = False
        return engine

    def window(self):
        """Create a real main window using the existing isolated test harness."""
        window = self.harness.window()
        # Do not let an error message box block an unattended QA run.
        window.show_error_dialog = Mock()
        return window

    def dialog(self, window):
        """Open the real modal Settings via its production entry point, then close it."""
        QTimer.singleShot(0, lambda: window.settings_dialog.reject())
        window.show_settings_dialog()
        return window.settings_dialog

    @staticmethod
    def icon_image(button):
        """Compare rendered icon pixels rather than unstable QIcon cache identities."""
        return button.icon().pixmap(24, 24).toImage()

    def test_settings_twenty_modal_cycles_and_tabs(self):
        """Repeated modal openings reuse state without duplicate theme signals."""
        window = self.window()
        first = self.dialog(window)
        spy = QSignalSpy(first.themeChangeRequested)
        for iteration in range(20):
            dialog = self.dialog(window)
            self.assertIs(dialog, first)
            dialog.show()
            for index, button in enumerate(dialog.segmented._buttons):
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.assertEqual(dialog.stack.currentIndex(), index)
            name = 'light' if iteration % 2 else 'dark'
            dialog.theme_selector.setCurrentIndex(dialog.theme_selector.findData(name))
            self.assertEqual(ConfigManager(str(self.root / 'config.json')).get_theme(), name)
            dialog.reject()
        self.assertLessEqual(len(spy), 20)
        self.assertTrue(window.primary_button.isEnabled())

    def test_copy_feedback_three_completed_cycles(self):
        """Copy transfers Unicode and restores its rendered icon after every timeout."""
        window = self.window()
        self.fixture.notes.create_transcription_note('Тест 📝 Unicode', 1)
        original = self.icon_image(window.copy_button)
        check = get_icon('check', theme=window.theme).pixmap(24, 24).toImage()
        for _ in range(3):
            QTest.mouseClick(window.copy_button, Qt.MouseButton.LeftButton)
            self.assertEqual(QApplication.clipboard().text(), 'Тест 📝 Unicode')
            self.assertEqual(self.icon_image(window.copy_button), check)
            QTest.qWait(2300)
            self.assertEqual(self.icon_image(window.copy_button), original)

    def test_copy_rapid_second_click_restores_original_icon(self):
        """Regression: a second click during feedback must not preserve the check icon."""
        window = self.window()
        self.fixture.notes.create_transcription_note('Copy twice', 1)
        original = self.icon_image(window.copy_button)
        QTest.mouseClick(window.copy_button, Qt.MouseButton.LeftButton)
        QTest.qWait(100)
        QTest.mouseClick(window.copy_button, Qt.MouseButton.LeftButton)
        QTest.qWait(2400)
        self.assertEqual(QApplication.clipboard().text(), 'Copy twice')
        self.assertEqual(self.icon_image(window.copy_button), original)

    def test_copy_feedback_after_window_destruction(self):
        """Pending feedback must not access a deleted Qt button during shutdown."""
        window = self.window()
        self.fixture.notes.create_transcription_note('Close immediately', 1)
        window.copy_button.click()
        window.request_exit()
        self.fixture.wait_for(lambda: window._shutdown_complete)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.harness.windows.remove(window)
        QTest.qWait(2300)
        # tearDown asserts captured callback exceptions, including deleted buttons.

    def test_advanced_boundaries_unicode_and_disk_reload(self):
        """Exercise limits, dependencies and text through actual Settings controls."""
        window = self.window()
        dialog = self.dialog(window)
        for value in (0, dialog.adv_temperature.singleStep(), 0.3, 0.7, dialog.adv_temperature.maximum()):
            with self.subTest(temperature=value):
                dialog.adv_temperature.setValue(value)
                self.assertEqual(dialog.adv_beam_size.isEnabled(), value == 0)
                self.assertEqual(dialog.adv_best_of.isEnabled(), value > 0)
                self.assertEqual(self.config.get_advanced_transcription_options()['temperature'], value)
        for widget, key in ((dialog.adv_beam_size, 'beam_size'), (dialog.adv_best_of, 'best_of')):
            for value in (widget.minimum(), widget.maximum()):
                widget.setValue(value)
                self.assertEqual(self.config.get_advanced_transcription_options()[key], value)
        for value in (True, False):
            dialog.adv_condition.setChecked(value)
            self.assertEqual(self.config.get_advanced_transcription_options()['condition_on_previous_text'], value)
        for value in ('', 'Русский текст', '📝 中文 café\n' * 800):
            dialog.adv_prompt.setPlainText(value)
            loaded = ConfigManager(str(self.root / 'config.json')).get_advanced_transcription_options()
            self.assertEqual(loaded['initial_prompt'], value.strip() or None)
        self.assertIs(self.dialog(window), dialog)
        self.assertEqual(dialog.adv_prompt.toPlainText(), value)
        # A newly constructed dialog verifies persistence, not just retained widgets.
        from widgets.settings_dialog import SettingsDialog
        fresh = SettingsDialog(window, theme=window.theme)
        self.assertEqual(fresh.adv_prompt.toPlainText(), value.strip())
        fresh.deleteLater()
        dialog.adv_reset_btn.click()
        self.assertEqual(self.config.get_advanced_transcription_options(), ConfigManager.DEFAULT_ADVANCED)

    def test_advanced_rapid_values(self):
        """Rapid edits retain the last values and preserve inactive decoding options."""
        dialog = self.dialog(self.window())
        for index in range(40):
            dialog.adv_temperature.setValue((index % 11) / 10)
            dialog.adv_beam_size.setValue(1 + index % 5)
            dialog.adv_best_of.setValue(2 + index % 5)
        loaded = ConfigManager(str(self.root / 'config.json')).get_advanced_transcription_options()
        self.assertEqual(loaded['temperature'], dialog.adv_temperature.value())
        self.assertEqual(loaded['beam_size'], dialog.adv_beam_size.value())
        self.assertEqual(loaded['best_of'], dialog.adv_best_of.value())

    def test_engine_device_language_and_model_memory(self):
        """Real selectors load fake engines and preserve engine-specific choices."""
        window = self.window()
        dialog = self.dialog(window)
        for engine, model in (('faster-whisper', 'large-v2'), ('openai-whisper', 'tiny.en')):
            dialog.engine_selector.setCurrentIndex(dialog.engine_selector.findData(engine))
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
            self.assertEqual([dialog.model_selector.itemText(i) for i in range(dialog.model_selector.count())], self.registry.get_models(engine))
            dialog.model_selector.setCurrentText(model)
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
            self.assertEqual(self.config.get_model(), model)
        self.assertEqual(self.config.get_language(), 'en')
        self.assertFalse(dialog.language_combo_box.isEnabled())
        dialog.engine_selector.setCurrentIndex(dialog.engine_selector.findData('faster-whisper'))
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(dialog.model_selector.currentText(), 'large-v2')
        for mode in ('cpu', 'gpu', 'auto'):
            dialog.device_selector.setCurrentIndex(dialog.device_selector.findData(mode))
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
            self.assertEqual(self.config.get_device_mode(), mode)
        dialog.language_combo_box.setCurrentText('Russian')
        self.assertEqual(self.config.get_language(), 'ru')

    def test_repeated_model_load_failure_restores_entire_selection(self):
        """Failed workers restore engine/model/device UI and release disabled controls."""
        window = self.window()
        dialog = self.dialog(window)
        previous = (self.config.get_engine(), self.config.get_model(), self.config.get_device_mode())
        failed = self.fake_engine('', 'cpu')
        failed.load_model.side_effect = RuntimeError('QA load failure')
        observed = []
        with patch.object(self.controller, '_create_engine', return_value=failed):
            for _ in range(3):
                dialog.engine_selector.setCurrentIndex(dialog.engine_selector.findData('faster-whisper'))
                self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
                self.assertEqual((self.config.get_engine(), self.config.get_model(), self.config.get_device_mode()), previous)
                observed.append((dialog.engine_selector.currentData(), dialog.model_selector.currentText(), dialog.device_selector.currentData()))
                self.assertTrue(dialog.engine_selector.isEnabled())
                self.assertTrue(window.primary_button.isEnabled())
                # Explicit test-only recovery permits another independent failed attempt.
                dialog.restore_transcription_selection()
        self.assertEqual(failed.load_model.call_count, 3)
        self.assertEqual(observed, [previous] * 3)

    def test_recording_controls_without_microphone(self):
        """Drive recording/pause/resume/cancel and inspect real controls and status."""
        window = self.window()
        dialog = self.dialog(window)
        self.config.set_language('ru')
        recording = self.controller.recording_controller
        recording.recorder.is_running.return_value = False
        self.assertFalse(window.timer_label.isVisible())
        idle_icon = self.icon_image(window.primary_button)
        with patch.object(recording, 'is_microphone_available', return_value=True):
            window.primary_button.click()
        self.assertEqual(recording.state, 'recording')
        self.assertNotEqual(self.icon_image(window.primary_button), idle_icon)
        self.assertTrue(window.timer_label.isVisible())
        self.assertTrue(window.cancel_button.isVisible())
        self.assertFalse(window.folder_selector.isEnabled())
        self.assertFalse(dialog.engine_selector.isEnabled())
        self.assertIn('Recording', window.status_display.text())
        pause_icon = self.icon_image(window.secondary_button)
        window.secondary_button.click()
        self.assertEqual(recording.state, 'paused')
        self.assertIn('Paused', window.status_display.text())
        self.assertNotEqual(self.icon_image(window.secondary_button), pause_icon)
        window.secondary_button.click()
        self.assertEqual(recording.state, 'recording')
        source = self.harness.recording()
        recording.recorder.output_filename = str(source)
        window.cancel_button.click()
        recording._on_recording_finished(str(source))
        self.assertEqual(recording.state, 'idle')
        self.assertFalse(window.timer_label.isVisible())
        self.assertEqual(window.status_display.text(), 'Ready')

    def test_notes_editor_autosave_search_move_reload(self):
        """Selected-folder notes survive GUI editing, filtering and repository reload."""
        window = self.window()
        folder = self.fixture.folder('Работа 📝')
        self.controller.select_folder(folder)
        path = self.fixture.notes.create_transcription_note('Initial text', 2)
        self.assertEqual(self.fixture.repo.read_note(path)['folder_id'], folder)
        window.text_area.setPlainText('Изменено 📝')
        QTest.qWait(1750)
        self.assertEqual(NotesRepository(str(self.root / 'notes')).read_note(path)['full_text'], 'Изменено 📝')
        spy = QSignalSpy(self.fixture.notes.notes_filtered)
        self.fixture.notes.filter_notes('Изменено')
        self.assertEqual(len(spy[-1][0]), 1)
        self.fixture.notes.filter_notes('absent')
        self.assertEqual(len(spy[-1][0]), 0)
        moved = self.fixture.repo.move_note(path, 'inbox')
        self.assertEqual(self.fixture.repo.read_note(moved)['full_text'], 'Изменено 📝')
        self.fixture.notes.clear_active_note()
        self.fixture.repo.delete_note(moved)
        self.assertFalse(Path(moved).exists())

    def test_folder_unicode_validation_and_delete_confirmation(self):
        """Folder names, colors and icons reload; deletion moves notes only after Yes."""
        panel = self.dialog(self.window()).notes_panel
        for name in ('Русский 📝', 'L' * 80, 'Duplicate', 'Duplicate'):
            panel._new()
            panel.name.setText(name)
            panel.color.setCurrentIndex(2)
            panel._icon_name = 'book'
            panel._save()
            self.assertEqual(panel.message.text(), 'Folder saved.')
        folders = self.fixture.repo.folders.list_folders()
        self.assertEqual(len(folders), 5)
        for name in ('', '   ', 'L' * 81):
            panel._new()
            panel.name.setText(name)
            panel._save()
            self.assertEqual(len(self.fixture.repo.folders.list_folders()), 5)
        folder = folders[-1]['id']
        panel.folders.setCurrentIndex(panel.folders.findData(folder))
        panel.name.setText('Renamed 📝')
        panel._save()
        data = next(f for f in NotesRepository(str(self.root / 'notes')).folders.list_folders() if f['id'] == folder)
        self.assertEqual((data['name'], data['color'], data['icon']), ('Renamed 📝', '#22c55e', resolve_name('book')))
        self.fixture.note('Preserve me', folder=folder)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            panel.delete.click()
        self.assertEqual(len(self.fixture.repo.load_notes(folder)), 1)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            panel.delete.click()
        self.assertEqual(len(self.fixture.repo.load_notes('inbox')), 1)
        panel.folders.setCurrentIndex(panel.folders.findData('inbox'))
        self.assertFalse(panel.delete.isEnabled())

    def test_cleanup_folder_all_and_cancel(self):
        """Manual GUI cleanup respects scope and cancellation before destructive work."""
        panel = self.dialog(self.window()).notes_panel
        folder = self.fixture.folder()
        selected = self.fixture.note('selected', folder=folder)
        other = self.fixture.note('other')
        panel.reload()
        panel.folders.setCurrentIndex(panel.folders.findData(folder))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            panel._cleanup('folder')
        self.assertTrue(Path(selected).exists())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            panel._cleanup('folder')
        self.fixture.wait_for(lambda: not Path(selected).exists())
        self.assertTrue(Path(other).exists())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            panel._cleanup('all')
        self.fixture.wait_for(lambda: not Path(other).exists())

    def server(self):
        """Create a per-test loopback server and a proxy-free real HTTP adapter."""
        server = LoopbackServer()
        self.addCleanup(server.close)
        settings = dict(self.fixture.settings(), base_url=server.url)
        self.fixture.service.provider_factory = lambda s, key: OpenAICompatibleProvider(s, key, build_opener(ProxyHandler({}), NoRedirect()))
        self.fixture.service.save(settings)
        return server, self.fixture.service.provider()

    def test_http_models_completions_unicode_text_only(self):
        """Real loopback requests contain exactly model/prompt/text, never audio."""
        server, provider = self.server()
        provider.test_connection()
        self.assertEqual(provider.process('Текст 📝', 'Пунктуация 中文'), 'Edited text.')
        self.assertEqual(server.requests[0], ('GET', '/v1/models', None))
        method, endpoint, payload = server.requests[1]
        self.assertEqual((method, endpoint), ('POST', '/v1/chat/completions'))
        self.assertEqual(set(payload), {'model', 'temperature', 'messages', 'stream'})
        self.assertEqual(payload['messages'], [{'role': 'system', 'content': 'Пунктуация 中文'}, {'role': 'user', 'content': 'Текст 📝'}])

    def test_http_status_errors_and_invalid_json(self):
        """HTTP failures and malformed responses are controlled, sanitized errors."""
        server, provider = self.server()
        for status in (401, 404, 500):
            with self.subTest(status=status):
                server.status = status
                with self.assertRaises(RuntimeError):
                    provider.test_connection()
                with self.assertRaises(RuntimeError):
                    provider.process('text', 'prompt')
        server.status, server.invalid_json = 200, True
        with self.assertRaises(RuntimeError):
            provider.test_connection()

    def test_connection_refused(self):
        """A reserved, non-listening loopback port produces a controlled failure."""
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            settings = dict(self.fixture.settings(), base_url=f'http://127.0.0.1:{reserved.getsockname()[1]}/v1')
            provider = OpenAICompatibleProvider(settings, opener=build_opener(ProxyHandler({})))
            with self.assertRaisesRegex(RuntimeError, 'failed or timed out'):
                provider.test_connection()

    def test_http_timeout_with_short_test_transport(self):
        """Exercise a real socket timeout without spending the production 60 seconds."""
        server, provider = self.server()
        server.delay = 0.3
        original = provider._opener.open
        with patch.object(provider._opener, 'open', side_effect=lambda request, timeout: original(request, timeout=0.05)) as opened:
            with self.assertRaisesRegex(RuntimeError, 'failed or timed out'):
                provider.test_connection()
        self.assertEqual(opened.call_args.kwargs['timeout'], 60)

    def test_connection_ui_is_responsive_and_recovers_repeatedly(self):
        """A heartbeat fires while a real HTTP worker runs; buttons recover on either result."""
        server, _ = self.server()
        panel = self.dialog(self.window()).post_panel
        panel.provider.setCurrentIndex(panel.provider.findData('local'))
        panel.url.setText(server.url)
        panel.model.setText('fake-model')
        server.delay = 0.2
        for status in (200, 500, 200):
            server.status = status
            ticks = []
            QTimer.singleShot(30, lambda: ticks.append(panel._testing))
            panel.test.click()
            self.assertFalse(panel.test.isEnabled())
            QTest.qWait(80)
            self.assertEqual(ticks, [True], 'GUI event loop was blocked by HTTP')
            self.fixture.wait_for(lambda: not self.controller._network_jobs)
            self.assertTrue(panel.test.isEnabled())
            self.assertIn('successful' if status == 200 else 'failed', panel.message.text())
        self.assertEqual(len(server.requests), 3)

    def test_provider_validation_and_ui_temperature(self):
        """Invalid config cannot enable Refine; valid Unicode settings persist at UI limits."""
        panel = self.dialog(self.window()).post_panel
        self.assertFalse(self.controller.set_refine_enabled(True))
        panel.provider.setCurrentIndex(panel.provider.findData('local'))
        for url, model, prompt in (('not a URL', 'm', 'p'), ('http://localhost:1/v1', '', 'p'), ('http://localhost:1/v1', 'm', '   ')):
            panel.url.setText(url)
            panel.model.setText(model)
            panel.prompt.setPlainText(prompt)
            self.assertFalse(panel._save())
            self.assertFalse(self.controller.set_refine_enabled(True))
        panel.url.setText('http://127.0.0.1:1/v1')
        panel.model.setText('fake')
        panel.prompt.setPlainText('Русский 📝 中文')
        for temperature in (panel.temperature.minimum(), panel.temperature.maximum()):
            panel.temperature.setValue(temperature)
            self.assertTrue(panel._save())
            self.assertEqual(self.controller.get_post_settings()['temperature'], temperature)
        self.assertTrue(self.controller.set_refine_enabled(True))

    def test_pipeline_sends_transcript_not_recording(self):
        """The real threaded pipeline sends only fake-engine text to the loopback server."""
        server, _ = self.server()
        window = self.window()
        self.assertTrue(self.controller.set_refine_enabled(True))
        source = self.harness.recording()
        source.write_bytes(b'PRIVATE_AUDIO_SENTINEL')
        self.harness.transcribe(source)
        payload = server.requests[-1][2]
        self.assertEqual(payload['messages'][1]['content'], 'Recorded text.')
        self.assertNotIn('PRIVATE_AUDIO_SENTINEL', json.dumps(payload))
        self.assertNotIn(str(source), json.dumps(payload))
        self.assertEqual(window.text_area.toPlainText(), 'Edited text.')

    def test_autosave_feedback_after_window_destruction(self):
        """Saved-label feedback must not dereference a label destroyed during exit."""
        window = self.window()
        self.fixture.notes.create_transcription_note('Before edit', 1)
        window.text_area.setPlainText('After edit')
        self.fixture.notes.autosave.force_save()
        self.assertIn('Saved', window.result_title.text())
        window.request_exit()
        self.fixture.wait_for(lambda: window._shutdown_complete)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.harness.windows.remove(window)
        QTest.qWait(1750)

    def test_persisted_inactive_engine_model_survives_new_settings(self):
        """Switching engines after a Settings rebuild must restore its persisted model."""
        self.config.set_model('small', 'faster-whisper')
        window = self.window()
        dialog = self.dialog(window)
        dialog.engine_selector.setCurrentIndex(dialog.engine_selector.findData('faster-whisper'))
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual((dialog.model_selector.currentText(), self.config.get_model('faster-whisper')), ('small', 'small'))

    def test_stop_to_processing_controls(self):
        """Stop finalizes a fake recording, disables processing controls and recovers."""
        window = self.window()
        recording = self.controller.recording_controller
        recording.recorder.is_running.return_value = False
        self.config.set_language('ru')
        gate = threading.Event()
        def transcribe(*_args, **_kwargs):
            """Hold the worker until UI assertions finish, with a bounded fallback."""
            gate.wait(4)
            return {'text': 'Finished', 'duration': 2}
        self.controller.engine.transcribe.side_effect = transcribe
        with patch.object(recording, 'is_microphone_available', return_value=True):
            window.primary_button.click()
        source = self.harness.recording()
        source.write_bytes(b'x' * 33000)
        window.primary_button.click()
        self.assertTrue(recording.is_stopping)
        self.assertIn('Processing', window.status_display.text())
        recording._on_recording_finished(str(source))
        try:
            self.assertTrue(self.controller.is_processing)
            for button in (window.primary_button, window.secondary_button, window.folder_selector):
                self.assertFalse(button.isEnabled())
            self.assertFalse(window.timer_label.isVisible())
        finally:
            gate.set()
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertTrue(window.primary_button.isEnabled())
        self.assertEqual(window.text_area.toPlainText(), 'Finished')

    def test_failed_english_model_preserves_language_without_reload(self):
        """Rollback restores all selectors without English leakage or a second load."""
        self.config.set_language('ru')
        window = self.window()
        dialog = self.dialog(window)
        failed = self.fake_engine('', 'cpu')
        failed.load_model.side_effect = RuntimeError('QA English model failure')
        spy = QSignalSpy(dialog.modelChangeRequested)
        with patch.object(self.controller, '_create_engine', return_value=failed):
            dialog.model_selector.setCurrentText('tiny.en')
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(len(spy), 1)
        failed.load_model.assert_called_once_with('tiny.en')
        self.assertEqual(self.config.get_language(), 'ru')
        self.assertEqual(dialog.language_combo_box.currentText(), 'Russian')
        self.assertTrue(dialog.language_combo_box.isEnabled())
        self.assertEqual(dialog.model_selector.currentText(), self.config.get_model())

    def test_feedback_timers_destroyed_with_window_without_exit(self):
        """Qt ownership also cancels feedback when no explicit shutdown hook runs."""
        window = self.window()
        self.fixture.notes.create_transcription_note('Original', 1)
        window.text_area.setPlainText('Edited')
        self.fixture.notes.autosave.force_save()
        window.copy_button.click()
        self.assertTrue(window._copy_feedback_timer.isActive())
        self.assertTrue(window._saved_feedback_timer.isActive())
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.harness.windows.remove(window)
        self.fixture.notes.autosave.clear_active_note()
        QTest.qWait(2300)

    def test_folder_create_delete_stress(self):
        """A bounded series of folder mutations leaves only the permanent Inbox."""
        window = self.window()
        panel = self.dialog(window).notes_panel
        for index in range(8):
            panel._new()
            panel.name.setText(f'Temporary {index}')
            panel._save()
            self.assertEqual(len(self.fixture.repo.folders.list_folders()), 2)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
                panel.delete.click()
            self.assertEqual(len(self.fixture.repo.folders.list_folders()), 1)

    def test_manual_age_cleanup_across_folders(self):
        """Age cleanup removes only old temporary notes after explicit acceptance."""
        panel = self.dialog(self.window()).notes_panel
        folder = self.fixture.folder()
        old = self.fixture.note('old', folder=folder, timestamp=int(time.time()) - 40 * 86400)
        recent = self.fixture.note('recent')
        panel.days.setValue(30)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            panel._cleanup('days')
        self.assertTrue(Path(old).exists())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            panel._cleanup('days')
        self.fixture.wait_for(lambda: not Path(old).exists())
        self.assertTrue(Path(recent).exists())

    @unittest.skipUnless(os.environ.get('FEINNOTE_QA_TURBO_SMOKE') == '1', 'Optional local Turbo smoke: opt in separately with FEINNOTE_QA_TURBO_SMOKE=1')
    def test_optional_local_turbo_smoke(self):
        """Load one existing local Turbo snapshot and transcribe temporary silence offline."""
        from core.transcription.engines.faster_whisper import FasterWhisperEngine
        from core.transcription.base_engine import TranscriptionOptions
        project = Path(__file__).resolve().parents[1]
        roots = [project / 'models/faster-whisper', project / 'dist/FeinNote/models/faster-whisper']
        candidates = [p for root in roots for p in root.glob('models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/*')
                      if all((p / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'))]
        if not candidates:
            self.skipTest('No complete project-local Faster Whisper Turbo snapshot')
        snapshot = candidates[0]
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in snapshot.iterdir() if p.is_file()}
        audio = self.root / 'silence.wav'
        with wave.open(str(audio), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b'\0\0' * 16000)
        with patch.dict(os.environ, {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}):
            engine = FasterWhisperEngine(models_dir=str(snapshot.parent), device='cuda' if fixtures.torch.cuda.is_available() else 'cpu')
            engine.load_model(str(snapshot))
            result = engine.transcribe(str(audio), language='ru', options=TranscriptionOptions())
        self.assertIsInstance(result['text'], str)
        self.assertAlmostEqual(result['duration'], 1, places=2)
        self.assertEqual(before, {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in snapshot.iterdir() if p.is_file()})


if __name__ == '__main__':
    unittest.main()
