# Responsible for: memory policy, engine lifecycle and serialized pipeline regressions.

import json
import threading
import unittest
import weakref
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests import test_application as application
from PyQt6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QMessageBox

from core.config_manager import ConfigManager
from core.transcription.engines.faster_whisper import FasterWhisperEngine
from core.transcription.engines.openai_whisper import OpenAIWhisperEngine
from core.worker import Worker
from widgets.settings_dialog import SettingsDialog


class FakeMemoryEngine:
    """Expose model state and deterministic phase events without GPU or downloads."""

    def __init__(self, events):
        """Start loaded; optional gates make worker lifetime deterministic in tests."""
        self.events = events
        self.model = object()
        self.device = "cpu"
        self.current_model_name = "tiny"
        self.unload_gate = None
        self.load_gate = None
        self.unload_error = False
        self.load_error = False
        self.transcribe_error = False
        self.text = "Original text."
        self.segments = [{"start": 1.25, "end": 2.75, "text": "Original text."}]
        self.on_unload = None

    def is_model_loaded(self, name):
        """Report actual residency without changing the selected model."""
        return self.model is not None and name == self.current_model_name

    def load_model(self, name):
        """Record a reload on the calling worker and optionally block/fail it."""
        self.events.append("load")
        if self.load_gate:
            self.load_gate.wait(5)
        if self.load_error:
            raise RuntimeError("Test reload failure")
        self.model = object()
        self.current_model_name = name
        return True

    def transcribe(self, *args):
        """Return deterministic real-shaped segment data from a resident model."""
        assert self.model is not None
        self.events.append("transcribe")
        if self.transcribe_error:
            raise RuntimeError("Test transcription failure")
        return {"text": self.text, "segments": self.segments, "duration": 3}

    def unload_model(self):
        """Record release separately from completion and simulate work/failure."""
        self.events.append("unload-start")
        if self.on_unload:
            self.on_unload()
        if self.unload_gate:
            self.unload_gate.wait(5)
        if self.unload_error:
            raise RuntimeError("Test unload failure")
        self.model = None
        self.events.append("unload-end")


class ModelMemoryTests(unittest.TestCase):
    """Exercise production Qt threads and engine adapters against isolated local fixtures."""

    @classmethod
    def setUpClass(cls):
        """Reuse the suite's offscreen Qt application."""
        application.ApplicationTests.setUpClass()

    def setUp(self):
        """Create owned audio/config/notes and a fake engine/provider."""
        self.harness = application.ApplicationTests()
        self.harness.setUp()
        self.fixture = self.harness.fixture
        self.controller = self.harness.controller
        self.config = self.harness.config
        self.config.set_language("ru")
        self.config.set_device_mode("cpu")
        self.events = []
        self.engine = FakeMemoryEngine(self.events)
        self.controller.engine = self.engine
        self.source = self.harness.root / "input.wav"
        self.source.write_bytes(b"Mocked audio fixture")
        self.gates = []
        self.dialogs = []
        self.errors = QSignalSpy(self.controller.model_memory_failed)
        self.controller.processing_finished.connect(lambda: self.events.append("ready"))
        self.provider = Mock()
        self.provider.process.side_effect = self.refine
        self.controller.post_processing.provider = Mock(return_value=self.provider)
        self.controller.refine_enabled = Mock(return_value=False)

    def tearDown(self):
        """Unblock workers before disposing windows and test-owned data."""
        for gate in self.gates:
            gate.set()
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        for dialog in self.dialogs:
            dialog.close()
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()

    def refine(self, text, prompt):
        """Record refinement and return different text without touching segments."""
        self.events.append("refine")
        return "Refined text."

    def gate(self):
        """Create a bounded worker gate that teardown always releases."""
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def dialog(self, window=None):
        """Open a real Settings page backed by the isolated controller."""
        dialog = SettingsDialog(window or self.harness.window(), theme=self.harness.windows[-1].theme)
        self.dialogs.append(dialog)
        dialog.show()
        dialog.segmented.setCurrentIndex(3)
        self.fixture.app.processEvents()
        return dialog

    def run_pipeline(self, mode, refine=False):
        """Run one real pipeline and return its saved note after all workers stop."""
        self.controller.set_model_memory_mode(mode)
        self.controller.refine_enabled.return_value = refine
        self.assertTrue(self.controller.start_file_transcription(str(self.source)))
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        return self.fixture.repo.read_note(self.controller._raw_note_path)

    def test_default_legacy_and_invalid_config(self):
        """Legacy and malformed values fall back safely without an eager config write."""
        self.assertEqual(self.config.get_model_memory_mode(), "keep_loaded")
        for value in (None, "unknown", True, [], {}):
            path = self.harness.root / "legacy.json"
            path.write_text(json.dumps({"model_memory_mode": value}), encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(ConfigManager(str(path)).get_model_memory_mode(), "keep_loaded")
            self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("model_memory_mode", self.config._config)

    def test_all_modes_persist_without_changing_selection(self):
        """A single enum persists independently of transcription/application settings."""
        original = dict(self.config._config)
        for mode in ConfigManager.MODEL_MEMORY_MODES:
            self.controller.set_model_memory_mode(mode)
            saved = ConfigManager(str(self.harness.root / "config.json"))
            self.assertEqual(saved.get_model_memory_mode(), mode)
            self.assertEqual({k: v for k, v in saved._config.items() if k != "model_memory_mode"}, original)
        with self.assertRaises(ValueError):
            self.config.set_model_memory_mode("bad")

    def test_config_failure_rolls_back(self):
        """A failed enum save preserves the previously active policy."""
        self.config.set_model_memory_mode("release_after_transcription")
        with patch.object(self.config, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.config.set_model_memory_mode("keep_loaded")
        self.assertEqual(self.config.get_model_memory_mode(), "release_after_transcription")

    def test_exclusive_ui_reopen_and_delayed_tooltips(self):
        """Exactly one clickable choice remains checked and the existing help system is reused."""
        dialog = self.dialog()
        for mode, button in dialog.memory_buttons.items():
            button.click()
            button.click()
            self.assertTrue(button.isChecked())
            self.assertEqual(sum(b.isChecked() for b in dialog.memory_buttons.values()), 1)
            self.assertTrue(all(b.isEnabled() for b in dialog.memory_buttons.values()))
            self.assertTrue(button.toolTip())
            self.assertEqual(self.config.get_model_memory_mode(), mode)
        self.assertTrue(dialog.unload_model_button.toolTip())
        self.assertEqual(dialog.interactions.timer.interval(), 2000)
        dialog.close()
        reopened = self.dialog(self.harness.windows[0])
        self.assertTrue(reopened.memory_buttons["release_after_transcription"].isChecked())

    def test_ui_storage_failure_restores_selection(self):
        """The exclusive controls roll back visibly if configuration cannot be saved."""
        dialog = self.dialog()
        with patch.object(self.config, "_save", side_effect=OSError("disk full")), patch.object(QMessageBox, "warning"):
            dialog.memory_buttons["release_after_transcription"].click()
        self.assertTrue(dialog.memory_buttons["keep_loaded"].isChecked())

    def test_keep_loaded_without_refine(self):
        """Default mode retains a resident model and the original note."""
        note = self.run_pipeline("keep_loaded")
        self.assertEqual(self.events, ["transcribe", "ready"])
        self.assertIsNotNone(self.engine.model)
        self.assertEqual(note["full_text"], "Original text.")

    def test_keep_loaded_with_refine(self):
        """Default refinement never unloads the engine."""
        self.run_pipeline("keep_loaded", True)
        self.assertEqual(self.events, ["transcribe", "refine", "ready"])
        self.assertIsNotNone(self.engine.model)

    def test_release_before_refine_off(self):
        """The LLM-specific policy does nothing when refinement is disabled."""
        self.run_pipeline("release_before_postprocessing")
        self.assertEqual(self.events, ["transcribe", "ready"])
        self.assertIsNotNone(self.engine.model)

    def test_release_before_refine_on(self):
        """Raw segments are saved before release and final text is refined afterwards."""
        saved_at_release = []
        self.engine.on_unload = lambda: saved_at_release.append(self.fixture.repo.read_note(self.controller._raw_note_path))
        note = self.run_pipeline("release_before_postprocessing", True)
        self.assertEqual(self.events, ["transcribe", "unload-start", "unload-end", "refine", "ready"])
        self.assertEqual(saved_at_release[0]["full_text"], "Original text.")
        self.assertEqual(note["raw_text"], "Original text.")
        self.assertEqual(note["full_text"], "Refined text.")
        self.assertEqual(note["segments"], self.engine.segments)

    def test_release_after_without_refine(self):
        """Every successful transcription releases before Ready even without an LLM."""
        self.run_pipeline("release_after_transcription")
        self.assertEqual(self.events, ["transcribe", "unload-start", "unload-end", "ready"])
        self.assertIsNone(self.engine.model)

    def test_release_after_with_refine(self):
        """Automatic release completes before refinement starts."""
        self.run_pipeline("release_after_transcription", True)
        self.assertEqual(self.events, ["transcribe", "unload-start", "unload-end", "refine", "ready"])

    def test_next_transcription_reloads_selected_model(self):
        """Two recordings reload exactly between uses and preserve model/device/language."""
        self.run_pipeline("release_after_transcription")
        self.events.clear()
        self.run_pipeline("release_after_transcription")
        self.assertEqual(self.events, ["load", "transcribe", "unload-start", "unload-end", "ready"])
        self.assertEqual((self.engine.current_model_name, self.engine.device, self.config.get_language()), ("tiny", "cpu", "ru"))

    def test_unload_failure_preserves_raw_and_skips_llm(self):
        """An uncertain release is logged/reported while the saved original remains intact."""
        self.engine.unload_error = True
        with self.assertLogs("WhisperNote.app_controller", level="ERROR"):
            note = self.run_pipeline("release_before_postprocessing", True)
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(note["full_text"], "Original text.")
        self.assertEqual(note["segments"], self.engine.segments)
        self.provider.process.assert_not_called()
        self.assertFalse(self.controller.is_unloading_model)

    def test_manual_repeated_unload_changes_no_settings(self):
        """Manual release is safe while unloaded and never starts recognition."""
        original = dict(self.config._config)
        self.assertTrue(self.controller.unload_model_now())
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertFalse(self.controller.unload_model_now())
        self.assertNotIn("transcribe", self.events)
        self.assertEqual(self.config._config, original)
        self.events.clear()
        self.run_pipeline("keep_loaded")
        self.assertEqual(self.events, ["load", "transcribe", "ready"])

    def test_release_worker_is_responsive_and_prevents_overlapping_actions(self):
        """UI stays busy while slow release runs on a different thread; all engine work is guarded."""
        window = self.harness.window()
        dialog = self.dialog(window)
        self.engine.unload_gate = self.gate()
        thread_ids = []
        self.engine.on_unload = lambda: thread_ids.append(QThread.currentThread() is self.fixture.app.thread())
        self.controller.set_model_memory_mode("release_before_postprocessing")
        self.controller.refine_enabled.return_value = True
        self.assertTrue(self.controller.start_file_transcription(str(self.source)))
        self.fixture.wait_for(lambda: "unload-start" in self.events)
        self.assertTrue(self.controller.is_processing)
        self.assertTrue(window.is_busy)
        self.assertFalse(dialog.unload_model_button.isEnabled())
        self.assertFalse(self.controller.unload_model_now())
        self.assertFalse(self.controller.start_recording())
        self.assertFalse(self.controller.start_file_transcription(str(self.source)))
        self.assertFalse(self.controller.request_model_change("base"))
        self.assertNotIn("ready", self.events)
        self.assertNotIn("refine", self.events)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        self.fixture.wait_for(lambda: heartbeat)
        self.assertEqual(thread_ids, [False])
        self.engine.unload_gate.set()
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertFalse(dialog.unload_model_button.isEnabled())
        self.assertTrue(dialog.load_model_button.isEnabled())
        self.assertEqual(self.events[-2:], ["refine", "ready"])

    def test_result_signal_does_not_allow_early_unload(self):
        """An emitted result is insufficient: release waits for the transcription thread exit."""
        gate = self.gate()

        class DelayedWorker(Worker):
            """Hold the worker alive after producing its transcription result."""

            def run(inner):
                """Use the real worker for release and hold only the decoder worker."""
                if inner.function.__name__ != "transcribe":
                    return super().run()
                inner.signals.result.emit(inner.function())
                gate.wait(5)
                inner.signals.finished.emit()

        self.controller.set_model_memory_mode("release_after_transcription")
        with patch("controllers.app_controller.Worker", DelayedWorker):
            self.assertTrue(self.controller.start_file_transcription(str(self.source)))
            self.fixture.wait_for(lambda: self.controller._transcription_result is not None)
            self.assertFalse(self.controller._transcription_thread.isFinished())
            self.assertEqual(self.events, ["transcribe"])
            self.assertFalse(self.controller.unload_model_now())
            gate.set()
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.events, ["transcribe", "unload-start", "unload-end", "ready"])

    def test_policy_changes_apply_to_next_pipeline_only(self):
        """Changing the enum while busy never changes the captured policy mid-pipeline."""
        self.engine.load_gate = self.gate()
        self.engine.model = None
        self.controller.set_model_memory_mode("keep_loaded")
        self.assertTrue(self.controller.start_file_transcription(str(self.source)))
        self.fixture.wait_for(lambda: "load" in self.events)
        dialog = self.dialog()
        dialog.memory_buttons["release_after_transcription"].click()
        self.assertTrue(all(b.isEnabled() for b in dialog.memory_buttons.values()))
        self.engine.load_gate.set()
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertIsNotNone(self.engine.model)
        self.assertNotIn("unload-start", self.events)
        self.run_pipeline("release_after_transcription")
        self.assertIsNone(self.engine.model)

    def test_manual_button_disabled_during_model_load_and_recording(self):
        """Loading and recording transitions keep the manual action unavailable."""
        dialog = self.dialog()
        self.engine.model = None
        self.engine.load_gate = self.gate()
        self.controller.model_registry.is_model_present = Mock(return_value=True)
        with patch.object(self.controller, "_create_engine", return_value=self.engine):
            self.assertTrue(self.controller.request_model_change("base", allow_download=True))
            self.fixture.wait_for(lambda: "load" in self.events)
            self.assertFalse(dialog.unload_model_button.isEnabled())
            self.assertFalse(self.controller.unload_model_now())
            self.engine.load_gate.set()
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertTrue(dialog.unload_model_button.isEnabled())
        with patch.object(self.controller.recording_controller, "state", "recording"):
            self.controller.recording_state_changed.emit("recording")
            self.assertFalse(dialog.unload_model_button.isEnabled())
            self.assertFalse(self.controller.unload_model_now())

    def test_shutdown_during_release_waits_and_skips_refinement(self):
        """Closing near release drains workers without starting an LLM or deleting live Qt objects."""
        window = self.harness.window()
        self.engine.unload_gate = self.gate()
        self.controller.set_model_memory_mode("release_before_postprocessing")
        self.controller.refine_enabled.return_value = True
        self.assertTrue(self.controller.start_file_transcription(str(self.source)))
        self.fixture.wait_for(lambda: "unload-start" in self.events)
        window.request_exit()
        self.assertFalse(window._shutdown_complete)
        self.assertFalse(self.controller.unload_model_now())
        self.engine.unload_gate.set()
        self.fixture.wait_for(lambda: window._shutdown_complete)
        self.provider.process.assert_not_called()
        self.assertEqual(self.fixture.repo.read_note(self.controller._raw_note_path)["full_text"], "Original text.")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_reload_failure_recovers_busy_state(self):
        """A failed lazy reload reports failure and permits a later successful attempt."""
        self.engine.model = None
        self.engine.load_error = True
        errors = QSignalSpy(self.controller.transcription_failed)
        self.assertTrue(self.controller.start_file_transcription(str(self.source)))
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(len(errors), 1)
        self.assertIsNone(self.controller._raw_note_path)
        self.engine.load_error = False
        self.run_pipeline("keep_loaded")
        self.assertIsNotNone(self.engine.model)

    def test_empty_successful_transcription_still_releases(self):
        """An empty result creates no note but honors release-after and returns idle."""
        self.engine.text = ""
        self.controller.set_model_memory_mode("release_after_transcription")
        self.assertTrue(self.controller.start_file_transcription(str(self.source)))
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertIsNone(self.controller._raw_note_path)
        self.assertIsNone(self.engine.model)

    def test_faster_whisper_references_idempotency_and_reload(self):
        """CT2 unloads native weights, drops the wrapper and can reload/transcribe on CPU."""
        engine = FasterWhisperEngine(str(self.harness.root / "models"), "cpu")
        engine.unload_model()
        native = Mock()

        class Wrapper:
            """Own only the fake native backend for reference-lifetime assertions."""

        wrapper = Wrapper()
        wrapper.model = native
        engine.model = wrapper
        engine.current_model_name = "tiny"
        reference = weakref.ref(wrapper)
        del wrapper
        engine.unload_model()
        native.unload_model.assert_called_once_with(to_cpu=False)
        self.assertIsNone(reference())
        self.assertFalse(engine.is_model_loaded("tiny"))
        engine.unload_model()
        replacement = Mock()
        replacement.transcribe.return_value = (iter([SimpleNamespace(start=1, end=2, text="Text")]), SimpleNamespace(duration=2))
        with patch("core.transcription.engines.faster_whisper.WhisperModel", return_value=replacement):
            engine.load_model("tiny")
        self.assertEqual(engine.transcribe(str(self.source))["text"], "Text")
        self.assertEqual((engine.current_model_name, engine.device), ("tiny", "cpu"))

    def test_openai_cpu_reference_release_and_reload(self):
        """CPU Whisper drops Python weights without touching CUDA and reloads normally."""
        with patch.object(OpenAIWhisperEngine, "_configure_ffmpeg"):
            engine = OpenAIWhisperEngine(str(self.harness.root / "models"), "cpu")
        engine.unload_model()
        model = Mock(device="cpu")
        engine.model, engine.current_model_name = model, "tiny"
        reference = weakref.ref(model)
        del model
        with patch("torch.cuda.empty_cache") as empty_cache:
            engine.unload_model()
            engine.unload_model()
            empty_cache.assert_not_called()
        self.assertIsNone(reference())
        replacement = Mock(device="cpu")
        replacement.transcribe.return_value = {"text": "Text", "segments": self.engine.segments}
        with patch("core.transcription.engines.openai_whisper.whisper.load_model", return_value=replacement), patch.object(
                engine, "_process_audio_before_transcription", return_value=(str(self.source), 2)):
            engine.load_model("tiny")
            self.assertEqual(engine.transcribe(str(self.source))["text"], "Text")
        self.assertEqual((engine.current_model_name, engine.device), ("tiny", "cpu"))

    def test_openai_cuda_cache_cleared_after_references(self):
        """Mocked CUDA cleanup runs only after the model reference has been released."""
        with patch.object(OpenAIWhisperEngine, "_configure_ffmpeg"):
            engine = OpenAIWhisperEngine(str(self.harness.root / "models"), "cuda")
        model = Mock(device="cuda:0")
        engine.model, engine.current_model_name = model, "tiny"
        reference = weakref.ref(model)
        del model
        collected = []
        with patch("torch.cuda.is_available", return_value=True), patch("torch.cuda.device", return_value=nullcontext()), patch(
                "torch.cuda.empty_cache", side_effect=lambda: collected.append(reference() is None)):
            engine.unload_model()
            engine.unload_model()
        self.assertEqual(collected, [True])
        self.assertEqual((engine.current_model_name, engine.device), ("tiny", "cuda"))

    def test_faster_native_failure_still_drops_wrapper(self):
        """The CT2 adapter surfaces native errors without leaving its model usable."""
        engine = FasterWhisperEngine(str(self.harness.root / "models"), "cpu")
        native = Mock()
        native.unload_model.side_effect = RuntimeError("Native release failed")
        engine.model = SimpleNamespace(model=native)
        with self.assertRaises(RuntimeError):
            engine.unload_model()
        self.assertIsNone(engine.model)
        engine.unload_model()
