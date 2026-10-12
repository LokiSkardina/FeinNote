# Responsible for: offline regression coverage of refinement, folders and retention.

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Match main.py's DLL initialization order before importing Qt widgets.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import torch
from PyQt6.QtWidgets import QApplication, QWidget

from core.config_manager import ConfigManager
from core.notes.notes_repository import NotesRepository
from core.notes.cleanup_service import CleanupService
from core.postprocessing.service import PostProcessingService, DEFAULT_PROMPT
from core.postprocessing.providers.openai_compatible import OpenAICompatibleProvider
from controllers.notes_controller import NotesController
from controllers.app_controller import AppController


class FeatureTests(unittest.TestCase):
    """Exercise storage and controller paths using temporary project-local fixtures."""

    @classmethod
    def setUpClass(cls):
        """Create an offscreen Qt application once for signal and widget tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Isolate files and replace credential access and all network providers."""
        self.temp = tempfile.TemporaryDirectory(prefix="test_feinnote_", dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / "config.json"))
        self.repo = NotesRepository(str(self.root / "notes"))
        self.notes = NotesController(self.repo)
        self.keys = {}
        self.credentials = Mock()
        self.credentials.get.side_effect = lambda kind: self.keys.get(kind, "")
        self.credentials.set.side_effect = lambda kind, key: self.keys.__setitem__(kind, key)
        self.service = PostProcessingService(self.config, self.credentials)

    def tearDown(self):
        """Finish Qt workers before removing this test's own fixtures."""
        self.wait_for(lambda: not self.notes._cleanup_jobs)
        self.notes._cleanup_timer.stop()
        self.temp.cleanup()

    def wait_for(self, predicate):
        """Pump Qt events until a bounded asynchronous operation completes."""
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate())

    def folder(self, name="Work", rule=None):
        """Create one folder for test setup and return its stable ID."""
        return self.repo.folders.save_folder(name, "#3b82f6", "work", rule or {"mode": "disabled", "value": 30})

    def note(self, text="source", folder="inbox", timestamp=None):
        """Persist a test note and return its path."""
        return self.repo.create_note(text, "metadata", int(time.time()) if timestamp is None else timestamp,
                                     folder_id=folder)[1]

    def settings(self, kind="local"):
        """Return a complete active provider configuration without secrets."""
        return dict(provider=kind, base_url="http://localhost:1234/v1" if kind == "local" else "https://example.invalid/v1",
                    model="test-model", temperature=0.2, prompt=DEFAULT_PROMPT)

    def controller(self):
        """Create the application controller with fake engine and recorder signals."""
        from controllers.recording_controller import RecordingController
        recorder = Mock()
        recording = RecordingController(recorder)
        engine = Mock()
        engine.is_model_loaded.return_value = True
        controller = AppController(config_manager=self.config, notes_controller=self.notes,
                                   recording_controller=recording, engine=engine)
        controller.post_processing = self.service
        return controller

    def test_inbox_legacy_and_raw_preserved(self):
        """Old flat JSON files remain visible and retain original text on edit."""
        path = self.root / "notes" / "legacy.json"
        path.write_text(json.dumps(dict(full_text="old", timestamp=100, metadata="old")), encoding="utf-8")
        self.assertEqual(self.repo.load_notes("inbox")[0][0]["raw_text"], "old")
        self.repo.update_note_text(str(path), "edited")
        self.assertEqual(self.repo.read_note(str(path))["raw_text"], "old")
        reopened = NotesRepository(self.repo.notes_dir)
        self.assertEqual(len(reopened.load_notes("inbox")), 1)
        with self.assertRaises(ValueError):
            reopened.folders.remove_empty_folder("inbox")

    def test_folder_create_rename_move_delete(self):
        """Folder metadata persists and deleting a folder preserves its notes."""
        fid = self.folder()
        path = self.note(folder=fid)
        self.repo.folders.save_folder("Learning", "#22c55e", "book", {"mode": "days", "value": 90}, fid)
        self.assertEqual(len(self.repo.load_notes("inbox")), 0)
        self.assertEqual(len(self.repo.load_notes(fid)), 1)
        moved = self.repo.move_note(path, "inbox")
        self.assertEqual(self.repo.read_note(moved)["folder_id"], "inbox")
        self.repo.move_note(moved, fid)
        self.notes.select_folder(fid)
        self.notes.delete_folder(fid)
        self.assertEqual(self.notes.active_folder, "inbox")
        self.assertEqual(len(self.repo.load_notes("inbox")), 1)
        self.assertEqual(len(self.repo.folders.list_folders()), 1)

    def test_retention_boundaries_and_scope(self):
        """Count and age rules select only eligible notes from one folder."""
        fid = self.folder()
        now = 2_000_000
        paths = [self.note(folder=fid, timestamp=now - i * 86400) for i in range(5)]
        other = self.note(timestamp=100)
        notes = self.repo.load_notes(fid)
        self.assertEqual(set(CleanupService.candidates(notes, {"mode": "count", "value": 2})), set(paths[2:]))
        self.assertEqual(set(CleanupService.candidates(notes, {"mode": "days", "value": 2}, now)), set(paths[3:]))
        self.assertEqual(CleanupService.candidates(notes, {"mode": "disabled"}), [])
        for path in CleanupService.candidates(notes, {"mode": "count", "value": 1}):
            self.repo.delete_note(path)
        self.assertTrue(Path(other).exists())
        with self.assertRaises(ValueError):
            CleanupService.candidates(notes, {"mode": "count", "value": 0})

    def test_auto_cleanup_and_editor_clear(self):
        """Automatic cleanup and confirmed deletion refresh controller state."""
        fid = self.folder(rule={"mode": "count", "value": 1})
        old = self.note(folder=fid, timestamp=100)
        self.note(folder=fid, timestamp=200)
        self.notes.select_folder(fid)
        self.notes.select_note(old)
        self.notes.schedule_cleanup()
        self.wait_for(lambda: not Path(old).exists())
        self.assertIsNone(self.notes.active_filepath)
        self.assertIsNone(self.notes.autosave.current_filepath)

    def test_settings_and_credentials(self):
        """Provider config reloads without writing any API key to JSON."""
        self.assertIsNone(self.service.provider())
        self.service.save(self.settings())
        self.assertTrue(self.service.configured())
        self.service.save(self.settings("cloud"), "secret-test-key")
        self.assertNotIn("secret-test-key", (self.root / "config.json").read_text())
        loaded = PostProcessingService(ConfigManager(str(self.root / "config.json")), self.credentials)
        self.assertEqual(loaded.settings()["model"], "test-model")
        self.assertTrue(loaded.configured())
        self.service.save(dict(provider="disabled"))
        self.assertFalse(self.service.configured())

    def test_refine_unconfigured_rejected(self):
        """Refine cannot be enabled until a provider is usable."""
        controller = self.controller()
        self.assertFalse(controller.set_refine_enabled(True))
        self.assertFalse(controller.refine_enabled())

    def test_disabled_pipeline_and_selection_reload(self):
        """Disabled refinement saves raw text in the persisted destination folder."""
        controller = self.controller()
        folder_id = self.folder()
        controller.select_folder(folder_id)
        controller._pipeline_folder = folder_id
        controller._on_transcription_result({"text": "raw only", "duration": 1})
        data = self.repo.load_notes(folder_id)[0][0]
        self.assertEqual(data["raw_text"], data["full_text"])
        self.assertFalse(controller._network_jobs)
        loaded = ConfigManager(str(self.root / "config.json"))
        self.assertEqual(loaded.get_section("notes")["active_folder"], folder_id)
        self.notes.select_folder("missing-folder")
        self.assertEqual(self.notes.active_folder, "inbox")

    def test_manual_cleanup_requires_confirmation(self):
        """Cancelling deletion leaves notes intact; acceptance clears the editor."""
        from widgets.settings_panel import NotesPanel
        from PyQt6.QtWidgets import QMessageBox
        controller = self.controller()
        path = self.note()
        self.notes.select_note(path)
        panel = NotesPanel(controller)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            panel._cleanup("all")
        self.assertTrue(Path(path).exists())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as confirm:
            panel._cleanup("all")
        self.assertIn("1 notes", confirm.call_args.args[2])
        self.wait_for(lambda: not Path(path).exists())
        self.assertIsNone(self.notes.active_filepath)

    def test_provider_validation_and_http_errors(self):
        """Reject insecure cloud URLs and never echo an HTTP response or secret."""
        from urllib.error import HTTPError
        invalid = self.settings("cloud")
        invalid["base_url"] = "http://example.invalid/v1"
        with self.assertRaises(ValueError):
            self.service.save(invalid, "secret-test-key")
        with self.assertRaises(ValueError):
            self.service.save(self.settings("cloud"))
        opener = Mock()
        opener.open.side_effect = HTTPError("https://example.invalid", 401, "secret-test-key", {}, None)
        provider = OpenAICompatibleProvider(self.settings("cloud"), "secret-test-key", opener)
        with self.assertRaises(RuntimeError) as error:
            provider.test_connection()
        self.assertNotIn("secret-test-key", str(error.exception))
        self.assertIsNone(error.exception.__context__)

    def test_provider_text_only_and_sanitized_errors(self):
        """Requests contain only text; arbitrary transport errors cannot leak secrets."""
        opener = Mock()
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "Edited."}}]}).encode())
        opener.open.return_value = response
        provider = OpenAICompatibleProvider(self.settings(), "secret-test-key", opener)
        self.assertEqual(provider.process("raw words", "Edit only"), "Edited.")
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["messages"][1]["content"], "raw words")
        self.assertNotIn("audio", payload)
        opener.open.side_effect = RuntimeError("secret-test-key")
        with self.assertRaises(RuntimeError) as captured:
            provider.process("raw", "edit")
        self.assertNotIn("secret-test-key", str(captured.exception))
        self.assertIsNone(captured.exception.__context__)

    def test_refinement_success_and_failure_keep_raw(self):
        """Both worker outcomes retain the original transcription on disk."""
        controller = self.controller()
        provider = Mock()
        provider.settings = self.settings()
        for fails in (False, True):
            provider.process.side_effect = RuntimeError("secret-test-key") if fails else None
            provider.process.return_value = "Edited text."
            controller._pipeline_provider = provider
            controller.is_processing = True
            import logging
            output = io.StringIO()
            handler = logging.StreamHandler(output)
            logger = logging.getLogger("WhisperNote")
            logger.addHandler(handler)
            try:
                controller._on_transcription_result({"text": "raw text", "duration": 4, "segments": []})
                controller._on_transcription_finished()
                self.wait_for(lambda: not controller._network_jobs)
            finally:
                logger.removeHandler(handler)
            self.assertNotIn("secret-test-key", output.getvalue())
            data = self.repo.read_note(controller._raw_note_path)
            self.assertEqual(data["raw_text"], "raw text")
            self.assertEqual(data["full_text"], "raw text" if fails else "Edited text.")
            self.assertFalse(controller.is_processing)

    def test_connection_test_and_settings_ui(self):
        """Settings widgets construct and connection tests use only the fake provider."""
        from widgets.settings_dialog import SettingsDialog
        controller = self.controller()
        self.service.save(self.settings())
        provider = Mock()
        self.service.provider_factory = Mock(return_value=provider)
        received = []
        controller.connection_tested.connect(received.append)
        controller.test_post_connection()
        self.wait_for(lambda: not controller._network_jobs)
        self.assertTrue(received)
        provider.test_connection.assert_called_once()
        provider.process.assert_not_called()
        from utils.themes import dark_theme
        parent = QWidget()
        parent.app_controller = controller
        dialog = SettingsDialog(parent, theme=dark_theme)
        dialog.set_controls_enabled(True)
        dialog.notes_panel._new()
        dialog.notes_panel.name.setText("UI folder")
        dialog.notes_panel._save()
        self.assertEqual(len(self.repo.folders.list_folders()), 2, dialog.notes_panel.message.text())
        dialog.close()
        dialog.deleteLater()
        parent.deleteLater()


    def test_main_window_smoke(self):
        """Launch the real View against isolated storage and a fake loaded engine."""
        import app_window
        controller = self.controller()
        controller.load_selected_model = lambda: True
        self.note("Visible source")
        with patch.object(app_window, "AppController", return_value=controller):
            window = app_window.WhisperNoteApp()
        window.show()
        self.app.processEvents()
        self.assertEqual(len(window._cards_by_filepath), 1)
        fid = self.folder()
        window._select_folder(fid)
        self.assertEqual(len(window._cards_by_filepath), 0)
        self.notes.create_transcription_note("<plain text>", 2)
        self.assertEqual(window.text_area.toPlainText(), "<plain text>")
        window._select_folder("inbox")
        self.assertEqual(len(window._cards_by_filepath), 1)
        with patch.object(window, "show_settings_dialog") as open_settings:
            window._toggle_refine(True)
            open_settings.assert_called_once()
        self.assertFalse(window.post_process_toggle.toggle.isChecked())
        window.close()
        self.wait_for(lambda: window._shutdown_complete)
        self.app.processEvents()

    def test_transcription_adapter_regression(self):
        """Both unchanged engine adapters still pass language and decode options."""
        from types import SimpleNamespace
        from core.transcription.base_engine import TranscriptionOptions
        from core.transcription.engines.openai_whisper import OpenAIWhisperEngine
        from core.transcription.engines.faster_whisper import FasterWhisperEngine
        source = self.root / "audio.wav"
        source.write_bytes(b"fake audio; decoding is mocked")
        options = TranscriptionOptions(temperature=0.0, beam_size=4, initial_prompt="Vocabulary")
        openai = OpenAIWhisperEngine.__new__(OpenAIWhisperEngine)
        openai.model = Mock()
        openai.current_model_name = "tiny"
        openai.model.transcribe.return_value = {"text": "hello", "segments": []}
        with patch.object(openai, "_process_audio_before_transcription", return_value=(str(source), 2.0)):
            self.assertEqual(openai.transcribe(str(source), "en", options)["text"], "hello")
        self.assertEqual(openai.model.transcribe.call_args.kwargs["beam_size"], 4)
        self.assertEqual(openai.model.transcribe.call_args.kwargs["language"], "en")
        faster = FasterWhisperEngine.__new__(FasterWhisperEngine)
        faster.model, faster.device = Mock(), "cpu"
        faster.model.transcribe.return_value = (iter([SimpleNamespace(start=0, end=2, text=" hello")]), SimpleNamespace(duration=2))
        self.assertEqual(faster.transcribe(str(source), "en", options)["text"], "hello")
        self.assertEqual(faster.model.transcribe.call_args.kwargs["beam_size"], 4)
        self.assertEqual(faster.model.transcribe.call_args.kwargs["initial_prompt"], "Vocabulary")


if __name__ == "__main__":
    unittest.main()
