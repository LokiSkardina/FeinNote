# Responsible for: isolated regressions for the user-testing improvement iteration.

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import test_application as application
from PyQt6.QtCore import Qt, QCoreApplication, QEvent
from PyQt6.QtGui import QCursor, QFocusEvent
from PyQt6.QtTest import QTest, QSignalSpy

from core.single_instance import SingleInstance


class UserIterationTests(unittest.TestCase):
    """Exercise real Qt orchestration with project-local data and fake models."""

    @classmethod
    def setUpClass(cls):
        """Reuse the suite's offscreen QApplication and torch import ordering."""
        application.ApplicationTests.setUpClass()

    def setUp(self):
        """Create an isolated application harness without live data or credentials."""
        self.harness = application.ApplicationTests()
        self.harness.setUp()
        self.root = self.harness.root
        self.controller = self.harness.controller
        self.fixture = self.harness.fixture
        self.config = self.harness.config
        self.instances = []

    def tearDown(self):
        """Drain owned workers and release IPC before removing the test fixtures."""
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        for instance in self.instances:
            instance.close()
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def instance(self, directory=None):
        """Track a test-owned portable instance guard for reliable cleanup."""
        instance = SingleInstance(directory or self.root)
        self.instances.append(instance)
        return instance

    def batch_engine(self):
        """Install a deterministic resident engine and language for queue tests."""
        from tests.test_model_memory import FakeMemoryEngine
        self.events = []
        engine = FakeMemoryEngine(self.events)
        self.controller.engine = engine
        self.config.set_language("ru")
        self.controller.refine_enabled = Mock(return_value=False)
        return engine

    def test_batch_saves_in_order_and_continues_after_failure(self):
        """A failed source neither duplicates results nor prevents subsequent saves."""
        engine = self.batch_engine()
        def transcribe(path, language, options):
            self.events.append(path)
            if path == "bad.ogg":
                raise ValueError("fixture failure")
            if path == "last.wav":
                self.assertEqual(len(self.fixture.repo.load_notes()), 1)
            return {"text": path, "segments": engine.segments, "duration": 3}
        engine.transcribe = transcribe
        self.assertTrue(self.controller.start_file_batch(["first.mp3", "bad.ogg", "last.wav"]))
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.events, ["first.mp3", "bad.ogg", "last.wav"])
        self.assertEqual([r["status"] for r in self.controller.batch.rows], ["Completed", "Failed", "Completed"])
        notes = self.fixture.repo.load_notes()
        self.assertEqual({n["source_name"] for n, _ in notes}, {"first.mp3", "last.wav"})
        self.assertTrue(all(n["segments"] == engine.segments for n, _ in notes))

    def test_batch_refines_after_all_raw_saves_and_releases_once(self):
        """Stage B uses the captured provider exactly once per saved raw note."""
        self.batch_engine()
        self.config.set_model_memory_mode("release_before_postprocessing")
        self.controller.refine_enabled.return_value = True
        provider = Mock()
        def refine(text, prompt):
            self.assertEqual(len(self.fixture.repo.load_notes()), 2)
            self.events.append("refine")
            return "refined"
        provider.process.side_effect = refine
        self.controller.post_processing.provider = Mock(return_value=provider)
        self.controller.start_file_batch(["a.wav", "b.wav"])
        self.config.set_model_memory_mode("keep_loaded")
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.events, ["transcribe", "transcribe", "unload-start", "unload-end", "refine", "refine"])
        self.assertEqual(provider.process.call_count, 2)
        self.assertTrue(all(n["raw_text"] != n["full_text"] for n, _ in self.fixture.repo.load_notes()))

    def test_batch_stop_preserves_current_result(self):
        """Stop does not discard the operation in flight or schedule the next file."""
        self.batch_engine()
        self.controller.start_file_batch(["a.wav", "b.wav"])
        self.controller.batch.stop()
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(len(self.fixture.repo.load_notes()), 1)
        self.assertEqual([r["status"] for r in self.controller.batch.rows], ["Completed", "Cancelled"])

    def test_finished_batch_does_not_retain_replaced_engine(self):
        """An idle queue must not keep the previous model alive after engine replacement."""
        import gc
        import weakref
        engine = self.batch_engine()
        previous = weakref.ref(engine)
        self.controller.start_file_batch(["fixture.wav"])
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.controller.engine = Mock()
        del engine
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        self.assertIsNone(previous(), "Finished queue retains the previous engine/model")

    def test_completed_queue_resets_on_recording_and_single_file_work(self):
        """Accepted new work clears the previous batch summary without changing its notes."""
        self.batch_engine()
        window = self.harness.window()
        self.controller.start_file_batch(["first.wav", "second.wav"])
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertTrue(window.queue_panel.isVisible())
        self.assertFalse(window.queue_stop.isVisible())
        self.assertFalse(window.queue_progress.isVisible())
        recording = self.controller.recording_controller
        recording.recorder.is_running.return_value = False
        recording.recorder.output_filename = None
        with patch.object(recording, "is_microphone_available", return_value=True):
            self.assertTrue(self.controller.start_recording())
        self.assertFalse(window.queue_panel.isVisible())
        self.assertEqual(window.queue_list.count(), 0)
        self.controller.cancel_recording()
        recording._on_recording_finished(None)
        self.controller.start_file_batch(["third.wav"])
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        source = self.root / "single.wav"
        source.touch()
        self.assertTrue(self.controller.start_file_transcription(str(source)))
        self.assertFalse(window.queue_panel.isVisible())
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertFalse(window.queue_panel.isVisible())
        self.assertEqual(len(self.fixture.repo.load_notes()), 4)
        self.controller.start_file_batch(["new-batch.wav"])
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(window.queue_list.count(), 1)
        self.assertIn("new-batch.wav", window.queue_list.item(0).text())

    def test_batch_ui_explains_two_stages_and_preserves_originals(self):
        """Raw saving precedes Refine and the live UI distinguishes both stages."""
        import threading
        self.batch_engine()
        self.controller.refine_enabled.return_value = True
        window = self.harness.window()
        gate = threading.Event()
        self.addCleanup(gate.set)
        provider = Mock()
        provider.process.side_effect = lambda text, prompt: (gate.wait(3), "Edited fixture")[1]
        self.controller.post_processing.provider = Mock(return_value=provider)
        snapshots = QSignalSpy(self.controller.batch.changed)
        try:
            self.assertTrue(self.controller.start_file_batch(["one.wav", "two.wav"]))
            self.assertIn("Step 1/2", window.queue_label.text())
            self.assertIn("Refine starts after", window.queue_label.text())
            self.fixture.wait_for(lambda: self.controller.batch.phase == "Refining")
            self.assertIn("Step 2/2", window.queue_label.text())
            self.assertIn("Awaiting Refine", window.queue_list.item(1).text())
            notes = self.fixture.repo.load_notes()
            self.assertEqual(len(notes), 2)
            self.assertTrue(all(data["full_text"] == data["raw_text"] for data, _ in notes))
            self.assertTrue(any(s[0]["stage"] == "transcribe" and
                                s[0]["rows"][0]["status"] == "Awaiting Refine" for s in snapshots))
        finally:
            gate.set()
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(provider.process.call_count, 2)
        self.assertIn("2/2 files completed", window.queue_label.text())
        self.assertIn("Transcribed · Refined", window.queue_list.item(0).text())

    def test_stopped_batch_marks_saved_refinement_as_cancelled(self):
        """Stopping stage one preserves the raw note and explicitly cancels its Refine."""
        self.batch_engine()
        self.controller.refine_enabled.return_value = True
        provider = Mock()
        self.controller.post_processing.provider = Mock(return_value=provider)
        window = self.harness.window()
        self.controller.start_file_batch(["one.wav", "two.wav"])
        self.controller.batch.stop()
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        provider.process.assert_not_called()
        self.assertEqual(self.controller.batch.rows[0]["status"], "Refine cancelled")
        self.assertIn("original saved", window.queue_list.item(0).text())
        self.assertIn("Stopped", window.queue_label.text())
        self.assertIn("2 cancelled", window.queue_label.text())
        self.assertEqual(len(self.fixture.repo.load_notes()), 1)
        updates = QSignalSpy(self.controller.batch.changed)
        self.controller.batch.stop()
        self.assertEqual(len(updates), 0)

    def test_queue_header_has_no_background_band_and_reports_failures(self):
        """Queue captions blend into both themes and do not call failed files completed."""
        window = self.harness.window()
        state = dict(rows=[dict(name="saved.wav", status="Completed", error="", path="saved"),
                           dict(name="failed.wav", status="Failed", error="Fixture error", path="raw")],
                     index=2, total=2, done=4, units=4, running=False, stopping=False,
                     phase="Completed with errors", stage="refine", refine_enabled=True)
        for theme in ("light", "dark"):
            window.set_theme(theme)
            window._render_batch(state)
            self.fixture.app.processEvents()
            self.assertIn("1/2 files completed", window.queue_label.text())
            self.assertIn("1 failed", window.queue_label.text())
            self.assertIn("Refine failed", window.queue_list.item(1).text())
            self.assertIn("original saved", window.queue_list.item(1).text())
            self.assertFalse(window.queue_stop.isVisible())
            picture = window.grab().toImage()
            point = window.queue_label.mapTo(window, window.queue_label.rect().topRight())
            scale = picture.devicePixelRatio()
            self.assertEqual(picture.pixelColor(round((point.x() - 2) * scale),
                                                round((point.y() + 2) * scale)).name(), window.theme["WHITE_BG"])

    def test_mouse_note_deletion_does_not_focus_settings(self):
        """Deleting a focused trash button must not synthesize keyboard focus on the gear."""
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QMessageBox
        window = self.harness.window()
        for confirm in (False, True):
            self.controller.set_confirm_note_deletion(confirm)
            path = self.fixture.notes.create_transcription_note("Delete fixture", 1)
            card = window._cards_by_filepath[path]
            self.fixture.app.processEvents()
            card.delete_btn.show()
            card.delete_btn.setFocus(Qt.FocusReason.MouseFocusReason)
            if confirm:
                def accept_confirmation():
                    for box in QApplication.topLevelWidgets():
                        if isinstance(box, QMessageBox):
                            box.button(QMessageBox.StandardButton.Yes).click()
                QTimer.singleShot(30, accept_confirmation)
            QTest.mouseClick(card.delete_btn, Qt.MouseButton.LeftButton)
            QTest.qWait(40)
            self.assertNotIn(path, window._cards_by_filepath)
            self.assertFalse(window.settings_button.hasFocus())
            self.assertFalse(window.settings_button.property("keyboardFocus"))
        # Intentional keyboard navigation must still have its visible focus ring.
        window.activateWindow()
        self.fixture.app.processEvents()
        window.text_area.setFocus(Qt.FocusReason.OtherFocusReason)
        window.settings_button.setFocus(Qt.FocusReason.TabFocusReason)
        self.assertTrue(window.settings_button.property("keyboardFocus"))

    def test_model_display_text_resolves_id_after_edit(self):
        """Completion text must resolve to its ID even before the combo index changes."""
        from widgets.settings_panel import ModelSelector
        selector = ModelSelector()
        self.addCleanup(selector.deleteLater)
        selector.set_catalog([{"id": "vendor/one", "name": "First"},
                              {"id": "vendor/two", "name": "Second"}])
        selector.setCurrentIndex(0)
        selector.setEditText(selector.itemText(1))
        self.assertEqual(selector.text(), "vendor/two")
        selector.setEditText("manual/model")
        self.assertEqual(selector.text(), "manual/model")

    def test_failed_autosave_preserves_editor_across_context_changes(self):
        """A failed atomic write must not clear, move, delete, or replace pending edits."""
        window = self.harness.window()
        folder = self.fixture.folder()
        self.controller.select_folder(folder)
        path = self.fixture.note("saved", folder)
        other = self.fixture.note("other", folder)
        self.fixture.notes.load_notes()
        self.controller.select_note(path)
        window.text_area.setPlainText("unsaved edits")
        cleared = QSignalSpy(self.fixture.notes.active_note_cleared)
        actions = {
            "select note": lambda: self.controller.select_note(other),
            "select folder": lambda: self.controller.select_folder("inbox"),
            "clear editor": window.reset_editor_state,
            "move note": lambda: self.fixture.notes.move_note(path, "inbox"),
            "delete note": lambda: self.controller.delete_note(path),
            "delete folder": lambda: self.fixture.notes.delete_folder(folder),
        }
        with patch("core.autosave.os.replace", side_effect=PermissionError("fixture write denied")):
            for name, action in actions.items():
                with self.subTest(action=name):
                    action()
                    self.assertEqual(self.fixture.notes.active_filepath, path)
                    self.assertEqual(self.fixture.notes.active_folder, folder)
                    self.assertEqual(window.text_area.toPlainText(), "unsaved edits")
                    self.assertEqual(self.fixture.repo.read_note(path)["full_text"], "saved")
                    self.assertEqual(len(cleared), 0)
        self.controller.select_note(other)
        self.assertEqual(self.fixture.repo.read_note(path)["full_text"], "unsaved edits")
        self.assertEqual(window.text_area.toPlainText(), "other")

    def test_failed_autosave_blocks_new_audio_work(self):
        """Recording and file workflows must not start after failing to save the editor."""
        self.batch_engine()
        path = self.fixture.note("saved")
        self.controller.select_note(path)
        self.fixture.notes.set_text_getter(lambda: "unsaved edits")
        source = self.root / "fixture.wav"
        source.touch()
        with patch("core.autosave.os.replace", side_effect=PermissionError("fixture write denied")), \
                patch.object(self.controller.recording_controller, "is_microphone_available", return_value=True), \
                patch.object(self.controller.recording_controller, "start") as start, \
                patch.object(self.controller, "_start_transcription") as transcribe, \
                patch.object(self.controller.batch, "start") as batch:
            self.assertFalse(self.controller.start_recording())
            self.assertFalse(self.controller.prepare_for_file_selection())
            self.assertFalse(self.controller.start_file_transcription(str(source)))
            self.assertFalse(self.controller.start_file_batch([str(source)]))
            start.assert_not_called()
            transcribe.assert_not_called()
            batch.assert_not_called()
        self.assertEqual(self.fixture.notes.active_filepath, path)
        self.assertTrue(self.fixture.notes.autosave.force_save())
        self.assertEqual(self.fixture.repo.read_note(path)["full_text"], "unsaved edits")

    def test_new_transcription_does_not_replace_unsaved_editor(self):
        """Keep a completed result on disk even if the previous editor cannot be saved."""
        window = self.harness.window()
        path = self.fixture.note("saved")
        self.fixture.notes.load_notes()
        self.controller.select_note(path)
        window.text_area.setPlainText("unsaved edits")
        replace = os.replace
        def deny_active_note(source, destination):
            if os.fspath(destination) == path:
                raise PermissionError("fixture write denied")
            return replace(source, destination)
        with patch("core.autosave.os.replace", side_effect=deny_active_note):
            created = self.fixture.notes.create_transcription_note("new result", 1)
        self.assertEqual(self.fixture.repo.read_note(created)["full_text"], "new result")
        self.assertIn(created, window._cards_by_filepath)
        self.assertEqual(self.fixture.notes.active_filepath, path)
        self.assertEqual(window.text_area.toPlainText(), "unsaved edits")
        self.assertTrue(self.fixture.notes.autosave.force_save())

    def test_failed_autosave_cancels_exit_without_disabling_window(self):
        """Exit can be retried after storage recovers, without losing pending editor text."""
        window = self.harness.window()
        path = self.fixture.note("saved")
        self.fixture.notes.load_notes()
        self.controller.select_note(path)
        window.text_area.setPlainText("unsaved edits")
        with patch("core.autosave.os.replace", side_effect=PermissionError("fixture write denied")), \
                patch.object(window, "show_error_dialog") as error:
            window.request_exit()
            self.assertFalse(window._exit_requested)
            self.assertFalse(self.controller.is_shutting_down)
            self.assertFalse(self.controller._shutdown_timer.isActive())
            self.assertTrue(window.isEnabled())
            self.assertEqual(window.text_area.toPlainText(), "unsaved edits")
            error.assert_called_once()
            self.harness.quit.assert_not_called()
        window.request_exit()
        self.fixture.wait_for(lambda: window._shutdown_complete)
        self.assertEqual(self.fixture.repo.read_note(path)["full_text"], "unsaved edits")
        self.harness.quit.assert_called_once()

    def test_atomic_save_temporaries_are_invisible_to_retention(self):
        """A concurrent scan must not count a pending write as an extra saved note."""
        from core.notes.cleanup_service import CleanupService
        self.fixture.note("older", timestamp=1)
        path = self.fixture.note("newer", timestamp=2)
        self.controller.select_note(path)
        self.fixture.notes.set_text_getter(lambda: "edited")
        replace = os.replace
        snapshots = []
        def scan_before_commit(source, destination):
            # This is the exact interval in which the retention worker can
            # observe a fully written temporary file before atomic replacement.
            visible = self.fixture.repo.load_notes()
            snapshots.append((len(visible), CleanupService.candidates(visible, {"mode": "count", "value": 2})))
            return replace(source, destination)
        writers = {
            "autosave": self.fixture.notes.autosave.force_save,
            "repository": lambda: self.fixture.repo.update_note_text(path, "refined"),
        }
        for name, write in writers.items():
            with self.subTest(writer=name):
                with patch("os.replace", side_effect=scan_before_commit):
                    write()
                self.assertEqual(snapshots[-1], (2, []))

    def test_failed_transcription_honors_captured_memory_policy(self):
        """A decoding exception must release only in the unconditional release-after mode."""
        engine = self.batch_engine()
        engine.transcribe_error = True
        source = self.root / "invalid.wav"
        source.touch()
        for mode in self.config.MODEL_MEMORY_MODES:
            with self.subTest(mode=mode):
                engine.model = object()
                self.events.clear()
                self.config.set_model_memory_mode(mode)
                failed = QSignalSpy(self.controller.transcription_failed)
                self.assertTrue(self.controller.start_file_transcription(str(source)))
                self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
                self.assertEqual(len(failed), 1)
                self.assertEqual(self.fixture.repo.load_notes(), [])
                released = mode == "release_after_transcription"
                self.assertEqual(engine.model is None, released)
                self.assertEqual(self.events, ["transcribe"] + (["unload-start", "unload-end"] if released else []))

    def test_manual_load_unload_and_actual_device_status(self):
        """Manual preloading uses a worker and residency gates conflicting actions."""
        engine = self.batch_engine()
        self.assertEqual(self.controller.model_memory_status(), "Model loaded (CPU)")
        self.assertFalse(self.controller.can_load_model())
        self.controller.unload_model_now()
        self.assertFalse(self.controller.can_load_model())
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.controller.model_memory_status(), "Model unloaded")
        self.assertFalse(self.controller.can_unload_model())
        self.config.set_device_mode("cpu")
        with patch.object(self.controller.model_registry, "is_model_present", return_value=True), patch.object(self.controller, "_create_engine", return_value=engine):
            self.assertTrue(self.controller.load_model_now())
            self.assertEqual(self.controller.model_memory_status(), "Loading model…")
            self.assertFalse(self.controller.unload_model_now())
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.events, ["unload-start", "unload-end", "load"])
        engine.device = "cuda"
        self.assertEqual(self.controller.model_memory_status(), "Model loaded (CUDA)")

    def test_manual_load_failure_recovers_controls(self):
        """A failed load is not reported as resident and can be retried explicitly."""
        engine = self.batch_engine()
        engine.model, engine.load_error = None, True
        with patch.object(self.controller.model_registry, "is_model_present", return_value=True), patch.object(self.controller, "_create_engine", return_value=engine):
            self.controller.load_model_now()
            self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.controller.model_memory_status(), "Model load failed")
        self.assertTrue(self.controller.can_load_model())
        self.assertFalse(self.controller.can_unload_model())

    def test_original_refined_switch_autosave_copy_export_reopen(self):
        """Original remains immutable while final edits survive switching and reopening."""
        window = self.harness.window()
        notes = self.fixture.notes
        path = notes.create_transcription_note("raw", 3, segments=[{"start": 0, "end": 3, "text": "raw"}])
        self.controller.save_refined_note(path, "refined")
        self.assertTrue(window.version_switch.isVisible())
        window.text_area.setPlainText("edited final")
        window._select_version(True)
        self.assertTrue(window.text_area.isReadOnly())
        self.assertEqual(window.text_area.toPlainText(), "raw")
        window.copy_text()
        self.assertEqual(self.fixture.app.clipboard().text(), "raw")
        self.assertEqual(self.controller.get_export_data(edited_text="raw")["full_text"], "edited final")
        notes.force_save()
        self.assertEqual(self.fixture.repo.read_note(path)["full_text"], "edited final")
        notes.select_note(path)
        self.assertEqual(window.text_area.toPlainText(), "edited final")
        self.assertFalse(window.text_area.isReadOnly())
        self.assertEqual(self.fixture.repo.read_note(path)["raw_text"], "raw")

    def test_original_switch_blocks_on_autosave_error_and_legacy_stays_single(self):
        """Failed writes do not discard edits; text inequality does not manufacture success."""
        window = self.harness.window()
        path = self.fixture.notes.create_transcription_note("raw", 1)
        self.fixture.repo.update_note_text(path, "legacy edited")
        self.fixture.notes.select_note(path)
        self.assertFalse(window.version_switch.isVisible())
        self.controller.save_refined_note(path, "raw")
        self.assertTrue(window.version_switch.isVisible())
        window.text_area.setPlainText("pending")
        with patch.object(self.fixture.notes.autosave, "force_save", return_value=False):
            window._select_version(True)
        self.assertEqual(window.text_area.toPlainText(), "pending")
        self.assertFalse(self.fixture.notes.viewing_original)

    def test_presets_catalog_and_manual_model_id(self):
        """Explicit presets set endpoints, preserve prompt/key, and suggestions save IDs."""
        from widgets.settings_panel import PostProcessingPanel
        panel = PostProcessingPanel(self.controller)
        self.addCleanup(panel.deleteLater)
        prompt = panel.prompt.toPlainText()
        panel.key.setText("fake-key")
        panel.preset.setCurrentIndex(panel.preset.findData("openrouter"))
        self.assertEqual(panel.url.text(), "https://openrouter.ai/api/v1")
        self.assertEqual(panel.key.text(), "fake-key")
        self.assertEqual(panel.prompt.toPlainText(), prompt)
        panel.model.set_catalog([{"id": "vendor/id", "name": "Friendly name"}])
        panel.model.setCurrentIndex(0)
        self.assertEqual(panel.model.text(), "vendor/id")
        self.assertTrue(panel._save())
        self.assertEqual(self.controller.get_post_settings()["model"], "vendor/id")
        panel.model.setEditText("manual/id")
        self.assertEqual(panel.model.text(), "manual/id")

    def test_custom_settings_and_catalog_errors_preserve_drafts(self):
        """Opening legacy custom settings and failed discovery do not rewrite user choices."""
        from widgets.settings_panel import PostProcessingPanel
        settings = self.fixture.settings()
        settings.update(base_url="http://localhost:4321/custom", prompt="Custom prompt", model="my-model")
        self.controller.save_post_settings(settings, "fixture-key")
        panel = PostProcessingPanel(self.controller)
        self.addCleanup(panel.deleteLater)
        panel.key.setText("new-key")
        self.assertEqual(panel.preset.currentData(), "custom")
        self.assertEqual(panel.url.text(), settings["base_url"])
        self.assertEqual(panel.prompt.toPlainText(), "Custom prompt")
        with patch.object(self.controller, "refresh_post_models"):
            panel._refresh_models()
        panel._models_received({"id": panel._catalog_id, "error": "Timeout"})
        self.assertEqual(panel.model.text(), "my-model")
        self.assertEqual(panel.key.text(), "new-key")

    def test_catalog_and_diagnostics_mocked_transport(self):
        """Catalog uses GET, refinement logs numeric usage and never text or secrets."""
        import io
        import json
        from core.postprocessing.providers.openai_compatible import OpenAICompatibleProvider
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps({"data": [{"id": "id", "name": "Display"}]}).encode()),
                                  io.BytesIO(json.dumps({"choices": [{"message": {"content": "edited private"}}],
                                                        "usage": {"total_tokens": 42, "secret": "private"}}).encode())]
        provider = OpenAICompatibleProvider(self.fixture.settings(), "fake-secret", opener)
        with self.assertLogs("WhisperNote.postprocessing", "INFO") as logs:
            self.assertEqual(provider.list_models(), [{"id": "id", "name": "Display"}])
            self.assertEqual(opener.open.call_args.args[0].get_method(), "GET")
            provider.process("private conversation", "private prompt")
        output = "\n".join(logs.output)
        for secret in ("fake-secret", "private conversation", "private prompt", "edited private"):
            self.assertNotIn(secret, output)
        self.assertEqual(provider.last_diagnostics["usage"], {"total_tokens": 42})
        self.assertIn("refine_elapsed_seconds", provider.last_diagnostics)
        self.assertEqual(opener.open.call_count, 2)

    def test_network_failures_are_categorized_without_retries(self):
        """Timeout, authentication, and rate limit errors are sanitized and sent once."""
        from urllib.error import HTTPError, URLError
        from core.postprocessing.providers.openai_compatible import OpenAICompatibleProvider
        for error, category in ((TimeoutError(), "timeout"), (URLError("private"), "connection_error"),
                                (HTTPError("https://invalid", 401, "private", {}, None), "http_error"),
                                (HTTPError("https://invalid", 429, "private", {}, None), "rate_limit")):
            opener = Mock()
            opener.open.side_effect = error
            provider = OpenAICompatibleProvider(self.fixture.settings(), "fake-key", opener)
            with self.assertRaises(RuntimeError):
                provider.process("private", "prompt")
            self.assertEqual(provider.last_diagnostics["category"], category)
            opener.open.assert_called_once()

    def test_new_controls_share_delayed_help(self):
        """Functional controls reuse the two-second tooltip manager in both themes."""
        window = self.harness.window()
        for theme in ("light", "dark"):
            window.set_theme(theme)
            self.assertEqual(window.interactions.timer.interval(), 2000)
            for widget in (window.queue_panel, window.queue_stop, window.original_button,
                           window.refined_button, window.download_button, window.folder_selector):
                self.assertTrue(widget.toolTip())

    def test_main_controls_and_headings_have_explanatory_help(self):
        """Icon actions, headings and workflow fields explain their purpose in both themes."""
        window = self.harness.window()
        names = ("app_title", "settings_button", "primary_button", "secondary_button", "cancel_button",
                 "copy_button", "download_button", "status_display", "status_indicator", "timer_label",
                 "search_input", "search_icon", "result_title", "text_area", "queue_label", "queue_progress")
        for theme in ("light", "dark"):
            window.set_theme(theme)
            for name in names:
                with self.subTest(theme=theme, control=name):
                    self.assertGreater(len(getattr(window, name).toolTip()), 20)
            for name in ("settings_button", "primary_button", "secondary_button", "cancel_button", "copy_button", "download_button"):
                self.assertTrue(getattr(window, name).accessibleName(), name)

    def test_folder_refine_and_editor_help_survives_refresh(self):
        """Dynamic labels must retain instructions rather than replace them with short captions."""
        from widgets.settings_help import HELP
        window = self.harness.window()
        folder_name = "Long folder name " * 4
        folder = self.fixture.folder(folder_name)
        self.controller.select_folder(folder)
        window.folder_selector.resize(100, 36)
        window.folder_selector._update_elided_text()
        self.assertIn(HELP["destination"], window.folder_selector.toolTip())
        self.assertIn(folder_name.strip(), window.folder_selector.toolTip())
        row = window.post_process_toggle
        row.resize(120, 36)
        row._update_elided_label()
        self.assertEqual(row.toggle.toolTip(), HELP["refine"])
        self.assertEqual(row.text_label.toolTip(), HELP["refine"])
        path = self.fixture.notes.create_transcription_note("Result", 1, source_name="meeting.wav")
        self.assertIn(HELP["editor"], window.text_area.toolTip())
        self.assertIn("meeting.wav", window.text_area.toolTip())
        self.controller.select_note(path)
        self.assertIn(HELP["editor"], window.text_area.toolTip())
        window.reset_editor_state()
        self.assertNotIn("meeting.wav", window.text_area.toolTip())

    def test_settings_buttons_labels_and_fields_have_help(self):
        """Check the real Settings tree, including headers and manually created field labels."""
        from PyQt6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QTextEdit, QComboBox, QAbstractSpinBox
        from widgets.settings_dialog import SettingsDialog
        window = self.harness.window()
        dialog = SettingsDialog(window, theme=window.theme)
        for theme in ("light", "dark"):
            window.set_theme(theme)
            dialog.update_theme(window.theme)
            for widget in dialog.findChildren((QAbstractButton, QLabel, QLineEdit, QTextEdit, QComboBox, QAbstractSpinBox)):
                # Qt's own embedded line edits and popup controls inherit field help.
                if widget.objectName().startswith("qt_"):
                    continue
                text = widget.text() if isinstance(widget, (QAbstractButton, QLabel)) else type(widget).__name__
                if not text:
                    continue
                with self.subTest(theme=theme, control=text[:80]):
                    self.assertIsNotNone(dialog.interactions._tooltip_target(widget), text)
        dialog.close()

    def test_stopping_recording_disables_stop_pause_and_explains_state(self):
        """Stop/pause stay unavailable while WAV finalization is pending, even after a theme change."""
        window = self.harness.window()
        recording = self.controller.recording_controller
        for state, secondary in (("recording", "Pause"), ("paused", "Resume")):
            recording.state = state
            window._update_ui_for_state()
            self.assertIn("Stop", window.primary_button.toolTip())
            self.assertIn(secondary, window.secondary_button.toolTip())
        recording.is_stopping = True
        window._on_recording_stop_requested()
        for theme in ("light", "dark"):
            window.set_theme(theme)
            self.assertFalse(window.primary_button.isEnabled())
            self.assertFalse(window.secondary_button.isEnabled())
            self.assertIn("finishing", window.primary_button.toolTip().lower())
        recording.state, recording.is_stopping = "idle", False

    def test_queue_item_tooltips_are_not_swallowed(self):
        """The shared widget filter must allow the view to display per-item error details."""
        from PyQt6.QtGui import QHelpEvent
        from PyQt6.QtWidgets import QApplication, QToolTip
        window = self.harness.window()
        window._render_batch(dict(rows=[dict(name="source.wav", status="Failed", error="Fixture error")],
                                  index=1, total=1, done=1, units=1, running=False, stopping=False, phase="Completed"))
        self.fixture.app.processEvents()
        viewport = window.queue_list.viewport()
        point = window.queue_list.visualItemRect(window.queue_list.item(0)).center()
        event = QHelpEvent(QEvent.Type.ToolTip, point, viewport.mapToGlobal(point))
        self.assertFalse(window.interactions.eventFilter(viewport, event))
        self.assertIn("Fixture error", window.queue_list.item(0).toolTip())
        QApplication.sendEvent(viewport, event)
        self.assertIn("Fixture error", QToolTip.text())
        QToolTip.hideText()

    def test_actual_tooltips_use_active_theme(self):
        """Shown help, not just its stored string, must remain readable in both themes."""
        from PyQt6.QtGui import QPalette
        from PyQt6.QtWidgets import QApplication, QToolTip
        window = self.harness.window()
        for theme in ("light", "dark"):
            window.set_theme(theme)
            window.interactions.target = window.primary_button
            with patch("widgets.themed_controls.QApplication.widgetAt", return_value=window.primary_button):
                window.interactions._show()
            self.fixture.app.processEvents()
            tips = [widget for widget in QApplication.topLevelWidgets()
                    if widget.objectName() == "qtooltip_label" and widget.isVisible()]
            self.assertEqual(len(tips), 1)
            tip = tips[0]
            self.assertEqual(tip.palette().color(QPalette.ColorRole.WindowText).name(), window.theme["DARK_TEXT"])
            self.assertEqual(tip.palette().color(QPalette.ColorRole.Window).name(), window.theme["SIDEBAR_BG"])
            output = Path(__file__).resolve().parents[1] / "test_results" / f"ui_help_tooltip_{theme}.png"
            self.assertTrue(tip.grab().save(str(output)))
            QToolTip.hideText()
            window.interactions.cancel()

    def test_context_actions_and_auxiliary_dialogs_have_help(self):
        """Menu actions, icon selection and About controls retain meaningful explanations."""
        from PyQt6.QtWidgets import QAbstractButton
        from widgets.icon_picker import IconPicker
        from widgets.about_dialog import AboutDialog
        window = self.harness.window()
        path = self.fixture.notes.create_transcription_note("Test", 1)
        menu = window._cards_by_filepath[path].create_context_menu()
        def check_menu(current):
            self.assertTrue(current.toolTipsVisible())
            for action in current.actions():
                if not action.isSeparator():
                    self.assertNotEqual(action.toolTip(), action.text())
                    self.assertTrue(action.toolTip())
                    if action.menu():
                        check_menu(action.menu())
        check_menu(menu)
        check_menu(window._tray_menu)
        for dialog in (IconPicker("folder", "#64748b", window.theme, window), AboutDialog(window, window.theme)):
            for button in dialog.findChildren(QAbstractButton):
                self.assertTrue(button.toolTip(), button.text())
            dialog.close()

    def test_batch_release_after_each_file_and_lazy_reload(self):
        """The explicit per-transcription release policy applies to every batch file."""
        self.batch_engine()
        self.config.set_model_memory_mode("release_after_transcription")
        phases = QSignalSpy(self.controller.runtime_phase_changed)
        self.controller.start_file_batch(["a.wav", "b.wav"])
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual(self.events, ["transcribe", "unload-start", "unload-end", "load", "transcribe", "unload-start", "unload-end"])
        self.assertIn(["Loading model"], phases)
        self.assertEqual(self.controller.model_memory_status(), "Model unloaded")

    def test_batch_failure_keeps_original_and_never_retries(self):
        """A provider failure preserves raw segments and permits the next explicit queue item."""
        engine = self.batch_engine()
        self.controller.refine_enabled.return_value = True
        provider = Mock()
        provider.process.side_effect = [RuntimeError("fixture timeout"), "success"]
        self.controller.post_processing.provider = Mock(return_value=provider)
        self.controller.start_file_batch(["a.wav", "b.wav"])
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        rows = self.controller.batch.rows
        self.assertEqual([r["status"] for r in rows], ["Failed", "Completed"])
        first = self.fixture.repo.read_note(rows[0]["path"])
        self.assertEqual(first["raw_text"], first["full_text"])
        self.assertEqual(first["segments"], engine.segments)
        self.assertFalse(self.fixture.notes.has_refined_version(first))
        self.assertEqual(provider.process.call_count, 2)

    def test_batch_snapshot_and_conflicting_actions(self):
        """Later config changes cannot affect captured language, options or destination."""
        import threading
        engine = self.batch_engine()
        gate = threading.Event()
        self.addCleanup(gate.set)
        folder = self.fixture.folder()
        self.controller.select_folder(folder)
        seen = []
        def transcribe(path, language, options):
            gate.wait(2)
            seen.append((language, options.temperature))
            return {"text": "source", "duration": 1}
        engine.transcribe = transcribe
        self.controller.start_file_batch(["a.wav", "b.wav"])
        self.assertFalse(self.controller.unload_model_now())
        self.assertFalse(self.controller.load_model_now())
        self.assertFalse(self.controller.start_file_batch(["other.wav"]))
        self.config.set_language("en")
        self.controller.select_folder("inbox")
        gate.set()
        self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
        self.assertEqual([item[0] for item in seen], ["ru", "ru"])
        self.assertEqual(len(self.fixture.repo.load_notes(folder)), 2)
        self.assertEqual(len(self.fixture.repo.load_notes("inbox")), 0)

    def test_async_catalog_refresh_preserves_model_and_ui_responsiveness(self):
        """A gated fake catalog keeps Qt responsive and never calls completion."""
        import threading
        from widgets.settings_panel import PostProcessingPanel
        from PyQt6.QtCore import QTimer
        gate = threading.Event()
        panel = PostProcessingPanel(self.controller)
        self.addCleanup(panel.deleteLater)
        provider = Mock()
        provider.list_models.side_effect = lambda: (gate.wait(2) and [{"id": "vendor/id", "name": "Name"}])
        panel.provider.setCurrentIndex(panel.provider.findData("local"))
        panel.model.setText("existing-id")
        ticks = []
        try:
            with patch.object(self.controller.post_processing, "catalog_provider", return_value=provider):
                panel._refresh_models()
                QTimer.singleShot(0, lambda: ticks.append(True))
                self.fixture.wait_for(lambda: bool(ticks))
                self.assertFalse(panel.refresh_models.isEnabled())
                gate.set()
                self.fixture.wait_for(lambda: not self.controller.has_active_jobs())
            self.assertEqual(panel.model.text(), "existing-id")
            self.assertEqual(panel.model.itemData(0), "vendor/id")
            provider.process.assert_not_called()
        finally:
            gate.set()

    def test_export_collision_is_not_overwritten_without_consent(self):
        """Publication refuses a destination created after the initial existence check."""
        service = self.controller.export_service
        path = self.root / "race.txt"
        exporter = service.EXPORTERS["txt"]
        render = exporter.render
        def race(instance, note):
            path.write_text("other writer", encoding="utf-8")
            return render(instance, note)
        with patch.object(exporter, "render", race):
            with self.assertRaises(FileExistsError):
                service.export({"full_text": "new"}, path, "txt")
        self.assertEqual(path.read_text(encoding="utf-8"), "other writer")

    def test_single_instance_lock_without_real_ipc(self):
        """Exercise real file ownership with a mocked notification transport, not a real IPC claim."""
        first, second = self.instance(), self.instance()
        with patch.object(first.server, "listen", return_value=True):
            self.assertTrue(first.acquire_or_activate())
        fake_socket = Mock()
        fake_socket.waitForConnected.return_value = True
        with patch("core.single_instance.QLocalSocket", return_value=fake_socket):
            self.assertFalse(second.acquire_or_activate())
        fake_socket.write.assert_called_once_with(b"activate\n")
        first.close()
        with patch.object(second.server, "listen", return_value=True):
            self.assertTrue(second.acquire_or_activate())

    def test_real_window_render_artifacts(self):
        """Capture both themes with synthetic notes/queue data for layout inspection."""
        from widgets.settings_dialog import SettingsDialog
        self.batch_engine()
        window = self.harness.window()
        path = self.fixture.notes.create_transcription_note("Original fixture", 4)
        window._on_processing_finished()
        self.controller.save_refined_note(path, "Refined fixture")
        window._render_batch({"phase": "Completed", "index": 2, "total": 2, "done": 4, "units": 4,
                              "running": False, "stopping": False, "rows": [
                                  {"name": "meeting.ogg", "status": "Completed", "error": ""},
                                  {"name": "interview.mp3", "status": "Completed", "error": ""}]})
        directory = Path(__file__).resolve().parents[1] / "test_results"
        for theme in ("light", "dark"):
            window.set_theme(theme)
            self.fixture.app.processEvents()
            self.assertTrue(window.grab().save(str(directory / f"iteration_main_{theme}.png")))
            dialog = SettingsDialog(window, theme=window.theme)
            try:
                dialog.show()
                dialog.tab_widget.setCurrentIndex(1)
                self.fixture.app.processEvents()
                self.assertTrue(dialog.grab().save(str(directory / f"iteration_post_{theme}.png")))
                dialog.tab_widget.setCurrentIndex(3)
                self.fixture.app.processEvents()
                from PyQt6.QtWidgets import QScrollArea
                scroll = next(area for area in dialog.findChildren(QScrollArea)
                              if area.isAncestorOf(dialog.load_model_button))
                scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                self.fixture.app.processEvents()
                self.assertTrue(dialog.grab().save(str(directory / f"iteration_memory_{theme}.png")))
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_stationary_hover_after_delete_and_rebuild(self):
        """The replacement card's delete button appears without a new enter event."""
        window = self.harness.window()
        self.fixture.notes.create_transcription_note("older", 1)
        first = self.fixture.notes.create_transcription_note("newer", 1)
        card = window._cards_by_filepath[first]
        QTest.qWait(30)
        point = card.mapToGlobal(card.rect().center())
        QCursor.setPos(point)
        self.fixture.notes.delete_note(first)
        QTest.qWait(50)
        replacement = next(iter(window._cards_by_filepath.values()))
        self.assertTrue(replacement.delete_btn.isVisible())
        self.fixture.notes.load_notes()
        QTest.qWait(50)
        self.assertTrue(next(iter(window._cards_by_filepath.values())).delete_btn.isVisible())

    def test_keyboard_ring_without_mouse_outline_both_themes(self):
        """Mouse focus has no native outline, while Tab retains an explicit ring."""
        window = self.harness.window()
        for theme in ("light", "dark"):
            window.set_theme(theme)
            for button in (window.settings_button, window.primary_button, window.secondary_button, window.copy_button):
                QCoreApplication.sendEvent(button, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason))
                self.assertFalse(button.property("keyboardFocus"))
                self.assertIn("outline: none", button.styleSheet())
                self.assertNotEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)
                QCoreApplication.sendEvent(button, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
                self.assertTrue(button.property("keyboardFocus"))
                self.assertIn("border: 2px solid", button.styleSheet())

    def test_single_instance_activates_existing_owner(self):
        """A second launch notifies the first and never becomes another owner."""
        first = self.instance()
        self.assertTrue(first.acquire_or_activate())
        activated = QSignalSpy(first.activated)
        self.assertFalse(self.instance().acquire_or_activate())
        self.fixture.wait_for(lambda: len(activated) == 1)

    def test_single_instance_independent_roots_and_stale_file(self):
        """A leftover file is not a held OS lock, and portable copies stay independent."""
        (self.root / ".feinnote.lock").write_bytes(b"old stale lock")
        first = self.instance()
        second = self.instance(self.root / "other")
        self.assertTrue(first.acquire_or_activate())
        self.assertTrue(second.acquire_or_activate())
        self.assertNotEqual(first.name, second.name)
        first.close()
        self.assertTrue(self.instance().acquire_or_activate())

    def test_single_instance_crash_releases_os_lock(self):
        """Abrupt process death releases ownership without deleting a stale file."""
        code = ("import os,sys; from PyQt6.QtCore import QCoreApplication; "
                "from core.single_instance import SingleInstance; app=QCoreApplication([]); "
                "guard=SingleInstance(sys.argv[1]); assert guard.acquire_or_activate(); os._exit(0)")
        subprocess.run([sys.executable, "-c", code, str(self.root)], check=True, timeout=15)
        self.assertTrue(self.instance().acquire_or_activate())
