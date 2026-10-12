# Responsible for: regression coverage of note preferences, export formats and their UI routes.

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from tests import test_application as application
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QMessageBox, QScrollArea

from core.config_manager import ConfigManager
from core.exporter.service import ExportService
from core.exporter.formats.subtitles import UNAVAILABLE
from core.notes.notes_repository import NotesRepository
from widgets.export_dialog import FILTERS, choose_export_destination
from widgets.settings_dialog import SettingsDialog
from widgets.toggle_switch import ToggleSwitch


class UIExportTests(unittest.TestCase):
    """Use isolated project-local notes, fake devices, and real export serializers."""

    @classmethod
    def setUpClass(cls):
        """Reuse the suite's offscreen Qt application."""
        application.ApplicationTests.setUpClass()

    def setUp(self):
        """Create a fresh note repository and application controller for each test."""
        self.harness = application.ApplicationTests()
        self.harness.setUp()
        self.fixture = self.harness.fixture
        self.controller = self.harness.controller
        self.config = self.harness.config
        self.root = self.harness.root
        self.service = ExportService()
        self.segments = [
            {"start": 1.234, "end": 3.567, "text": " Первый вопрос?"},
            {"start": 3661.006, "end": 3664.999, "text": "Вторая строка.\nПродолжение."},
        ]
        self.note = {"full_text": "Отредактированный текст — финал.", "raw_text": "Первый вопрос? Вторая строка.",
                     "segments": self.segments}
        self.dialogs = []

    def tearDown(self):
        """Close test windows and finish workers before removing owned fixtures."""
        for dialog in self.dialogs:
            dialog.close()
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()

    def dialog(self):
        """Return a visible real Settings dialog with isolated dependencies."""
        window = self.harness.window()
        dialog = SettingsDialog(window, theme=window.theme)
        self.dialogs.append(dialog)
        dialog.show()
        self.fixture.app.processEvents()
        return dialog

    def test_config_transient_windows_lock_retry_is_bounded(self):
        """Short Windows locks recover; persistent failures preserve disk and preference."""
        import os

        self.config.set_confirm_note_deletion(True)
        error = PermissionError("Temporary file lock")
        error.winerror = 5
        replace = os.replace
        attempts = []

        def transient_replace(source, destination):
            attempts.append(source)
            if len(attempts) < 3:
                raise error
            return replace(source, destination)

        with patch("core.config_manager.os.replace", side_effect=transient_replace), patch("core.config_manager.time.sleep"):
            self.config.set_confirm_note_deletion(False)
        self.assertEqual(len(attempts), 3)
        self.assertFalse(ConfigManager(str(self.root / "config.json")).get_confirm_note_deletion())
        with patch("core.config_manager.os.replace", side_effect=error) as mocked, patch("core.config_manager.time.sleep"):
            with self.assertRaises(PermissionError):
                self.config.set_confirm_note_deletion(True)
        self.assertEqual(mocked.call_count, 5)
        self.assertFalse(self.config.get_confirm_note_deletion())
        self.assertFalse(ConfigManager(str(self.root / "config.json")).get_confirm_note_deletion())
        self.assertEqual(list(self.root.glob("config_*.json")), [])

    def test_condition_toggle_persists_both_values(self):
        """The reused pill preserves boolean options and unrelated decoding thresholds."""
        dialog = self.dialog()
        self.assertIsInstance(dialog.adv_condition, ToggleSwitch)
        original = self.config.get_advanced_transcription_options()
        for value in (False, True):
            dialog.adv_condition.setChecked(value)
            saved = ConfigManager(str(self.root / "config.json")).get_advanced_transcription_options()
            self.assertEqual(saved["condition_on_previous_text"], value)
            self.assertEqual(saved["no_speech_threshold"], original["no_speech_threshold"])

    def test_confirmation_default_and_legacy_config(self):
        """Missing or malformed preferences retain the previous safe confirmation behavior."""
        self.assertTrue(self.config.get_confirm_note_deletion())
        for settings in ({"active_folder": "inbox"}, {"confirm_note_deletion": "false"}):
            path = self.root / "legacy_config.json"
            path.write_text(json.dumps({"notes": settings}), encoding="utf-8")
            before = path.read_bytes()
            self.assertTrue(ConfigManager(str(path)).get_confirm_note_deletion())
            self.assertEqual(path.read_bytes(), before)

    def test_confirmation_toggle_persists_across_folder_selection(self):
        """Saving a folder choice does not discard the new note preference."""
        dialog = self.dialog()
        self.assertIsInstance(dialog.notes_panel.confirm_deletion, ToggleSwitch)
        dialog.notes_panel.confirm_deletion.setChecked(False)
        folder = self.fixture.folder()
        self.controller.select_folder(folder)
        loaded = ConfigManager(str(self.root / "config.json"))
        self.assertFalse(loaded.get_confirm_note_deletion())
        self.assertEqual(loaded.get_section("notes")["active_folder"], folder)
        dialog.notes_panel.confirm_deletion.setChecked(True)
        self.assertTrue(ConfigManager(str(self.root / "config.json")).get_confirm_note_deletion())
        self.assertEqual(self.config.get_section("notes")["active_folder"], folder)

    def test_confirmation_failed_save_restores_switch(self):
        """A storage error restores both the visible control and saved preference."""
        dialog = self.dialog()
        with patch.object(self.config, "_save", side_effect=OSError("disk full")):
            dialog.notes_panel.confirm_deletion.setChecked(False)
        self.assertTrue(dialog.notes_panel.confirm_deletion.isChecked())
        self.assertTrue(self.config.get_confirm_note_deletion())

    def test_delete_on_uses_confirmation_for_trash_and_context(self):
        """Both ordinary delete entry points honor No and only delete after Yes."""
        window = self.harness.window()
        for route in ("trash", "context"):
            path = self.fixture.notes.create_transcription_note("Delete fixture", 2)
            card = window._cards_by_filepath[path]
            menu = card.create_context_menu()
            action = next(action for action in menu.actions() if action.text() == "Delete note")
            click = card.delete_btn.click if route == "trash" else action.trigger
            with patch("app_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question:
                click()
                question.assert_called_once()
            self.assertTrue(Path(path).exists())
            with patch("app_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                click()
            self.assertFalse(Path(path).exists())
            menu.deleteLater()

    def test_delete_off_is_immediate_for_trash_and_context(self):
        """The disabled preference bypasses confirmation for either single-note action."""
        window = self.harness.window()
        self.controller.set_confirm_note_deletion(False)
        for route in ("trash", "context"):
            path = self.fixture.notes.create_transcription_note("Delete fixture", 2)
            card = window._cards_by_filepath[path]
            menu = card.create_context_menu()
            action = next(action for action in menu.actions() if action.text() == "Delete note")
            with patch("app_window.QMessageBox.question") as question:
                (card.delete_btn.click if route == "trash" else action.trigger)()
                question.assert_not_called()
            self.assertFalse(Path(path).exists())
            menu.deleteLater()

    def test_bulk_cleanup_still_confirms_when_single_confirmation_off(self):
        """The preference never suppresses confirmation of mass cleanup."""
        dialog = self.dialog()
        path = self.fixture.note("Keep until confirmed")
        self.controller.set_confirm_note_deletion(False)
        with patch("widgets.settings_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question:
            dialog.notes_panel._cleanup("all")
            question.assert_called_once()
        self.assertTrue(Path(path).exists())

    def test_transcription_layout_and_about_fit_collapsed(self):
        """Normal Settings shows Advanced without scroll and keeps compact About content."""
        dialog = self.dialog()
        scroll = dialog.stack.widget(0).findChild(QScrollArea)
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
        self.assertTrue(dialog.adv_toggle_btn.isVisible())
        dialog.adv_toggle_btn.click()
        QTest.qWait(30)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        dialog.segmented.setCurrentIndex(4)
        self.fixture.app.processEvents()
        self.assertEqual(dialog.segmented._buttons[4].text(), "About")
        self.assertFalse(dialog.stack.widget(4).findChildren(QScrollArea))
        self.assertGreaterEqual(dialog.github_btn.y(), 0)
        self.assertLessEqual(dialog.github_btn.geometry().bottom(), dialog.stack.widget(4).height())

    def test_advanced_toggle_has_themed_frame_before_first_click(self):
        """Advanced starts as a framed button and keeps its appearance after mouse focus."""
        window = self.harness.window()
        for theme in ("light", "dark"):
            window.set_theme(theme)
            dialog = SettingsDialog(window, theme=window.theme)
            self.dialogs.append(dialog)
            dialog.show()
            QTest.qWait(30)
            button = dialog.adv_toggle_btn
            for expanded in (False, True, False):
                with self.subTest(theme=theme, expanded=expanded):
                    self.assertEqual(not dialog.adv_content.isHidden(), expanded)
                    dialog.engine_selector.setFocus(Qt.FocusReason.OtherFocusReason)
                    self.fixture.app.processEvents()
                    rendered = button.grab().toImage()
                    # Sample the fill and straight top border away from text/icons.
                    x = rendered.width() - round(20 * rendered.devicePixelRatio())
                    self.assertEqual(rendered.pixelColor(x, rendered.height() // 2).name(), window.theme["SIDEBAR_BG"])
                    self.assertEqual(rendered.pixelColor(x, 0).name(), window.theme["BORDER"])
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                QTest.qWait(30)
            dialog.close()

    def test_transcription_width_stays_fixed_when_advanced_expands(self):
        """Scrollbar appearance must not shift or resize transcription fields."""
        window = self.harness.window()
        for theme in ("light", "dark"):
            window.set_theme(theme)
            dialog = SettingsDialog(window, theme=window.theme)
            self.dialogs.append(dialog)
            dialog.show()
            QTest.qWait(30)
            scroll = dialog.stack.widget(0).findChild(QScrollArea)
            controls = (dialog.engine_selector, dialog.model_selector, dialog.device_selector,
                        dialog.language_combo_box, dialog.adv_toggle_btn)
            def horizontal_geometry():
                return [(control.mapTo(dialog, QPoint()).x(), control.width()) for control in controls]
            baseline = horizontal_geometry()
            viewport_width = scroll.viewport().width()
            for expanded in (False, True, False, True):
                with self.subTest(theme=theme, expanded=expanded):
                    if expanded == dialog.adv_content.isHidden():
                        dialog.adv_toggle_btn.click()
                    QTest.qWait(30)
                    self.assertEqual(horizontal_geometry(), baseline)
                    self.assertEqual(scroll.viewport().width(), viewport_width)
                    self.assertEqual(scroll.verticalScrollBar().maximum() > 0, expanded)
            # Bottom controls remain reachable and the horizontal scrollbar is unused.
            scroll.ensureWidgetVisible(dialog.adv_reset_btn)
            self.fixture.app.processEvents()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertTrue(scroll.viewport().rect().contains(
                dialog.adv_reset_btn.mapTo(scroll.viewport(), dialog.adv_reset_btn.rect().center())))
            dialog.close()

    def test_postprocessing_sections_preserve_values(self):
        """The two headings group the same provider/refinement fields without changing values."""
        dialog = self.dialog()
        panel = dialog.post_panel
        headings = [label.text() for label in panel.findChildren(QLabel) if label.objectName() == "section_title"]
        self.assertEqual(headings, ["Provider & Model", "Refinement Parameters"])
        self.assertEqual(panel.prompt.toPlainText(), self.controller.get_post_settings()["prompt"])
        panel.provider.setCurrentIndex(panel.provider.findData("local"))
        self.assertTrue(panel.connection.isEnabled())
        self.assertTrue(panel.refinement.isEnabled())

    def test_txt_unicode_and_exact_whitespace(self):
        """TXT exports the final Unicode content, including intentional blank lines."""
        text = "Привет, мир! Ёж — café.\n\n  Строка\n"
        path = self.service.export({"full_text": text}, self.root / "unicode", "txt")
        self.assertEqual(path.read_bytes(), text.encode("utf-8"))

    def test_docx_is_openxml_with_paragraphs_and_breaks(self):
        """The DOCX package has valid relationships and genuine Unicode Word paragraphs."""
        text = "Первый & <текст>\nВторая строка\n\nНовый абзац\tтабуляция"
        path = self.service.export({"full_text": text}, self.root / "document", "docx")
        with ZipFile(path) as package:
            self.assertIsNone(package.testzip())
            for name in package.namelist():
                ET.fromstring(package.read(name))
            root = ET.fromstring(package.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            self.assertEqual(len(root.findall(".//w:p", ns)), 2)
            self.assertEqual(len(root.findall(".//w:br", ns)), 1)
            self.assertEqual(len(root.findall(".//w:tab", ns)), 1)
            self.assertEqual([item.text for item in root.findall(".//w:t", ns)],
                             ["Первый & <текст>", "Вторая строка", "Новый абзац", "табуляция"])
            self.assertIn(b"officeDocument", package.read("_rels/.rels"))

    def test_pdf_unicode_wrapping_and_multiple_pages(self):
        """Qt produces a readable multipage PDF with selectable Russian text and visible glyphs."""
        text = "Привет, ёж! Русский текст — проверка.\n" + ("Длинная строка с переносом слов и пунктуацией. " * 12 + "\n") * 35 + "Конец документа."
        path = self.service.export({"full_text": text}, self.root / "unicode", "pdf")
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        document = QPdfDocument(None)
        self.assertEqual(document.load(str(path)), QPdfDocument.Error.None_)
        try:
            self.assertGreater(document.pageCount(), 1)
            self.assertIn("Привет, ёж!", document.getAllText(0).text())
            self.assertIn("Конец документа.", document.getAllText(document.pageCount() - 1).text())
            image = document.render(0, QSize(595, 842))
            self.assertFalse(image.isNull())
            pixels = image.constBits().asstring(image.sizeInBytes())
            self.assertGreater(len(set(pixels)), 1)
        finally:
            document.close()

    def test_srt_uses_original_text_and_exact_timestamps(self):
        """Final refined prose never replaces or redistributes timed source segments."""
        path = self.service.export(self.note, self.root / "source", "srt")
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "1\n00:00:01,234 --> 00:00:03,567\nПервый вопрос?\n\n"
                         "2\n01:01:01,006 --> 01:01:04,999\nВторая строка.\nПродолжение.\n\n")

    def test_vtt_has_header_and_real_cues(self):
        """WebVTT keeps source cue text and uses dot-separated millisecond ranges."""
        path = self.service.export(self.note, self.root / "source", "vtt")
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "WEBVTT\n\n00:00:01.234 --> 00:00:03.567\nПервый вопрос?\n\n"
                         "01:01:01.006 --> 01:01:04.999\nВторая строка.\nПродолжение.\n\n")

    def test_legacy_note_never_gets_fabricated_subtitles(self):
        """Reading an old note is nonmutating and exposes only document export formats."""
        path = self.root / "notes" / "legacy.json"
        path.write_text(json.dumps({"full_text": "Old text", "metadata": "old", "timestamp": 123}), encoding="utf-8")
        before = path.read_bytes()
        note = self.fixture.repo.read_note(str(path))
        self.assertEqual(note["full_text"], "Old text")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.service.available_formats(note), ("txt", "docx", "pdf"))
        for fmt in ("srt", "vtt"):
            with self.assertRaisesRegex(ValueError, "timestamps are unavailable"):
                self.service.export(note, self.root / "unavailable", fmt)
            self.assertFalse((self.root / f"unavailable.{fmt}").exists())

    def test_invalid_segments_are_not_enabled_or_exported(self):
        """Malformed, missing, negative and nonfinite timing never becomes generated cues."""
        for segments in (None, [], [{"text": "missing"}], [{"start": -1, "end": 2, "text": "bad"}],
                         [{"start": 1, "end": float("nan"), "text": "bad"}],
                         [{"start": 2, "end": 1, "text": "bad"}], [{"start": 0, "end": 1, "text": " "}]):
            with self.subTest(segments=segments):
                note = {"full_text": "Some text", "segments": segments}
                self.assertNotIn("srt", self.service.available_formats(note))
                with self.assertRaises(ValueError):
                    self.service.export(note, self.root / "bad", "srt")

    def test_backend_segments_survive_pipeline_reload_and_refinement(self):
        """Both real adapters pass mocked backend timestamps through note storage unchanged."""
        from core.transcription.engines.openai_whisper import OpenAIWhisperEngine
        from core.transcription.engines.faster_whisper import FasterWhisperEngine
        source = self.root / "audio.wav"
        source.write_bytes(b"mocked decoder fixture")
        openai = OpenAIWhisperEngine.__new__(OpenAIWhisperEngine)
        openai.model, openai.current_model_name = Mock(), "tiny"
        openai.model.transcribe.return_value = {"text": "Original", "segments": self.segments}
        with patch.object(openai, "_process_audio_before_transcription", return_value=(str(source), 3665)):
            first = openai.transcribe(str(source), "ru")
        faster = FasterWhisperEngine.__new__(FasterWhisperEngine)
        faster.model, faster.device = Mock(), "cpu"
        faster.model.transcribe.return_value = (iter(SimpleNamespace(**part) for part in self.segments), SimpleNamespace(duration=3665))
        second = faster.transcribe(str(source), "ru")
        for result in (first, second):
            self.controller._on_transcription_result(result)
            path = self.controller._raw_note_path
            self.controller._on_refined({"text": "Completely rewritten final text."})
            reloaded = NotesRepository(self.fixture.repo.notes_dir).read_note(path)
            self.assertEqual(reloaded["segments"], self.segments)
            self.assertEqual(reloaded["raw_text"], result["text"].strip())
            self.assertEqual(reloaded["full_text"], "Completely rewritten final text.")
            exported = self.service.export(reloaded, self.root / Path(path).stem, "srt")
            self.assertIn("Первый вопрос?", exported.read_text(encoding="utf-8"))
            self.assertNotIn("rewritten", exported.read_text(encoding="utf-8"))

    def test_extension_handling_and_existing_file_protection(self):
        """Extension normalization remains consistent and unapproved replacements fail."""
        for source, fmt, expected in (("note", "txt", "note.txt"), ("note.PDF", "pdf", "note.PDF"),
                                      ("note.txt", "docx", "note.docx"), ("note.v2", "vtt", "note.v2.vtt")):
            self.assertEqual(self.service.destination_path(source, fmt).name, expected)
        path = self.root / "existing.txt"
        path.write_text("Keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.service.export(self.note, path, "txt")
        self.assertEqual(path.read_text(encoding="utf-8"), "Keep")
        self.service.export(self.note, path, "txt", overwrite=True)
        self.assertEqual(path.read_text(encoding="utf-8"), self.note["full_text"])

    def test_normalized_destination_requires_overwrite_confirmation(self):
        """Adding a suffix cannot bypass confirmation for an existing target file."""
        path = self.root / "existing.pdf"
        path.write_bytes(b"keep")
        with patch("widgets.export_dialog.QFileDialog.getSaveFileName", return_value=(str(path.with_suffix("")), FILTERS["pdf"])):
            with patch("widgets.export_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question:
                self.assertIsNone(choose_export_destination(None, self.note, "note"))
                question.assert_called_once()
            with patch("widgets.export_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                self.assertEqual(choose_export_destination(None, self.note, "note"), (path, "pdf"))
        self.assertEqual(path.read_bytes(), b"keep")

    def test_context_export_actions_and_subtitle_availability(self):
        """The submenu offers all five formats and enables subtitles only with real cues."""
        window = self.harness.window()
        for segments in (None, self.segments):
            path = self.fixture.notes.create_transcription_note("Menu note", 4, segments=segments)
            card = window._cards_by_filepath[path]
            menu = card.create_context_menu()
            self.assertEqual([action.text() for action in menu.actions() if not action.isSeparator()],
                             ["Open", "Copy text", "Export as", "Move to folder", "Delete note"])
            submenu = next(action.menu() for action in menu.actions() if action.text() == "Export as")
            self.assertEqual([action.text() for action in submenu.actions()], ["TXT", "DOCX", "PDF", "SRT", "VTT"])
            self.assertEqual([action.isEnabled() for action in submenu.actions()], [True, True, True, bool(segments), bool(segments)])
            menu.deleteLater()

    def test_editor_and_card_share_export_with_unsaved_text(self):
        """Editor and context exports include draft final text while retaining source cues."""
        window = self.harness.window()
        path = self.fixture.notes.create_transcription_note("Original", 4, segments=self.segments)
        window.text_area.setPlainText("Unsaved final text — ё")
        for route in ("editor", "context"):
            destination = self.root / f"{route}.txt"
            with patch("app_window.choose_export_destination", return_value=(destination, "txt")), patch.object(self.controller.export_service, "export", wraps=self.controller.export_service.export) as export:
                if route == "editor":
                    window.save_text_to_file()
                else:
                    window._cards_by_filepath[path].export_requested.emit(path, "txt")
                export.assert_called_once()
            self.assertEqual(destination.read_text(encoding="utf-8"), "Unsaved final text — ё")
        inactive = self.fixture.note("Other stored note")
        self.assertEqual(self.controller.get_export_data(inactive, "Do not use editor here")["full_text"], "Other stored note")
        self.assertEqual(self.controller.get_export_data(path, "Draft")["segments"], self.segments)

    def test_legacy_editor_filters_and_explicit_subtitle_message(self):
        """Legacy save filters hide subtitles, and stale subtitle requests explain why."""
        legacy = {"full_text": "Legacy"}
        with patch("widgets.export_dialog.QFileDialog.getSaveFileName", return_value=("", "")) as save:
            self.assertIsNone(choose_export_destination(None, legacy, "legacy"))
            filters = save.call_args.args[3]
            self.assertIn("*.docx", filters)
            self.assertIn("*.pdf", filters)
            self.assertNotIn("*.srt", filters)
        with patch("widgets.export_dialog.QMessageBox.information") as information:
            self.assertIsNone(choose_export_destination(None, legacy, "legacy", "srt"))
            self.assertEqual(information.call_args.args[2], UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
