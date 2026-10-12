# Responsible for: real dialog contrast regressions independent of the Windows palette.

import unittest
from pathlib import Path

from tests import test_application as application
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QTextEdit, QVBoxLayout

from utils.themes import dark_theme, light_theme, dialog_palette
from widgets.about_dialog import AboutDialog
from widgets.icon_picker import IconPicker
from widgets.settings_dialog import SettingsDialog


class DialogThemeTests(unittest.TestCase):
    """Inspect displayed Qt widgets, including the actual single-note confirmation path."""

    @classmethod
    def setUpClass(cls):
        """Reuse the suite's offscreen QApplication."""
        application.ApplicationTests.setUpClass()

    def setUp(self):
        """Create an isolated application and deliberately mismatched OS colors."""
        self.harness = application.ApplicationTests()
        self.harness.setUp()
        self.fixture = self.harness.fixture
        self.app = self.fixture.app
        self.old_palette = self.app.palette()
        palette = QPalette(self.old_palette)
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        self.app.setPalette(palette)
        self.window = self.harness.window()

    def tearDown(self):
        """Close test dialogs before destroying their owner and restore the OS palette."""
        for dialog in self.window.findChildren(QDialog):
            dialog.close()
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()
            self.app.setPalette(self.old_palette)

    def assert_colors(self, dialog, theme):
        """Check actual rendered-widget palette roles against active theme tokens."""
        self.assertEqual(dialog.palette().color(QPalette.ColorRole.Window).name(), theme["WHITE_BG"])
        for label in dialog.findChildren(QLabel):
            if label.text():
                self.assertEqual(label.palette().color(QPalette.ColorRole.WindowText).name(), theme["DARK_TEXT"])
        for editor in dialog.findChildren(QTextEdit):
            self.assertEqual(editor.palette().color(QPalette.ColorRole.Text).name(), theme["DARK_TEXT"])
            self.assertEqual(editor.palette().color(QPalette.ColorRole.Base).name(), theme["WHITE_BG"])

    def capture_modal(self, action):
        """Run a real modal action and cancel it after capturing its visible message box."""
        captured = []

        def dismiss():
            boxes = [box for box in self.window.findChildren(QMessageBox) if box.isVisible()]
            if boxes:
                box = boxes[-1]
                captured.append(box)
                no = box.button(QMessageBox.StandardButton.No)
                (no or box.buttons()[0]).click()

        timer = QTimer(self.window)
        timer.setInterval(10)
        timer.timeout.connect(dismiss)
        timer.start()
        try:
            action()
        finally:
            timer.stop()
        self.assertEqual(len(captured), 1)
        return captured[0]

    def check_note_confirmation(self, name, theme):
        """Verify the real trash action without deleting the fixture note."""
        self.window.set_theme(name)
        path = self.fixture.notes.create_transcription_note("Confirm color fixture", 1)
        card = self.window._cards_by_filepath[path]
        # Static QMessageBox helpers destroy their dialog on return; capture
        # effective colors while it is alive instead of keeping its wrapper.
        snapshots = []
        timer = QTimer(self.window)
        timer.setInterval(10)

        def inspect():
            for box in self.window.findChildren(QMessageBox):
                if box.isVisible():
                    snapshots.append((box.palette().color(QPalette.ColorRole.Window).name(),
                                      [label.palette().color(QPalette.ColorRole.WindowText).name()
                                       for label in box.findChildren(QLabel) if label.text()], box.styleSheet()))
                    box.button(QMessageBox.StandardButton.No).click()

        timer.timeout.connect(inspect)
        timer.start()
        try:
            card.delete_btn.click()
        finally:
            timer.stop()
        self.assertEqual(len(snapshots), 1)
        background, foregrounds, style = snapshots[0]
        self.assertEqual(background, theme["WHITE_BG"])
        self.assertTrue(foregrounds)
        self.assertEqual(set(foregrounds), {theme["DARK_TEXT"]})
        self.assertIn(theme["DARK_TEXT"], style)
        self.assertTrue(Path(path).exists())

    def test_note_delete_confirmation_light(self):
        """Light note deletion stays dark even when the OS supplies white text."""
        self.check_note_confirmation("light", light_theme)

    def test_note_delete_confirmation_dark(self):
        """Dark note deletion uses the light foreground from the active dark theme."""
        self.check_note_confirmation("dark", dark_theme)

    def test_message_types_details_and_live_theme_switch(self):
        """Questions, warnings, errors and info update primary/secondary/detail text live."""
        settings = SettingsDialog(self.window, theme=self.window.theme)
        settings.show()
        for icon in (QMessageBox.Icon.Question, QMessageBox.Icon.Warning,
                     QMessageBox.Icon.Critical, QMessageBox.Icon.Information):
            box = QMessageBox(settings)
            box.setIcon(icon)
            box.setText("Primary warning text")
            box.setInformativeText("Secondary recovery guidance")
            box.setDetailedText("Diagnostic Unicode: Ошибка")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.show()
            for name, theme in (("dark", dark_theme), ("light", light_theme)):
                self.window.set_theme(name)
                self.app.processEvents()
                self.assert_colors(box, theme)
                for button in box.buttons():
                    self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text().replace("&", "")) + 32)
            box.close()

    def test_tokens_not_fixed_colors_and_destructive_style_preserved(self):
        """Nonstandard theme tokens reach palettes and the destructive button style."""
        theme = dict(light_theme, WHITE_BG="#edeee4", DARK_TEXT="#314252", STATUS_ERROR="#a12030")
        self.window.theme = theme
        self.window._apply_theme()
        box = QMessageBox(self.window)
        box.setText("Token verification")
        button = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        button.setProperty("danger", True)
        box.show()
        self.app.processEvents()
        self.assert_colors(box, theme)
        self.assertEqual(button.palette().color(QPalette.ColorRole.ButtonText).name(), theme["STATUS_ERROR"])
        self.assertEqual(button.palette().color(QPalette.ColorRole.Button).name(), theme["SIDEBAR_BG"])
        box.close()

    def test_error_dialog_uses_shared_style(self):
        """The existing detailed error entry point has no separate stale styling."""
        self.window.set_theme("light")
        box = self.capture_modal(lambda: self.window.show_error_dialog("Error", "Main", "Recovery", "Details"))
        self.assert_colors(box, light_theme)
        self.assertIn("QLabel", box.styleSheet())

    def test_custom_dialog_palette_and_theme_switch(self):
        """Plain custom confirmations inherit current palette roles on show and switch."""
        dialog = QDialog(self.window)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Custom confirmation"))
        layout.addWidget(QTextEdit("Custom details"))
        dialog.show()
        for name, theme in (("dark", dark_theme), ("light", light_theme)):
            self.window.set_theme(name)
            self.app.processEvents()
            self.assert_colors(dialog, theme)

    def test_about_and_icon_picker_update_while_open(self):
        """Other small custom windows refresh stale explicit styles on theme changes."""
        about = AboutDialog(self.window, light_theme)
        picker = IconPicker("folder", "#268bd2", light_theme, self.window)
        about.show()
        picker.show()
        for name, theme in (("dark", dark_theme), ("light", light_theme)):
            self.window.set_theme(name)
            self.app.processEvents()
            self.assert_colors(about, theme)
            self.assertEqual(picker.palette().color(QPalette.ColorRole.Window).name(), theme["WHITE_BG"])
            self.assertIn(theme["DARK_TEXT"], picker.styleSheet())

    def test_palette_all_groups_and_unrelated_window_untouched(self):
        """Inactive windows remain readable and the manager is scoped to its owner."""
        for theme in (light_theme, dark_theme):
            palette = dialog_palette(theme)
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
                self.assertEqual(palette.color(group, QPalette.ColorRole.WindowText).name(), theme["DARK_TEXT"])
                self.assertEqual(palette.color(group, QPalette.ColorRole.Window).name(), theme["WHITE_BG"])
        unrelated = QMessageBox()
        unrelated.setText("Unrelated window")
        unrelated.show()
        self.app.processEvents()
        self.assertEqual(unrelated.styleSheet(), "")
        unrelated.close()
        unrelated.deleteLater()
