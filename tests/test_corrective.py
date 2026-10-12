# Responsible for: focused regressions from the corrective static-audit pass.

import unittest
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from tests import test_application as application
from PyQt6.QtTest import QTest
from core.config_manager import ConfigManager
from core.recording_storage import RecordingStorage
from core.transcription.engines.openai_whisper import OpenAIWhisperEngine
from utils.themes import light_theme
from widgets.icon_picker import IconPicker


class CorrectiveTests(unittest.TestCase):
    """Exercise the specified failures against isolated files and fake devices."""

    @classmethod
    def setUpClass(cls):
        """Reuse the offscreen Qt application established by the existing suite."""
        application.ApplicationTests.setUpClass()

    def setUp(self):
        """Create only project-local temporary configuration and notes."""
        self.harness = application.ApplicationTests()
        self.harness.setUp()
        self.fixture = self.harness.fixture
        self.root = self.harness.root
        self.config = self.harness.config

    def tearDown(self):
        """Finish GUI work before removing this test's temporary data."""
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()

    def test_icon_picker_unknown_name_falls_back_without_changing_folder(self):
        """Unknown saved names permit a new choice without rewriting metadata."""
        folder_id = self.fixture.folder("Unknown icon")
        folder = self.fixture.repo.folders._folders[folder_id]
        folder["icon"] = "icon-that-does-not-exist"
        self.fixture.repo.folders._save()
        before = (self.root / "notes" / "folders.json").read_bytes()
        picker = IconPicker(folder["icon"], "#3b82f6", light_theme)
        self.assertEqual(picker.selected_name(), "folder")
        self.assertEqual((self.root / "notes" / "folders.json").read_bytes(), before)
        picker.search.setText("music")
        self.assertIn("music", picker.model.names)
        picker.grid.setCurrentIndex(picker.model.index(picker.model.names.index("music")))
        self.assertEqual(picker.selected_name(), "music")
        picker.reject()

    def test_openai_model_load_uses_resolved_cpu_and_cuda(self):
        """The controller's resolved device reaches both Whisper load calls."""
        for device in ("cpu", "cuda"):
            with self.subTest(device=device):
                with patch.object(OpenAIWhisperEngine, "_configure_ffmpeg"), patch("core.transcription.engines.openai_whisper.whisper.load_model", return_value=Mock(spec=[])) as load:
                    engine = self.harness.controller._create_engine("openai-whisper", device)
                    engine.load_model("tiny")
                self.assertEqual(load.call_args.kwargs["device"], device)

    def test_prompt_typing_batches_saves_and_close_flushes(self):
        """Rapid key input writes once, and closing flushes the latest prompt."""
        window = self.harness.window()
        from widgets.settings_dialog import SettingsDialog
        dialog = SettingsDialog(window, theme=window.theme)
        dialog.show()
        dialog.adv_prompt.setFocus()
        with patch.object(self.config, "_save", wraps=self.config._save) as disk_save:
            QTest.keyClicks(dialog.adv_prompt, "Rapid typing for a prompt")
            self.assertLessEqual(disk_save.call_count, 1)
            self.harness.fixture.wait_for(lambda: disk_save.call_count >= 1)
            self.assertEqual(disk_save.call_count, 1)
            QTest.keyClicks(dialog.adv_prompt, " final")
            dialog.reject()
            self.assertEqual(disk_save.call_count, 2)
        loaded = ConfigManager(str(self.root / "config.json"))
        self.assertEqual(loaded.get_advanced_transcription_options()["initial_prompt"], "Rapid typing for a prompt final")

    def test_advanced_options_getter_returns_independent_dictionary(self):
        """Mutating a returned dictionary cannot modify in-memory or disk state."""
        options = self.config.get_advanced_transcription_options()
        original = options["temperature"]
        options["temperature"] = 0.7
        self.assertEqual(self.config.get_advanced_transcription_options()["temperature"], original)
        self.config.set_advanced_transcription_options(options)
        self.assertEqual(self.config.get_advanced_transcription_options()["temperature"], 0.7)
        self.assertEqual(ConfigManager(str(self.root / "config.json")).get_advanced_transcription_options()["temperature"], 0.7)

    def test_empty_recording_removes_owned_temporary_reservation(self):
        """Zero captured frames leave no reserved WAV after finalization."""
        from core.recorder import AudioRecorder
        with patch("core.recorder.pyaudio.PyAudio"):
            recorder = AudioRecorder()
        recorder.storage = RecordingStorage(self.root)
        reserved = Path(recorder.storage.create_temporary())
        recorder.output_filename = str(reserved)
        self.assertTrue(reserved.exists())
        with self.assertLogs("WhisperNote.recorder", level="WARNING") as messages:
            self.assertIsNone(recorder._save_to_file_safely("qa-zero-frames"))
        self.assertTrue(any("No frames to save" in line for line in messages.output))
        self.assertFalse(reserved.exists())
        retained = self.root / "recordings" / "inbox" / "kept.wav"
        retained.parent.mkdir(parents=True)
        retained.write_bytes(b"retained audio")
        with patch("core.recorder.pyaudio.PyAudio"):
            fixed_recorder = AudioRecorder(str(retained))
        fixed_recorder.storage = RecordingStorage(self.root)
        with self.assertLogs("WhisperNote.recorder", level="WARNING"):
            fixed_recorder._save_to_file_safely("qa-fixed-path")
        self.assertEqual(retained.read_bytes(), b"retained audio")

    def test_failed_stream_open_removes_empty_temporary_reservation(self):
        """An aborted capture without frames also discards only its own WAV."""
        from core.recorder import AudioRecorder
        with patch("core.recorder.pyaudio.PyAudio"):
            recorder = AudioRecorder()
        recorder.storage = RecordingStorage(self.root)
        reserved = Path(recorder.storage.create_temporary())
        recorder.output_filename = str(reserved)
        recorder._is_recording = True
        with self.assertLogs("WhisperNote.recorder", level="ERROR") as messages:
            with patch("core.recorder.pyaudio.PyAudio") as audio:
                audio.return_value.open.side_effect = OSError("fake input device failure")
                recorder._record_loop()
        self.assertTrue(any("Fatal error opening audio stream" in line for line in messages.output))
        self.assertFalse(reserved.exists())

    def test_connection_dispatch_exception_restores_test_button(self):
        """An unexpected synchronous failure leaves the panel usable and sanitized."""
        from widgets.settings_panel import PostProcessingPanel
        panel = PostProcessingPanel(self.harness.controller)
        panel.provider.setCurrentIndex(panel.provider.findData("local"))
        panel.model.setText("fake-model")
        panel.prompt.setPlainText("Unicode 📝")
        with patch.object(self.harness.controller, "test_post_connection", side_effect=LookupError("PRIVATE INTERNAL ERROR")):
            panel._test()
        self.assertFalse(panel._testing)
        self.assertTrue(panel.test.isEnabled())
        self.assertIn("failed", panel.message.text().lower())
        self.assertNotIn("PRIVATE INTERNAL ERROR", panel.message.text())

    def test_audio_normalization_never_overwrites_neighbor_files(self):
        """Imported audio and an existing processed-name neighbor remain untouched."""
        from pydub import AudioSegment
        source = self.root / "speech.wav"
        neighbor = self.root / "speech_processed.wav"
        AudioSegment.silent(duration=120, frame_rate=22050).export(str(source), format="wav").close()
        neighbor.write_bytes(b"user-owned audio")
        original = source.read_bytes()
        with patch("core.transcription.engines.openai_whisper.get_app_root", return_value=str(self.root)), patch.object(OpenAIWhisperEngine, "_configure_ffmpeg"):
            engine = OpenAIWhisperEngine(str(self.root / "models"), "cpu")
            engine.model = Mock()
            generated = []

            def decode(path, **kwargs):
                generated.append(Path(path))
                self.assertEqual(Path(path).parent, self.root / "recordings" / ".temporary")
                self.assertTrue(Path(path).is_file())
                return {"text": "hello", "segments": []}

            engine.model.transcribe.side_effect = decode
            engine.transcribe(str(source), "en")
            engine.transcribe(str(source), "en")
            self.assertNotEqual(*generated)
            self.assertTrue(all(not path.exists() for path in generated))
            engine.model.transcribe.side_effect = RuntimeError("decoder failure")
            with self.assertRaises(RuntimeError):
                engine.transcribe(str(source), "en")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(neighbor.read_bytes(), b"user-owned audio")
        self.assertEqual(list((self.root / "recordings" / ".temporary").iterdir()), [])

    def test_failed_normalization_removes_only_its_reservation(self):
        """A conversion failure falls back to input without leaking partial WAV files."""
        sound = Mock(frame_rate=44100, channels=2, duration_seconds=2)
        sound.set_frame_rate.return_value.set_channels.return_value.export.side_effect = OSError("disk full")
        source = str(self.root / "input.mp3")
        Path(source).write_bytes(b"mocked input")
        with patch("core.transcription.engines.openai_whisper.get_app_root", return_value=str(self.root)), patch("core.transcription.engines.openai_whisper.AudioSegment.from_file", return_value=sound):
            self.assertEqual(OpenAIWhisperEngine._process_audio_before_transcription(source), (source, 0.0))
        self.assertEqual(list((self.root / "recordings" / ".temporary").iterdir()), [])

    def test_malformed_advanced_options_are_safe_without_rewriting_config(self):
        """Bad types, nonfinite numbers and unknown keys cannot break Qt or decoding."""
        from core.transcription.base_engine import TranscriptionOptions
        bad = dict(temperature="hot", beam_size=None, best_of=True, patience=float("nan"),
                   condition_on_previous_text="false", initial_prompt=[], no_speech_threshold=2,
                   log_prob_threshold=float("inf"), compression_ratio_threshold=-1, unknown=True)
        self.config.set_section("advanced_transcription", bad)
        before = (self.root / "config.json").read_bytes()
        options = self.config.get_advanced_transcription_options()
        self.assertEqual(options, ConfigManager.DEFAULT_ADVANCED)
        TranscriptionOptions(**options)
        from widgets.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self.harness.window(), theme=light_theme)
        self.assertEqual(dialog.adv_beam_size.value(), 5)
        self.assertEqual((self.root / "config.json").read_bytes(), before)
        dialog.close()

    def test_valid_advanced_options_and_disabled_thresholds_survive(self):
        """Safe existing decoding choices are not reset by input normalization."""
        values = dict(ConfigManager.DEFAULT_ADVANCED, temperature=0.7, initial_prompt="Names",
                      no_speech_threshold=None, compression_ratio_threshold=None, log_prob_threshold=None)
        self.config.set_advanced_transcription_options(values)
        self.assertEqual(self.config.get_advanced_transcription_options(), values)

    def test_config_setters_roll_back_after_failed_write(self):
        """A later save must not accidentally commit any previously rejected changes."""
        before = deepcopy(self.config._config)
        operations = [lambda: self.config.set_engine("faster-whisper"),
                      lambda: self.config.set_model("small"),
                      lambda: self.config.set_device_mode("gpu"),
                      lambda: self.config.set_theme("dark"),
                      lambda: self.config.set_language("de"),
                      lambda: self.config.set_transcription_selection("faster-whisper", "turbo", "gpu"),
                      lambda: self.config.set_advanced_transcription_options({"temperature": 0.9})]
        for operation in operations:
            with patch.object(self.config, "_save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    operation()
            self.assertEqual(self.config._config, before)

    def test_advanced_save_failure_does_not_escape_qt_slot(self):
        """Failed persistence reports once, keeps the draft and preserves saved settings."""
        from widgets.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self.harness.window(), theme=light_theme)
        before = self.config.get_advanced_transcription_options()
        with patch.object(self.config, "_save", side_effect=OSError("disk full")), patch("PyQt6.QtWidgets.QMessageBox.warning") as warning:
            dialog.adv_temperature.setValue(0.5)
        warning.assert_called_once()
        self.assertEqual(self.config.get_advanced_transcription_options(), before)
        self.assertEqual(dialog.adv_temperature.value(), 0.5)
        dialog.close()

    def test_false_model_load_does_not_promote_candidate(self):
        """A False engine result follows failure recovery rather than model_ready."""
        from PyQt6.QtTest import QSignalSpy
        controller = self.harness.controller
        original = controller.engine
        candidate = Mock()
        candidate.load_model.return_value = False
        failures = QSignalSpy(controller.model_load_failed)
        ready = QSignalSpy(controller.model_ready)
        with patch.object(controller, "_create_engine", return_value=candidate):
            self.assertTrue(controller._start_model_load("openai-whisper", "base", "cpu", persist_selection=True))
            self.fixture.wait_for(lambda: not controller.has_active_jobs())
        self.assertIs(controller.engine, original)
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(ready), 0)
        self.assertNotEqual(self.config.get_model(), "base")

    def test_invalid_note_field_types_are_skipped_without_modification(self):
        """One malformed note cannot crash history-card construction for the others."""
        good = self.fixture.note("valid")
        for field in ("full_text", "metadata"):
            for value in (None, 42, {}, []):
                data = dict(full_text="text", metadata="date", timestamp=1)
                data[field] = value
                path = self.root / "notes" / "bad.json"
                encoded = json.dumps(data)
                path.write_text(encoded, encoding="utf-8")
                notes = self.fixture.repo.load_notes()
                self.assertEqual(len(notes), 1)
                self.assertEqual(notes[0][1], good)
                self.assertEqual(path.read_text(encoding="utf-8"), encoded)

    def test_nonfinite_duration_does_not_break_note_creation(self):
        """Malformed duration metadata falls back safely instead of raising in a Qt slot."""
        from controllers.notes_controller import NotesController
        from core.transcription.engines.faster_whisper import FasterWhisperEngine
        for value in (float("inf"), float("nan"), 10 ** 1000, True):
            self.assertEqual(NotesController._format_duration(value), "...")
        for value in (float("inf"), float("nan"), 10 ** 1000):
            self.assertEqual(FasterWhisperEngine._normalize_duration(value), 0.0)
        path = self.fixture.notes.create_transcription_note("usable text", float("inf"))
        self.assertEqual(self.fixture.repo.read_note(path)["full_text"], "usable text")


if __name__ == "__main__":
    unittest.main()
