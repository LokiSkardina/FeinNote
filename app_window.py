# Responsible for: application widget assembly, visual rendering, and controller signal subscriptions.

"""Main View for FeinNote; all workflows are delegated to ``AppController``."""

import logging
import os
from datetime import datetime

from PyQt6.QtCore import QEvent, QSize, QTimer, Qt
from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QMainWindow,
    QMessageBox,
    QMenu,
    QSystemTrayIcon,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from controllers.app_controller import AppController
from utils.paths import get_resource_path
from utils.themes import dark_theme, light_theme, scrollbar_stylesheet, tooltip_stylesheet
from utils.icon_manager import get_icon, get_pixmap
from widgets.about_dialog import AboutDialog
from widgets.sidebar.history_card import HistoryCardWidget
from widgets.sidebar_controls import FolderDropdownButton, PostProcessRow, folder_icon
from widgets.settings_dialog import SettingsDialog
from widgets.export_dialog import choose_export_destination
from widgets.themed_controls import DialogThemeManager, ButtonFocusManager, SettingsInteractionManager
from widgets.settings_help import HELP, set_help


logger = logging.getLogger("WhisperNote.app")
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


class WhisperNoteApp(QMainWindow):
    """Build the application window and render state supplied by controllers."""

    def __init__(self) -> None:
        """Build widgets, subscribe to controller events, and start application work."""
        super().__init__()
        self._exit_requested = False
        self._shutdown_complete = False
        self._tray_icon = None
        self._tray_was_maximized = False
        self._hidden_in_tray = False
        self.setWindowTitle("FeinNote")
        self.setMinimumSize(1200, 800)

        icon_path = get_resource_path(os.path.join("assets", "icons", "app_icon.ico"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.app_controller = AppController()
        self.lang_name_to_code = self.app_controller.get_languages()
        self.lang_code_to_name = {
            language_code: display_name
            for display_name, language_code in self.lang_name_to_code.items()
        }
        self.current_theme_name = self.app_controller.get_selected_theme()
        self.theme = light_theme if self.current_theme_name == "light" else dark_theme

        self.settings_dialog: SettingsDialog | None = None
        self.active_note_card: HistoryCardWidget | None = None
        self._cards_by_filepath: dict[str, HistoryCardWidget] = {}
        self._loading_dot_count = 0
        self._base_loading_text = ""

        # The loading animation is a purely visual View timer.
        self.loading_timer = QTimer(self)
        self.loading_timer.timeout.connect(self._animate_loading_text)

        # Owned, restartable timers cannot outlive the View or compete on repeated feedback.
        self._copy_feedback_timer = QTimer(self)
        self._copy_feedback_timer.setSingleShot(True)
        self._copy_feedback_timer.setInterval(2000)
        self._copy_feedback_timer.timeout.connect(self._reset_copy_feedback)
        self._saved_feedback_timer = QTimer(self)
        self._saved_feedback_timer.setSingleShot(True)
        self._saved_feedback_timer.setInterval(1500)
        self._saved_feedback_timer.timeout.connect(self._reset_saved_feedback)

        self.load_fonts()
        self.init_ui()
        self.dialog_themes = DialogThemeManager(self, self.theme)
        self.button_focus = ButtonFocusManager(self)
        self.interactions = SettingsInteractionManager(self, exclude_dialogs=True)
        for widget, help_key in ((self.queue_panel, "batch"), (self.queue_stop, "batch_stop"),
                                 (self.download_button, "export_note"), (self.copy_button, "copy_displayed"),
                                 (self.post_process_toggle, "refine"), (self.original_button, "original"),
                                 (self.refined_button, "refined"), (self.app_title, "app"),
                                 (self.settings_button, "settings"), (self.cancel_button, "record_cancel"),
                                 (self.status_display, "status"), (self.status_indicator, "status"),
                                 (self.timer_label, "recording_time"), (self.search_input, "search"),
                                 (self.search_icon, "search"), (self.result_title, "editor"),
                                 (self.text_area, "editor"), (self.queue_label, "batch"),
                                 (self.queue_list, "batch"), (self.queue_progress, "batch")):
            set_help(widget, help_key)
        for widget, name in ((self.settings_button, "Settings"), (self.cancel_button, "Cancel recording"),
                             (self.copy_button, "Copy displayed text"), (self.download_button, "Export note")):
            widget.setAccessibleName(name)
        self._connect_controller_signals()
        self._setup_tray()
        self.app_controller.set_text_getter(self.text_area.toPlainText)
        self.app_controller.initialize()
        logger.info("WhisperNote View initialized")

    @property
    def recording_state(self) -> str:
        """Return the recording controller's current state for visual rendering."""
        return self.app_controller.recording_controller.state

    @property
    def is_busy(self) -> bool:
        """Return whether a controller workflow currently disables UI actions."""
        return (self.app_controller.is_processing or self.app_controller.is_loading_model
                or self.app_controller.is_unloading_model or self.app_controller.is_shutting_down)

    def load_fonts(self) -> None:
        """Load bundled Inter fonts for the existing UI design."""
        for font_name in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf"):
            QFontDatabase.addApplicationFont(
                get_resource_path(os.path.join("assets", "fonts", font_name))
            )

    def init_ui(self) -> None:
        """Construct the static window layout and visual controls."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.setAcceptDrops(True)

        self.drop_overlay = QLabel("Drag an audio file here", central_widget)
        self.drop_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_overlay.setFont(QFont("Inter", 24, QFont.Weight.Bold))
        self.drop_overlay.setStyleSheet(
            "QLabel { background-color: rgba(0, 0, 0, 0.7); color: white; "
            "border: 2px dashed #aaa; border-radius: 15px; }"
        )
        self.drop_overlay.hide()

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.create_top_header(root_layout)

        self.line = QFrame()
        self.line.setFrameShape(QFrame.Shape.HLine)
        root_layout.addWidget(self.line)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(0)
        self.create_sidebar(content_layout)
        self.separator = QFrame()
        self.separator.setFrameShape(QFrame.Shape.VLine)
        content_layout.addWidget(self.separator)
        self.create_main_area(content_layout)
        root_layout.addLayout(content_layout)

        self._apply_theme()
        self._update_ui_for_state()

    def _connect_controller_signals(self) -> None:
        """Subscribe View rendering slots to all controller events."""
        controller = self.app_controller
        controller.model_loading.connect(self._on_model_loading)
        controller.model_ready.connect(self._on_model_ready)
        controller.model_load_failed.connect(self._on_model_load_failed)
        controller.model_selection_reverted.connect(self._on_model_selection_reverted)
        controller.language_forced.connect(self._on_language_forced)
        controller.language_required.connect(self._on_language_required)
        controller.microphone_unavailable.connect(self._on_microphone_unavailable)

        controller.recording_state_changed.connect(self._on_recording_state_changed)
        controller.recording_duration_changed.connect(self._on_recording_duration_changed)
        controller.recording_stop_requested.connect(self._on_recording_stop_requested)
        controller.recording_cancelled.connect(self._on_recording_cancelled)
        controller.recording_invalid.connect(self._on_recording_invalid)
        controller.recording_interrupted.connect(self._on_recording_interrupted)
        controller.recording_watchdog.connect(self._on_recording_watchdog)

        controller.processing_started.connect(self._on_processing_started)
        controller.batch.changed.connect(self._render_batch)
        controller.runtime_phase_changed.connect(lambda phase: self.update_status_display(phase, is_loading=True))
        controller.transcription_empty.connect(self._on_transcription_empty)
        controller.transcription_failed.connect(self._on_transcription_failed)
        controller.transcription_warning.connect(self._on_transcription_warning)
        controller.processing_finished.connect(self._on_processing_finished)
        controller.model_memory_busy_changed.connect(self._on_model_memory_busy_changed)
        controller.model_memory_failed.connect(lambda message: self.show_error_dialog("Model memory", message))

        controller.notes_loaded.connect(self._render_notes)
        controller.note_created.connect(self._add_note_card)
        controller.note_opened.connect(self._display_opened_note)
        controller.note_deleted.connect(self._remove_note_card)
        controller.notes_filtered.connect(self._render_filtered_notes)
        controller.active_note_cleared.connect(self._render_editor_reset)
        controller.note_autosaved.connect(self._on_note_autosaved)
        controller.autosave_error.connect(self._on_autosave_error)
        controller.notes_error.connect(self._on_notes_error)
        controller.notes_controller.folders_changed.connect(self._refresh_folders)
        controller.post_settings_changed.connect(self._refresh_refine)
        controller.refining_started.connect(lambda: self.update_status_display("Refining", is_loading=True))
        controller.refinement_failed.connect(lambda message: self.show_error_dialog("Refinement", message))
        controller.storage_warning.connect(lambda message: self.show_error_dialog("Recording storage", message))
        controller.application_settings_changed.connect(self._sync_tray_preferences)
        controller.shutdown_ready.connect(self._finish_shutdown)
        self._refresh_folders()
        self._refresh_refine()

    def _refresh_folders(self):
        notes = self.app_controller.notes_controller
        folders = notes.list_folders()
        selected = next(f for f in folders if f["id"] == notes.active_folder)
        self.folder_selector.set_folder_label(selected["name"])
        self.folder_selector.setIcon(folder_icon(selected))
        for card in self._cards_by_filepath.values():
            card.folder_options = folders

    def _setup_tray(self):
        """Create exactly one tray icon and explicit open/exit actions for this window."""
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self._tray_icon = QSystemTrayIcon(self.windowIcon(), self)
        self._tray_icon.setToolTip("FeinNote")
        self._tray_menu = QMenu(self)
        self._tray_menu.setToolTipsVisible(True)
        open_action = self._tray_menu.addAction("Open FeinNote", self.restore_from_tray)
        set_help(open_action, "tray_open")
        self._tray_start = self._tray_menu.addAction("Start recording", self._tray_start_recording)
        self._tray_pause = self._tray_menu.addAction("Pause recording", self._tray_pause_recording)
        self._tray_stop = self._tray_menu.addAction("Stop recording", self.app_controller.stop_recording)
        self._tray_cancel = self._tray_menu.addAction("Cancel recording", self.app_controller.cancel_recording)
        self._tray_record_separator = self._tray_menu.addSeparator()
        self._tray_copy = self._tray_menu.addAction("Copy last note", self._copy_last_note)
        self._tray_menu.addSeparator()
        self._tray_exit = self._tray_menu.addAction("Exit", self.request_exit)
        for action, key in ((self._tray_start, "record_start"), (self._tray_stop, "record_stop"),
                            (self._tray_cancel, "record_cancel"), (self._tray_copy, "tray_copy"),
                            (self._tray_exit, "tray_exit")):
            set_help(action, key)
        self._tray_menu.aboutToShow.connect(self._refresh_tray_menu)
        controller = self.app_controller
        for signal in (controller.recording_state_changed, controller.recording_stop_requested,
                       controller.recording_cancelled, controller.processing_started,
                       controller.processing_finished, controller.model_loading,
                       controller.model_ready, controller.model_load_failed,
                       controller.note_created, controller.note_deleted, controller.notes_loaded):
            signal.connect(self._refresh_tray_menu)
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._tray_activated)
        self._sync_tray_preferences()
        self._refresh_tray_menu()

    def _tray_start_recording(self):
        self.app_controller.start_recording()
        self._refresh_tray_menu()

    def _tray_pause_recording(self):
        if self.recording_state == "paused":
            self.app_controller.resume_recording()
        else:
            self.app_controller.pause_recording()
        self._refresh_tray_menu()

    def _copy_last_note(self):
        try:
            if not self.app_controller.notes_controller.force_save():
                return
            text = self.app_controller.notes_controller.latest_note_text()
            if text is not None:
                QApplication.clipboard().setText(text)
        except (OSError, ValueError):
            self.show_error_dialog("Copy last note", "The latest note could not be read.")

    def _refresh_tray_menu(self, *_args):
        if self._tray_icon is None:
            return
        recording = self.app_controller.recording_controller
        active = recording.state in ("recording", "paused")
        available = not self.is_busy and not self._exit_requested
        self._tray_menu.setStyleSheet(f"""
            QMenu {{ background: {self.theme['SIDEBAR_BG']}; color: {self.theme['DARK_TEXT']}; border: 1px solid {self.theme['BORDER']}; padding: 6px; }}
            QMenu::item {{ padding: 7px 24px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {self.theme['BUTTON_HOVER_BG']}; }}
            QMenu::item:disabled {{ color: {self.theme['GRAY_TEXT']}; }}
            QMenu::separator {{ height: 1px; background: {self.theme['BORDER']}; margin: 5px; }}
        """)
        self._tray_start.setVisible(not active)
        self._tray_start.setEnabled(available and recording.can_start())
        for action in (self._tray_pause, self._tray_stop, self._tray_cancel, self._tray_record_separator):
            action.setVisible(active)
        self._tray_pause.setText("Resume recording" if recording.state == "paused" else "Pause recording")
        set_help(self._tray_pause, "record_resume" if recording.state == "paused" else "record_pause")
        self._tray_pause.setEnabled(available and not recording.is_stopping)
        self._tray_stop.setEnabled(available and not recording.is_stopping)
        self._tray_cancel.setEnabled(available)
        try:
            self._tray_copy.setEnabled(not self._exit_requested and self.app_controller.notes_controller.latest_note_text() is not None)
        except (OSError, ValueError):
            self._tray_copy.setEnabled(False)
        for action, name in ((self._tray_start, "mic"), (self._tray_pause, "play" if recording.state == "paused" else "pause"),
                             (self._tray_stop, "square"), (self._tray_cancel, "x"), (self._tray_copy, "copy"), (self._tray_exit, "log-out")):
            action.setIcon(get_icon(name, theme=self.theme))

    def _sync_tray_preferences(self):
        if self._tray_icon is None:
            return
        settings = self.app_controller.get_application_settings()
        enabled = settings["minimize_to_tray"] or settings["close_to_tray"]
        self._tray_icon.setVisible(enabled and QSystemTrayIcon.isSystemTrayAvailable())
        if not enabled and self._hidden_in_tray and not self._exit_requested:
            self.restore_from_tray()

    def _hide_to_tray(self):
        if self._exit_requested or not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        if not self.isMinimized():
            self._tray_was_maximized = self.isMaximized()
        self._tray_icon.show()
        self._hidden_in_tray = True
        self.hide()
        return True

    def restore_from_tray(self):
        """Restore the previous normal/maximized window and request keyboard focus."""
        self._hidden_in_tray = False
        if self._tray_was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_from_tray()

    def changeEvent(self, event):
        """Apply minimize-to-tray only after Qt completes the window-state change."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() and getattr(self, "_tray_icon", None) is not None:
            self._tray_was_maximized = bool(event.oldState() & Qt.WindowState.WindowMaximized)
            QTimer.singleShot(0, self._handle_minimize)

    def _handle_minimize(self):
        if self.isMinimized() and self.app_controller.get_application_settings()["minimize_to_tray"]:
            self._hide_to_tray()

    def request_exit(self):
        """Begin real shutdown independently of close-to-tray preferences."""
        if self._exit_requested:
            return
        # Keep a usable editor when saving fails, including Exit from the tray.
        self.restore_from_tray()
        if not self.app_controller.request_shutdown():
            self.show_error_dialog("Saving error", "The note could not be saved. Your edits remain in the editor. Save or copy them before exiting.")
            return
        self._exit_requested = True
        self._copy_feedback_timer.stop()
        self._saved_feedback_timer.stop()
        if self.settings_dialog:
            self.settings_dialog.reject()
        self.setEnabled(False)
        self.update_status_display("Finishing active work and exiting", is_loading=True)

    def _finish_shutdown(self):
        self._shutdown_complete = True
        self.loading_timer.stop()
        self._tray_icon.hide()
        self.close()
        QApplication.instance().quit()

    def _choose_folder(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for folder in self.app_controller.notes_controller.list_folders():
            action = menu.addAction(folder_icon(folder), folder["name"])
            action.setToolTip(f"{HELP['destination']}\n\nFolder: {folder['name']}")
            action.triggered.connect(lambda checked=False, fid=folder["id"]: self._select_folder(fid))
        menu.exec(self.folder_selector.mapToGlobal(self.folder_selector.rect().bottomLeft()))

    def _select_folder(self, folder_id):
        try:
            self.app_controller.select_folder(folder_id)
        except (OSError, ValueError) as error:
            self.show_error_dialog("Folder error", str(error))

    def _refresh_refine(self):
        toggle = self.post_process_toggle.toggle
        toggle.blockSignals(True)
        toggle.setChecked(self.app_controller.refine_enabled())
        toggle.blockSignals(False)
        toggle._on_toggled(toggle.isChecked())

    def _toggle_refine(self, enabled):
        try:
            accepted = self.app_controller.set_refine_enabled(enabled)
        except (OSError, RuntimeError):
            accepted = False
        if not accepted:
            self._refresh_refine()
            self.show_settings_dialog(1, "Configure a post-processing provider first.")

    def _move_note(self, filepath, folder_id):
        try:
            self.app_controller.notes_controller.move_note(filepath, folder_id)
        except (OSError, ValueError) as error:
            self.show_error_dialog("Move note", str(error))

    def _apply_theme(self) -> None:
        """Render all theme-dependent View styles and history card colors."""
        theme = self.theme
        self.settings_button.setIcon(get_icon("settings", theme=theme))
        self.copy_button.setIcon(get_icon("check" if self._copy_feedback_timer.isActive() else "copy", theme=theme))
        self.download_button.setIcon(get_icon("download", theme=theme))
        self.cancel_button.setIcon(get_icon("x", theme=theme))
        self.search_icon.setPixmap(get_pixmap("search", theme=theme, size=18, dpr=self.devicePixelRatioF()))
        self.setStyleSheet(f"QWidget {{ background-color: {theme['WHITE_BG']}; }}" + tooltip_stylesheet(theme))
        self.header.setStyleSheet(f"background-color: {theme['SIDEBAR_BG']};")
        self.line.setStyleSheet(f"color: {theme['BORDER']};")
        self.separator.setStyleSheet(f"color: {theme['BORDER']};")
        self.app_title.setStyleSheet(f"color: {theme['DARK_TEXT']};")
        self.header_separator.setStyleSheet(f"color: {theme['BORDER']};")

        scrollbar_style = scrollbar_stylesheet(theme)
        self.settings_button.setStyleSheet(
            f"QPushButton {{ background-color: transparent; border-radius: 20px; border: none; color: {theme['DARK_TEXT']}; }} "
            f"QPushButton:hover {{ background-color: {theme['HOVER_BG']}; }}"
        )
        self.folder_selector.setStyleSheet(
            f"QPushButton {{ background-color: {theme['WHITE_BG']}; color: {theme['DARK_TEXT']}; border: 1px solid {theme['BORDER']}; border-radius: 8px; text-align: left; padding-left: 12px; padding-right: 32px; }}"
        )

        self.sidebar.setStyleSheet(f"background-color: {theme['SIDEBAR_BG']};")
        self.search_frame.setStyleSheet(
            f"QFrame {{ background-color: {theme['WHITE_BG']}; border: 1px solid {theme['BORDER']}; "
            "border-radius: 10px; padding: 2px; }"
        )
        self.search_input.setStyleSheet(
            f"QLineEdit {{ border: none; background-color: transparent; color: {theme['DARK_TEXT']}; }} "
            f"QLineEdit::placeholder {{ color: {theme['LIGHT_GRAY_TEXT']}; }}"
        )
        self.scroll_area.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }} {scrollbar_style}")
        self.main_area.setStyleSheet(f"background-color: {theme['WHITE_BG']};")
        self.result_title.setStyleSheet(f"color: {theme['DARK_TEXT']};")
        self.text_area.setStyleSheet(
            f"QTextEdit {{ background-color: {theme['SIDEBAR_BG']}; border: 1px solid {theme['BORDER']}; "
            f"border-radius: 12px; padding: 20px; color: {theme['DARK_TEXT']}; }} {scrollbar_style}"
        )
        action_style = (
            f"QPushButton {{ background-color: transparent; border: 1px solid {theme['BORDER']}; "
            "border-radius: 8px; padding: 8px; } "
            f"QPushButton:hover {{ background-color: {theme['HOVER_BG']}; }}"
        )
        self.copy_button.setStyleSheet(action_style)
        self.download_button.setStyleSheet(action_style)
        version_style = (
            f"QPushButton {{ color: {theme['DARK_TEXT']}; background: {theme['SIDEBAR_BG']}; "
            f"border: 1px solid {theme['BORDER']}; border-radius: 8px; padding: 6px 10px; }}"
            f"QPushButton:checked {{ border-color: {theme['BLUE_ACCENT']}; background: {theme['WHITE_BG']}; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {theme['HOVER_BG']}; }}")
        for button in (self.original_button, self.refined_button):
            button.setStyleSheet(version_style)
        self.queue_panel.setStyleSheet(
            f"QFrame#batch_queue {{ background: transparent; border: none; }}"
            f"QLabel {{ color: {theme['DARK_TEXT']}; background: transparent; }}"
            f"QListWidget {{ color: {theme['DARK_TEXT']}; background: {theme['SIDEBAR_BG']}; "
            f"border: 1px solid {theme['BORDER']}; border-radius: 8px; padding: 6px; }}"
            f"QProgressBar {{ color: {theme['DARK_TEXT']}; border: 1px solid {theme['BORDER']}; "
            f"background: {theme['SIDEBAR_BG']}; border-radius: 4px; text-align: center; }}"
            f"QProgressBar::chunk {{ background: {theme['BLUE_ACCENT']}; border-radius: 4px; }}"
            + version_style + scrollbar_style)
        
        if self.settings_dialog:
            self.settings_dialog.update_theme(theme)
        if hasattr(self, "dialog_themes"):
            self.dialog_themes.update_theme(theme)
        if hasattr(self, 'post_process_toggle') and hasattr(self.post_process_toggle, 'apply_theme'):
            self.post_process_toggle.apply_theme(theme)
            
        for card in self._cards_by_filepath.values():
            card.update_theme(theme)
        self._update_ui_for_state()
        self._refresh_tray_menu()

    def set_theme(self, theme_name: str) -> None:
        """Set visual theme explicitly and delegate persistence to ``ConfigManager``."""
        if self.current_theme_name == theme_name:
            return
        self.current_theme_name = theme_name
        self.theme = dark_theme if self.current_theme_name == "dark" else light_theme
        try:
            self.app_controller.set_theme(self.current_theme_name)
        except OSError as error:
            logger.error("Could not save theme", exc_info=True)
            self.show_error_dialog("Settings error", f"Failed to save theme: {error}")
        self._apply_theme()

    def create_top_header(self, parent_layout: QVBoxLayout) -> None:
        """Create header widgets and connect UI inputs to controller methods."""
        self.header = QFrame()
        self.header.setFixedHeight(80)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        left_header = QWidget()
        left_header.setFixedWidth(340)
        left_layout = QHBoxLayout(left_header)
        left_layout.setContentsMargins(16, 0, 16, 0)
        self.app_title = QLabel("FeinNote")
        self.app_title.setFont(QFont("Inter", 20, QFont.Weight.Bold))
        left_layout.addWidget(self.app_title)
        left_layout.addStretch()

        self.settings_button = QPushButton()
        self.settings_button.setIcon(get_icon("settings", theme=self.theme))
        self.settings_button.setFixedSize(40, 40)
        self.settings_button.setIconSize(QSize(28, 28))
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self.show_settings_dialog)
        left_layout.addWidget(self.settings_button)

        right_header = QWidget()
        right_layout = QHBoxLayout(right_header)
        right_layout.setContentsMargins(32, 0, 24, 0)
        right_layout.setSpacing(16)
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(16)
        controls_layout.addWidget(self.create_status_widget())
        right_layout.addWidget(controls_widget)
        right_layout.addStretch(1)

        self.timer_label = QLabel("")
        self.timer_label.setFixedWidth(80)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_label.setFont(QFont("Inter", 18, QFont.Weight.Bold))
        self.timer_label.hide()
        right_layout.addWidget(self.timer_label)

        self.cancel_button = self._create_round_button("x.svg", self.cancel_recording)
        self.cancel_button.hide()
        right_layout.addWidget(self.cancel_button)
        self.primary_button = self._create_round_button(None, self._on_primary_button_click)
        right_layout.addWidget(self.primary_button)
        self.secondary_button = self._create_round_button(None, self._on_secondary_button_click)
        right_layout.addWidget(self.secondary_button)

        header_layout.addWidget(left_header)
        self.header_separator = QFrame()
        self.header_separator.setFrameShape(QFrame.Shape.VLine)
        header_layout.addWidget(self.header_separator)
        header_layout.addWidget(right_header)
        self._apply_initial_language_selection()
        parent_layout.addWidget(self.header)

    def _create_round_button(self, icon_name: str | None, callback: object) -> QPushButton:
        """Create a circular header action button.

        Args:
            icon_name: Optional SVG icon filename.
            callback: Slot called when the button is clicked.

        Returns:
            The configured button.
        """
        button = QPushButton()
        button.setFixedSize(56, 56)
        button.setIconSize(QSize(24, 24))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            button.setIcon(get_icon(icon_name.removesuffix(".svg"), theme=self.theme))
        button.clicked.connect(callback)
        return button



    def _apply_initial_language_selection(self) -> None:
        """Render the persisted language and English-only model restriction."""
        if not self.settings_dialog:
            return
        selected_model = self.app_controller.get_selected_model()
        selected_language = "en" if selected_model.endswith(".en") else self.app_controller.get_selected_language()
        language_name = self.lang_code_to_name.get(selected_language or "")
        if language_name:
            self.settings_dialog.set_selected_language(language_name)
            
        self.settings_dialog.set_controls_enabled(not self.is_busy and self.recording_state == "idle")

    def create_status_widget(self) -> QWidget:
        """Create the status caption used for controller progress display."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        status_row = QWidget()
        status_row_layout = QHBoxLayout(status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(6)

        self.status_indicator = QLabel("●")
        self.status_indicator.setFont(QFont("Inter", pointSize=13, weight=QFont.Weight.DemiBold))
        self.status_indicator.setContentsMargins(0, 0, 0, 0)
        self.status_indicator.setAlignment(Qt.AlignmentFlag.AlignLeft)
        status_row_layout.addWidget(self.status_indicator)

        self.status_display = QLabel("Loading model...")
        self.status_display.setFont(QFont("Inter", pointSize=14, weight=QFont.Weight.DemiBold))
        self.status_display.setAlignment(Qt.AlignmentFlag.AlignLeft)
        status_row_layout.addWidget(self.status_display, stretch=1)

        layout.addWidget(status_row)
        return widget

    def create_sidebar(self, parent_layout: QHBoxLayout) -> None:
        """Create the history search and card container without loading persistence directly."""
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(340)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(16, 16, 8, 16)
        sidebar_layout.setSpacing(16)
        self.search_frame = QFrame()
        search_layout = QHBoxLayout(self.search_frame)
        search_layout.setContentsMargins(12, 8, 12, 8)
        search_layout.setSpacing(8)
        search_icon = self.search_icon = QLabel()
        search_icon.setPixmap(
            get_icon("search", theme=self.theme).pixmap(18, 18)
        )
        search_icon.setStyleSheet("border: none; background-color: transparent;")
        search_layout.addWidget(search_icon)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search notes...")
        self.search_input.setFont(QFont("Inter", 14))
        self.search_input.textChanged.connect(self.app_controller.filter_notes)
        search_layout.addWidget(self.search_input)
        sidebar_layout.addWidget(self.search_frame)

        placeholders_layout = QHBoxLayout()
        placeholders_layout.setContentsMargins(0, 0, 0, 0)
        placeholders_layout.setSpacing(8)
        
        self.folder_selector = FolderDropdownButton("Inbox", "folder-pen.svg")
        self.folder_selector.clicked.connect(self._choose_folder)

        self.post_process_toggle = PostProcessRow(theme=self.theme)
        self.post_process_toggle.toggle.toggled.connect(self._toggle_refine)

        equal_width_policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        equal_width_policy.setHorizontalStretch(1)
        self.folder_selector.setSizePolicy(equal_width_policy)
        self.post_process_toggle.setSizePolicy(equal_width_policy)
        self.folder_selector.setMinimumWidth(0)
        self.post_process_toggle.setMinimumWidth(0)
        
        placeholders_layout.addWidget(self.folder_selector)
        placeholders_layout.addWidget(self.post_process_toggle)
        placeholders_layout.setStretch(0, 1)
        placeholders_layout.setStretch(1, 1)
        sidebar_layout.addLayout(placeholders_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setViewportMargins(0, 0, 0, 0)
        scroll_content = QWidget()
        self.history_layout = QVBoxLayout(scroll_content)
        self.history_layout.setContentsMargins(0, 4, 20, 4)
        self.history_layout.setSpacing(12)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(scroll_content)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._schedule_hover_refresh)
        sidebar_layout.addWidget(self.scroll_area)
        parent_layout.addWidget(self.sidebar)

    def create_main_area(self, parent_layout: QHBoxLayout) -> None:
        """Create the transcription editor and its View-only copy/export actions."""
        self.main_area = QFrame()
        layout = QVBoxLayout(self.main_area)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)
        header_layout = QHBoxLayout()
        self.result_title = QLabel("Transcription result")
        self.result_title.setFont(QFont("Inter", pointSize=18, weight=QFont.Weight.DemiBold))
        header_layout.addWidget(self.result_title)
        header_layout.addStretch()
        self.version_switch = QWidget()
        versions = QHBoxLayout(self.version_switch)
        versions.setContentsMargins(0, 0, 0, 0)
        self.original_button, self.refined_button = QPushButton("Original"), QPushButton("Refined")
        for button, original in ((self.original_button, True), (self.refined_button, False)):
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=original: self._select_version(value))
            versions.addWidget(button)
        header_layout.addWidget(self.version_switch)
        self.version_switch.hide()
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self.copy_button = QPushButton(icon=get_icon("copy", theme=self.theme))
        self.copy_button.setFixedSize(40, 40)
        self.copy_button.setIconSize(QSize(18, 18))
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self.copy_text)
        actions_layout.addWidget(self.copy_button)
        self.download_button = QPushButton(icon=get_icon("download", theme=self.theme))
        self.download_button.setFixedSize(40, 40)
        self.download_button.setIconSize(QSize(18, 18))
        self.download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_button.clicked.connect(self.save_text_to_file)
        actions_layout.addWidget(self.download_button)
        header_layout.addWidget(actions_widget)
        header_layout.addSpacing(16)

        self.text_area = QTextEdit()
        self.text_area.setPlaceholderText("Transcribed text will appear here...")
        self.text_area.setFont(QFont("Inter", pointSize=14))
        self.text_area.setAcceptDrops(False)
        self.text_area.textChanged.connect(self.app_controller.request_autosave)
        layout.addLayout(header_layout)
        self.queue_panel = QFrame()
        self.queue_panel.setObjectName("batch_queue")
        self.queue_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.queue_panel.setFont(QFont("Inter", 10))
        queue_layout = QVBoxLayout(self.queue_panel)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.setSpacing(6)
        self.queue_label = QLabel()
        self.queue_label.setWordWrap(True)
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(100)
        self.queue_list.setMinimumHeight(60)
        self.queue_progress = QProgressBar()
        self.queue_progress.setFixedHeight(8)
        self.queue_progress.setTextVisible(False)
        self.queue_stop = QPushButton("Stop after current operation")
        self.queue_stop.clicked.connect(self.app_controller.batch.stop)
        for widget in (self.queue_label, self.queue_list, self.queue_progress, self.queue_stop):
            queue_layout.addWidget(widget)
        layout.addWidget(self.queue_panel)
        self.queue_panel.hide()
        layout.addWidget(self.text_area, 1)
        parent_layout.addWidget(self.main_area)

    def _render_batch(self, state):
        """Render a compact queue snapshot without scheduling work in the View."""
        self.queue_panel.show()
        percent = round(100 * state["done"] / max(1, state["units"]))
        refine = state.get("refine_enabled", False)
        if state["running"]:
            step = ""
            explanation = "Refine is off for this batch."
            if refine:
                transcribing = state.get("stage") == "transcribe"
                step = "Step 1/2 · " if transcribing else "Step 2/2 · "
                explanation = ("Refine starts after all originals are saved." if transcribing
                               else "Refining saved transcripts; original text is preserved.")
            position = f" · file {state['index']}/{state['total']}" if 0 < state["index"] <= state["total"] else ""
            self.queue_label.setText(f"{step}{state['phase']}{position} · {percent}% overall\n{explanation}")
        else:
            completed = sum(row["status"] == "Completed" for row in state["rows"])
            failed = sum(row["status"] == "Failed" for row in state["rows"])
            cancelled = sum(row["status"] in ("Cancelled", "Refine cancelled") for row in state["rows"])
            summary = f"{state['phase']} — {completed}/{state['total']} files completed"
            if failed:
                summary += f" · {failed} failed"
            if cancelled:
                summary += f" · {cancelled} cancelled"
            self.queue_label.setText(summary)
        self.queue_list.clear()
        for row in state["rows"]:
            status = row["status"]
            if status == "Completed":
                status = "Transcribed · Refined" if refine else "Transcribed"
            elif status == "Awaiting Refine":
                status = "Transcribed · Awaiting Refine"
            elif status == "Refine cancelled":
                status = "Refine cancelled · original saved"
            elif status == "Failed" and row.get("path"):
                status = "Refine failed · original saved"
            self.queue_list.addItem(f"{row['name']} — {status}")
            details = f"{row['name']}\nStatus: {status}"
            if row["error"]:
                details += "\n" + row["error"]
            self.queue_list.item(self.queue_list.count() - 1).setToolTip(details)
        if 0 < state["index"] <= self.queue_list.count():
            self.queue_list.scrollToItem(self.queue_list.item(state["index"] - 1))
        self.queue_progress.setRange(0, state["units"])
        self.queue_progress.setValue(state["done"])
        self.queue_progress.setVisible(state["running"])
        self.queue_stop.setVisible(state["running"])
        self.queue_stop.setEnabled(state["running"] and not state["stopping"])
        self.queue_stop.setText("Stopping after current operation…" if state["stopping"] else "Stop after current operation")

    def _reset_batch_panel(self):
        """Clear the previous queue's presentation when a new audio workflow starts."""
        self.queue_panel.hide()
        self.queue_label.clear()
        self.queue_list.clear()
        self.queue_progress.reset()
        self.queue_stop.setEnabled(False)

    def _render_notes(self, notes: object) -> None:
        """Render repository-provided note data as history cards."""
        self.active_note_card = None
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards_by_filepath.clear()
        for note_data, filepath in notes:
            self._add_note_card(note_data, filepath, add_to_top=False)
            if filepath == self.app_controller.notes_controller.active_filepath:
                self.active_note_card = self._cards_by_filepath[filepath]
                self.active_note_card.setState("active")
        self._schedule_hover_refresh()

    def _schedule_hover_refresh(self):
        """Wait for layout/deferred deletion before checking stationary pointer hover."""
        QTimer.singleShot(0, self._refresh_card_hover)

    def _refresh_card_hover(self):
        """Refresh only current cards; removed cards are never captured in callbacks."""
        for card in self._cards_by_filepath.values():
            card.refresh_hover()

    def _add_note_card(self, note_data: object, filepath: str, add_to_top: bool = True) -> None:
        """Create one View-only card from note data emitted by ``NotesController``."""
        card = HistoryCardWidget(note_data, filepath, self.theme)
        card.card_clicked.connect(self._on_card_clicked)
        card.copy_clicked.connect(self.copy_note_text)
        card.delete_clicked.connect(self._on_card_delete_requested)
        card.export_requested.connect(self._export_note)
        card.folder_options = self.app_controller.notes_controller.list_folders()
        self._schedule_hover_refresh()
        card.move_requested.connect(self._move_note)
        self._cards_by_filepath[filepath] = card
        if add_to_top:
            self.history_layout.insertWidget(0, card)
        else:
            self.history_layout.addWidget(card)

    def _on_card_clicked(self, _text: str, card: HistoryCardWidget) -> None:
        """Forward history-card selection to the notes controller."""
        self.app_controller.select_note(card.file_path)

    def _display_opened_note(self, note_data: object, filepath: str) -> None:
        """Render an opened note in the editor and update the active card state."""
        card = self._cards_by_filepath.get(filepath)
        if not card:
            return
        self.text_area.blockSignals(True)
        notes = self.app_controller.notes_controller
        original = notes.viewing_original
        self.text_area.setPlainText(str(note_data["raw_text"] if original else note_data["full_text"]))
        self.text_area.blockSignals(False)
        self.text_area.setReadOnly(self.is_busy or original)
        self.version_switch.setVisible(notes.has_refined_version(note_data))
        self.version_switch.setEnabled(not self.is_busy)
        self.original_button.setChecked(original)
        self.refined_button.setChecked(not original)
        source = note_data.get("source_name")
        self.text_area.setToolTip(HELP["editor"] + (f"\n\nSource: {source}" if source else ""))
        self.text_area.setFocus()
        if self.active_note_card and self.active_note_card is not card:
            self.active_note_card.setState("normal")
        card.setState("active")
        self.active_note_card = card

    def _select_version(self, original):
        """Request a safe controller-owned version switch and restore checks on failure."""
        if not self.is_busy:
            self.app_controller.notes_controller.select_version(original)
        selected = self.app_controller.notes_controller.viewing_original
        self.original_button.setChecked(selected)
        self.refined_button.setChecked(not selected)

    def _on_card_delete_requested(self, filepath: str, _card: HistoryCardWidget) -> None:
        """Forward history-card deletion to the notes controller."""
        if not self.app_controller.confirm_note_deletion() or QMessageBox.question(self, "Delete note", "Permanently delete this note? This cannot be undone.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.app_controller.delete_note(filepath)

    def _remove_note_card(self, filepath: str) -> None:
        """Remove a deleted card after repository deletion succeeds."""
        card = self._cards_by_filepath.pop(filepath, None)
        if card:
            # Qt treats hiding/deleting a focused trash button as Tab navigation,
            # which otherwise moves focus to the gear and draws a keyboard ring.
            focused = QApplication.focusWidget()
            if focused is card or (focused is not None and card.isAncestorOf(focused)):
                self.text_area.setFocus(Qt.FocusReason.OtherFocusReason)
            if self.active_note_card is card:
                self.active_note_card = None
            self.history_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
            self._schedule_hover_refresh()

    def _render_filtered_notes(self, matching_paths: object) -> None:
        """Show or hide cards using controller-provided search results."""
        visible_paths = set(matching_paths)
        for filepath, card in self._cards_by_filepath.items():
            card.setVisible(filepath in visible_paths)

    def copy_note_text(self, text: str) -> None:
        """Copy history-card text through the View clipboard API."""
        QApplication.clipboard().setText(text)

    def _on_note_autosaved(self, filepath: str, full_text: str) -> None:
        """Show temporary saved feedback and refresh the matching card preview."""
        if not self._exit_requested:
            self.result_title.setText("Transcription result • Saved ✓")
            self._saved_feedback_timer.start()
        card = self._cards_by_filepath.get(filepath)
        if card:
            card.update_preview(full_text)

    def _reset_saved_feedback(self) -> None:
        """Restore the editor caption when the owned feedback timer expires."""
        self.result_title.setText("Transcription result")

    def reset_editor_state(self) -> None:
        """Request a force-save and active-note clear for an editor reset action."""
        self.app_controller.clear_active_note()

    def _render_editor_reset(self) -> None:
        """Clear editor visuals after controller clears the active note."""
        self.text_area.blockSignals(True)
        self.text_area.clear()
        self.version_switch.hide()
        self.text_area.blockSignals(False)
        self.text_area.setReadOnly(True)
        self.text_area.setPlaceholderText("Transcribed text will appear here...")
        set_help(self.text_area, "editor")
        if self.active_note_card:
            self.active_note_card.setState("normal")
            self.active_note_card = None

    def _on_primary_button_click(self) -> None:
        """Forward microphone start or stop button behavior to the controller."""
        if self.is_busy:
            return
        if self.recording_state == "idle":
            self.app_controller.start_recording()
        elif self.recording_state in ("recording", "paused"):
            self.app_controller.stop_recording()

    def _on_secondary_button_click(self) -> None:
        """Forward file selection, pause, or resume behavior to the controller."""
        if self.is_busy:
            return
        if self.recording_state == "idle":
            self.select_file()
        elif self.recording_state == "recording":
            self.app_controller.pause_recording()
        elif self.recording_state == "paused":
            self.app_controller.resume_recording()

    def _update_ui_for_state(self) -> None:
        """Render the existing button and control visuals from controller state."""
        theme = self.theme
        disabled = self.is_busy
        recording = self.recording_state != "idle"
        stopping = self.app_controller.recording_controller.is_stopping
        self.primary_button.setAccessibleName("Stop recording" if recording else "Start recording")
        self.secondary_button.setAccessibleName(
            "Pause recording" if self.recording_state == "recording" else "Resume recording" if recording else "Import audio files")
        primary_help = "record_stop" if recording else "record_start"
        secondary_help = "record_pause" if self.recording_state == "recording" else "record_resume" if recording else "import"
        set_help(self.primary_button, "record_finishing" if stopping else "input_busy" if disabled else primary_help)
        set_help(self.secondary_button, "record_finishing" if stopping else "input_busy" if disabled else secondary_help)
        cancel_style = (
            f"QPushButton {{ background-color: {theme['SIDEBAR_BG']}; border: 1px solid {theme['BORDER']}; "
            "border-radius: 28px; } "
            f"QPushButton:hover {{ background-color: {theme['HOVER_BG']}; }}"
        )
        if self.recording_state == "idle":
            self.primary_button.setIcon(get_icon("mic", color=light_theme["WHITE_BG"]))
            self.primary_button.setStyleSheet(
                f"QPushButton {{ background-color: {theme['BLUE_ACCENT'] if not disabled else '#94a3b8'}; "
                "border-radius: 28px; border: none; } "
                f"QPushButton:hover {{ background-color: {'#0369a1' if not disabled else '#94a3b8'}; }}"
            )
            self.primary_button.setEnabled(not disabled)
            self.secondary_button.setIcon(get_icon("upload", theme=self.theme))
            self.secondary_button.setStyleSheet(
                f"QPushButton {{ background-color: {theme['WHITE_BG'] if not disabled else theme['SIDEBAR_BG']}; "
                f"border: 1px solid {theme['BORDER']}; border-radius: 28px; }} "
                f"QPushButton:hover {{ background-color: {theme['HOVER_BG'] if not disabled else theme['SIDEBAR_BG']}; }}"
            )
            self.secondary_button.setEnabled(not disabled)
            self.timer_label.hide()
            self.cancel_button.hide()
        elif self.recording_state == "recording":
            self.primary_button.setIcon(get_icon("square", color=light_theme["WHITE_BG"]))
            self.primary_button.setStyleSheet(
                f"QPushButton {{ background-color: {theme['RED_RECORD']}; border-radius: 28px; border: none; }} "
                "QPushButton:hover { background-color: #dc2626; }"
            )
            self.primary_button.setEnabled(not disabled and not stopping)
            self.secondary_button.setIcon(get_icon("pause", color=light_theme["WHITE_BG"]))
            self.secondary_button.setStyleSheet(
                f"QPushButton {{ background-color: {theme['ORANGE_PAUSE']}; border-radius: 28px; border: none; }} "
                "QPushButton:hover { background-color: #d97706; }"
            )
            self.secondary_button.setEnabled(not disabled and not stopping)
            self.timer_label.setStyleSheet(f"color: {theme['RED_RECORD']};")
            self.timer_label.show()
            self.cancel_button.setStyleSheet(cancel_style)
            self.cancel_button.show()
        elif self.recording_state == "paused":
            self.primary_button.setIcon(get_icon("square", color=light_theme["WHITE_BG"]))
            self.primary_button.setEnabled(not disabled and not stopping)
            self.secondary_button.setIcon(get_icon("play", color=light_theme["WHITE_BG"]))
            self.secondary_button.setStyleSheet(
                f"QPushButton {{ background-color: {theme['GREEN_RESUME']}; border-radius: 28px; border: none; }} "
                "QPushButton:hover { background-color: #15803d; }"
            )
            self.secondary_button.setEnabled(not disabled and not stopping)
            self.timer_label.show()
            self.cancel_button.setStyleSheet(cancel_style)
            self.cancel_button.show()
        controls_enabled = not disabled and self.recording_state == "idle"
        self.folder_selector.setEnabled(controls_enabled)
        self.post_process_toggle.toggle.setEnabled(controls_enabled)
        self.text_area.setReadOnly(disabled or not self.app_controller.notes_controller.active_filepath
                                   or self.app_controller.notes_controller.viewing_original)
        self.version_switch.setEnabled(not disabled)
        self.scroll_area.setEnabled(not disabled)
        if self.settings_dialog:
            self.settings_dialog.set_controls_enabled(controls_enabled)
        self.settings_button.setEnabled(True)
        self.download_button.setEnabled(controls_enabled)

    def cancel_recording(self) -> None:
        """Forward cancellation to the recording controller."""
        self.app_controller.cancel_recording()

    def _on_model_loading(self, status: str) -> None:
        """Render non-blocking model load/download progress."""
        self.update_status_display(status, is_loading=True)
        self._update_ui_for_state()

    def _on_model_ready(self) -> None:
        """Render ready state after model loading succeeds."""
        if self.settings_dialog:
            self.settings_dialog.restore_transcription_selection()
        self.update_status_display("Ready")
        self._update_ui_for_state()

    def _on_model_load_failed(self, error_text: str) -> None:
        """Display the legacy model-load failure dialog."""
        self.show_error_dialog(
            "Model load error",
            "Failed to load or initialize the Whisper model.",
            "This may be caused by network connection problems. If you are using a VPN, "
            "try turning it off temporarily and restarting the application.",
            error_text,
        )
        self.update_status_display("Load error", is_error=True)
        self._update_ui_for_state()

    def _on_model_selection_reverted(self, model_name: str) -> None:
        """Render a controller-requested recovery to the previously loaded model."""
        if self.settings_dialog:
            self.settings_dialog.restore_transcription_selection()
        self._apply_initial_language_selection()

    def _on_language_forced(self, language_code: str) -> None:
        """Render English-only model language enforcement supplied by the controller."""
        if self.settings_dialog:
            self.settings_dialog.force_language(language_code)

    def _on_language_required(self, context: str) -> None:
        """Display the existing language-required dialog for an audio action."""
        self.show_error_dialog(
            "Language required",
            "FeinNote needs to know the language to provide accurate transcription.\n\n"
            f"Please select the spoken language before {context}.",
        )

    def _on_microphone_unavailable(self) -> None:
        """Display the existing missing-microphone error dialog."""
        self.show_error_dialog(
            "Microphone not found",
            "Unable to detect a recording device.",
            "Make sure your microphone is connected and working properly.",
        )

    def _on_recording_state_changed(self, state: str) -> None:
        """Render recording and pause transitions emitted by the state machine."""
        if state == "recording":
            self._reset_batch_panel()
            self.text_area.setPlaceholderText("Recording from microphone...")
            self.text_area.setReadOnly(True)
            self.update_status_display("Recording", is_loading=True)
        elif state == "paused":
            self.update_status_display("Paused", is_loading=True)
        self._update_ui_for_state()

    def _on_recording_duration_changed(self, seconds: int) -> None:
        """Render the recording duration owned by ``RecordingController``."""
        minutes, seconds_part = divmod(seconds, 60)
        self.timer_label.setText(f"{minutes:02d}:{seconds_part:02d}")

    def _on_recording_stop_requested(self) -> None:
        """Render legacy processing status while recorder finalizes its WAV file."""
        self.update_status_display("Processing recording...", is_loading=True)
        self.text_area.setPlaceholderText("Processing recording...")
        self._update_ui_for_state()

    def _on_recording_cancelled(self) -> None:
        """Render immediate cancellation feedback while controller cleans temporary audio."""
        self.update_status_display("Ready")
        self.text_area.setPlaceholderText("Recording cancelled")
        self._update_ui_for_state()

    def _on_recording_invalid(self, filepath: str, duration: int, show_error: bool) -> None:
        """Render validation failures for empty, missing, or too-short WAV recordings."""
        if not filepath:
            self.update_status_display("Recording failed - no file", is_error=True)
            self.text_area.setPlaceholderText("Recording failed")
        else:
            if show_error:
                self.show_error_dialog(
                    "Recording Failed",
                    "Audio data was not saved.",
                    "The timer was running, but no audio data was captured. Please check your microphone "
                    "settings or try selecting a different input device in system settings.",
                )
            self.update_status_display("Ready")
        self._update_ui_for_state()

    def _on_recording_interrupted(self, filepath: object, _duration: int) -> None:
        """Display existing partial-recording failure behavior before transcription resumes."""
        if filepath:
            self.show_error_dialog(
                "Recording Interrupted",
                "Recording device lost.",
                "We managed to save the partial recording. Starting transcription...",
            )
        else:
            self.show_error_dialog(
                "Recording Failed", "Recording device lost or error occurred.", "Could not save the recording."
            )
            self.update_status_display("Ready")
        self._update_ui_for_state()

    def _on_recording_watchdog(self) -> None:
        """Display the existing recorder watchdog error dialog."""
        self.update_status_display("Waiting for microphone to stop")
        self._update_ui_for_state()
        self.show_error_dialog(
            "Audio Driver Error",
            "The recording stopped responding.",
            "The application is waiting for the audio driver to release the microphone. "
            "Check your audio device if it does not recover.",
        )

    def _on_processing_started(self) -> None:
        """Render transcription progress while the controller worker runs."""
        self._reset_batch_panel()
        self.update_status_display("Transcription", is_loading=True)
        self.text_area.setPlaceholderText("Transcribing file...")
        self._update_ui_for_state()

    def _on_transcription_empty(self) -> None:
        """Render the existing no-speech placeholder for an empty Whisper result."""
        self.text_area.setPlaceholderText("No speech detected in the recording")

    def _on_transcription_failed(self, error: str) -> None:
        self.show_error_dialog("Transcription Error", f"Transcription failed:\n{error}")

    def _on_transcription_warning(self, message: str) -> None:
        """Log non-critical transcription warnings without replacing runtime status."""
        logger.warning("Transcription warning: %s", message)

    def _on_model_memory_busy_changed(self, busy: bool) -> None:
        """Render resource release without flashing Ready before refinement starts."""
        if busy:
            self.update_status_display("Unloading model", is_loading=True)
        elif not self.app_controller.is_processing and not self.app_controller.is_shutting_down:
            self.update_status_display("Ready")
        self._update_ui_for_state()

    def _on_processing_finished(self) -> None:
        """Restore ready visual state after any transcription worker exits."""
        self.update_status_display("Ready")
        self.text_area.setPlaceholderText("The transcribed text will appear here...")
        self._update_ui_for_state()

    def on_model_changed(self, selection: object) -> None:
        """Require explicit consent before a settings change can acquire weights."""
        if isinstance(selection, str):
            selection = {"engine": self.app_controller.get_selected_engine(), "model": selection,
                         "device_mode": self.app_controller.get_selected_device_mode()}
        if not isinstance(selection, dict):
            return
        accepted = False
        try:
            needs_download = self.app_controller.requires_model_download(selection)
            if not needs_download or self._confirm_model_download(selection):
                accepted = self.app_controller.request_model_change(selection, allow_download=needs_download)
        except (ValueError, OSError) as error:
            self.show_error_dialog("Model selection", str(error))
        if not accepted and self.settings_dialog:
            self.settings_dialog.restore_transcription_selection()

    def _confirm_model_download(self, selection: dict) -> bool:
        """Ask for a download with Cancel as the safe default, without starting work."""
        dialog = QMessageBox(self.settings_dialog or self)
        dialog.setWindowTitle("Download model")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(f"{selection['model']} is not downloaded yet. Download this model?")
        dialog.setInformativeText("Model weights can be several gigabytes. Exit will wait for an active download or model initialization to finish safely.")
        download = dialog.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        return dialog.clickedButton() is download

    def on_language_changed(self, language_code: object) -> None:
        """Forward language combo-box selection to the application controller."""
        if language_code:
            self.app_controller.request_language_change(str(language_code))

    def _open_post_processing_settings(self) -> None:
        """Open the settings dialog directly to the post-processing tab."""
        self.show_settings_dialog(tab_index=1)
        
    def show_settings_dialog(self, tab_index: int = 0, message: str = "") -> None:
        """Create and show the SettingsDialog if not already open."""
        if not self.settings_dialog:
            self.settings_dialog = SettingsDialog(
                self, 
                theme=self.theme,
                models=self.app_controller.get_model_names(),
                languages=sorted(self.lang_name_to_code),
                current_model=self.app_controller.get_selected_model(),
                current_language=self.app_controller.get_selected_language(),
                current_theme=self.current_theme_name,
                lang_name_to_code=self.lang_name_to_code,
                lang_code_to_name=self.lang_code_to_name
            )
            self.settings_dialog.modelChangeRequested.connect(self.on_model_changed)
            self.settings_dialog.languageChangeRequested.connect(self.on_language_changed)
            self.settings_dialog.themeChangeRequested.connect(self.set_theme)
            
            self._apply_initial_language_selection()

        self.settings_dialog.tab_widget.setCurrentIndex(tab_index)
        self.settings_dialog.set_controls_enabled(not self.is_busy and self.recording_state == "idle")
        if message:
            self.settings_dialog.post_panel.message.setText(message)
        self.settings_dialog.exec()

    def select_file(self) -> None:
        """Show the existing file picker after controller validation and preparation."""
        if not self.app_controller.prepare_for_file_selection():
            return
        filepaths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select an audio file",
            "",
            "Audio files (*.mp3 *.wav *.m4a *.ogg *.flac)",
        )
        if len(filepaths) > 1:
            self.app_controller.start_file_batch(filepaths)
        elif filepaths:
            self.app_controller.start_file_transcription(filepaths[0], prepared=True)

    def update_status_display(self, text: str, is_error: bool = False, is_loading: bool = False) -> None:
        """Render status text, semantic colors, and the legacy dot animation."""
        self.loading_timer.stop()
        color = self.theme["DARK_TEXT"]
        if is_error:
            color = self.theme["STATUS_ERROR"]
        elif text == "Ready":
            color = self.theme["STATUS_SUCCESS"]
        elif "Recording" in text:
            color = self.theme["RED_RECORD"]
        elif is_loading:
            color = self.theme["BLUE_ACCENT"]
        elif "Paused" in text:
            color = self.theme.get("ORANGE_PAUSE", "#f59e0b")
        self.status_indicator.setStyleSheet(f"color: {color};")
        self.status_display.setStyleSheet(f"color: {color};")
        if is_loading:
            self._base_loading_text = text
            self._loading_dot_count = 1
            self.status_display.setText(f"{text}.")
            self.loading_timer.start(500)
        else:
            self.status_display.setText(text)

    def _animate_loading_text(self) -> None:
        """Advance the View-only status ellipsis animation."""
        self._loading_dot_count = (self._loading_dot_count + 1) % 4
        dots = "." * self._loading_dot_count or "."
        self.status_display.setText(self._base_loading_text + dots)

    def show_error_dialog(
        self, title: str, main_text: str, informative_text: str = "", detailed_text: str = ""
    ) -> None:
        """Display an existing theme-aware error dialog.

        Args:
            title: Dialog window title.
            main_text: Bold main error text.
            informative_text: Optional supporting recovery guidance.
            detailed_text: Optional technical detail shown in the expandable area.
        """
        if self._exit_requested:
            logger.error("%s: %s", title, main_text)
            return
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Critical)
        message_box.setWindowTitle(title)
        text = f"<b>{main_text}</b>"
        if informative_text:
            text += f"<br><br>{informative_text}"
        message_box.setText(text)
        if detailed_text:
            message_box.setDetailedText(str(detailed_text))
        message_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        message_box.exec()

    def _on_autosave_error(self, error_text: str) -> None:
        """Keep failed-save feedback visible without opening a modal from a timer."""
        logger.error("Autosave error: %s", error_text)
        self._saved_feedback_timer.stop()
        self.result_title.setText("Transcription result • Save failed — edits not saved")

    def _on_notes_error(self, error_text: str) -> None:
        """Log repository errors emitted while loading or mutating history notes."""
        logger.error("Notes error: %s", error_text)

    def show_about_dialog(self) -> None:
        """Open the unchanged About dialog using the active View theme."""
        AboutDialog(self, self.theme).exec()

    def dragEnterEvent(self, event: object) -> None:
        """Show drop affordance for supported local audio files when controllers are idle."""
        if self.is_busy or self.recording_state != "idle":
            event.ignore()
            return
        if self._event_has_supported_audio(event):
            event.acceptProposedAction()
            self.drop_overlay.raise_()
            self.drop_overlay.show()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: object) -> None:
        """Hide the visual drop affordance when the pointer leaves the window."""
        self.drop_overlay.hide()
        event.accept()

    def dropEvent(self, event: object) -> None:
        """Forward the first supported dropped audio file to ``AppController``."""
        self.drop_overlay.hide()
        if self.is_busy or self.recording_state != "idle":
            event.ignore()
            return
        for url in event.mimeData().urls():
            if url.isLocalFile() and self._is_supported_audio_path(url.toLocalFile()):
                self.app_controller.start_file_transcription(url.toLocalFile())
                event.acceptProposedAction()
                return
        event.ignore()

    @staticmethod
    def _is_supported_audio_path(filepath: str) -> bool:
        """Return whether a path uses one of the legacy supported audio extensions."""
        return os.path.splitext(filepath)[1].lower() in SUPPORTED_AUDIO_EXTENSIONS

    def _event_has_supported_audio(self, event: object) -> bool:
        """Return whether a drag event contains a supported local audio file."""
        return event.mimeData().hasUrls() and any(
            url.isLocalFile() and self._is_supported_audio_path(url.toLocalFile())
            for url in event.mimeData().urls()
        )

    def resizeEvent(self, event: object) -> None:
        """Keep the View-only drag overlay aligned with the window size."""
        super().resizeEvent(event)
        self.drop_overlay.resize(self.size())

    def copy_text(self) -> None:
        """Copy current editor text and briefly render the existing check icon."""
        text = self.text_area.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.copy_button.setIcon(
            get_icon("check", theme=self.theme)
        )
        self._copy_feedback_timer.start()

    def _reset_copy_feedback(self) -> None:
        """Restore the canonical copy icon in the current theme, never a saved check."""
        self.copy_button.setIcon(get_icon("copy", theme=self.theme))

    def save_text_to_file(self) -> None:
        """Open the shared export picker for the current editor note."""
        if not self.app_controller.notes_controller.active_filepath and not self.text_area.toPlainText():
            return
        self._export_note()

    def _export_note(self, filepath=None, format_name=None) -> None:
        """Collect a save choice and delegate editor/card export to the controller."""
        try:
            note = self.app_controller.get_export_data(filepath, self.text_area.toPlainText())
            source = filepath or self.app_controller.notes_controller.active_filepath
            suggested = os.path.splitext(os.path.basename(source))[0] if source else f"note_{datetime.now():%Y-%m-%d_%H-%M}"
            choice = choose_export_destination(self, note, suggested, format_name)
            if choice is not None:
                destination, selected = choice
                self.app_controller.export_note(note, destination, selected, overwrite=True)
        except (OSError, ValueError, RuntimeError) as error:
            logger.error("Note export failed", exc_info=True)
            self.show_error_dialog("Saving error", f"Failed to save file: {error}")

    def closeEvent(self, event: object) -> None:
        """Hide to tray when configured, otherwise request one controlled real exit."""
        if self._shutdown_complete:
            event.accept()
            return
        event.ignore()
        if not self._exit_requested and self.app_controller.get_application_settings()["close_to_tray"]:
            if self._hide_to_tray():
                return
        self.request_exit()
