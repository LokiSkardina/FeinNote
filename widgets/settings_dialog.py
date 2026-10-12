# Responsible for: assembling the themed settings pages and navigation.

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QStackedWidget, QFrame, QButtonGroup, QSizePolicy,
    QScrollArea, QDoubleSpinBox, QSpinBox, QCheckBox, QTextEdit, QAbstractSpinBox
)
from PyQt6.QtGui import QFont, QIcon, QDesktopServices
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, QUrl
from utils.icon_manager import get_icon
from utils.themes import settings_stylesheet
from widgets.themed_controls import ThemedComboBox as QComboBox, SettingsInteractionManager
from widgets.settings_help import HELP, model_help, set_help
from widgets.toggle_switch import ToggleSwitch


class SegmentedTabBar(QWidget):
    """A macOS-style segmented control used to switch between settings pages.

    Behaves like a QTabBar (addTab / currentChanged / setCurrentIndex) but renders
    as a single rounded 'track' containing equal-width, mutually-exclusive buttons.
    The checked button reads as a raised pill. Styling is applied by the parent
    dialog via object names (SegTrack / SegButton).
    """

    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: list[QPushButton] = []

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_clicked)

        self._track = QFrame(self)
        self._track.setObjectName("SegTrack")
        self._track_layout = QHBoxLayout(self._track)
        self._track_layout.setContentsMargins(4, 4, 4, 4)
        self._track_layout.setSpacing(4)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._track)

    def addTab(self, text: str, icon: QIcon | None = None) -> int:
        index = len(self._buttons)

        btn = QPushButton(text)
        btn.setObjectName("SegButton")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        # Equal-width segments that share the track evenly.
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if icon is not None and not icon.isNull():
            btn.setIcon(icon)
            btn.setIconSize(QSize(16, 16))
        if index == 0:
            btn.setChecked(True)

        self._group.addButton(btn, index)
        self._track_layout.addWidget(btn)
        self._buttons.append(btn)
        return index

    def _on_clicked(self, index: int):
        self.currentChanged.emit(index)

    def setCurrentIndex(self, index: int):
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)
            self.currentChanged.emit(index)

    def currentIndex(self) -> int:
        return self._group.checkedId()


class PromptTextEdit(QTextEdit):
    """Identify direct user edits so disk persistence can be batched safely."""

    def __init__(self, parent=None):
        """Create a plain text editor with a transient input-state marker."""
        super().__init__(parent)
        self.user_edit_in_progress = False

    def keyPressEvent(self, event):
        """Mark keyboard edits while QTextEdit emits its synchronous change signal."""
        previous = self.user_edit_in_progress
        self.user_edit_in_progress = True
        try:
            super().keyPressEvent(event)
        finally:
            self.user_edit_in_progress = previous

    def inputMethodEvent(self, event):
        """Batch composed text produced by input methods as user input."""
        previous = self.user_edit_in_progress
        self.user_edit_in_progress = True
        try:
            super().inputMethodEvent(event)
        finally:
            self.user_edit_in_progress = previous

    def insertFromMimeData(self, source):
        """Batch pasted text, including a paste through the context menu."""
        previous = self.user_edit_in_progress
        self.user_edit_in_progress = True
        try:
            super().insertFromMimeData(source)
        finally:
            self.user_edit_in_progress = previous


class SettingsDialog(QDialog):
    modelChangeRequested = pyqtSignal(object)
    languageChangeRequested = pyqtSignal(object)
    themeChangeRequested = pyqtSignal(str)
    openPostProcessingRequested = pyqtSignal()

    def __init__(self, parent=None, theme=None, models=None, languages=None,
                 current_model=None, current_language=None, current_theme=None,
                 lang_name_to_code=None, lang_code_to_name=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedSize(660, 620)
        self.theme = theme
        self.models = models or []
        self.languages = languages or []
        self.current_model = current_model
        self.current_language = current_language
        self.current_theme = current_theme
        self.lang_name_to_code = lang_name_to_code or {}
        self.lang_code_to_name = lang_code_to_name or {}
        self.transcription_settings = self._get_transcription_settings(parent)
        self._engine_models: dict[str, list[object]] = {}
        self._selected_models: dict[str, str] = dict(self.transcription_settings.get("selected_models", {}))

        # UI elements
        self.engine_selector = None
        self.model_selector = None
        self.model_status_label = None
        self.device_selector = None
        self.device_status_label = None
        self.language_combo_box = None
        self.theme_selector = None

        self._init_ui()
        self._apply_theme()
        self._setup_help()
        self.interactions = SettingsInteractionManager(self)

    def _setup_help(self):
        """Bind Settings descriptions centrally without persisting help text."""
        for button, key in zip(self.segmented._buttons, ("tab_transcription", "tab_post", "tab_notes", "tab_application", "tab_about")):
            set_help(button, key)
        bindings = {
            "engine_selector": "engine", "device_selector": "device", "language_combo_box": "language",
            "adv_toggle_btn": "advanced", "adv_temperature": "temperature", "adv_beam_size": "beam",
            "adv_best_of": "best", "adv_condition": "condition", "adv_prompt": "initial_prompt",
            "adv_reset_btn": "reset", "theme_selector": "theme", "close_button": "close_settings",
            "github_btn": "repository", "model_status_label": "model_cache",
            "device_status_label": "device", "adv_dependency_hint": "decoding_mode",
        }
        for name, key in bindings.items():
            set_help(getattr(self, name), key)
        for name, toggle in self._application_toggles.items():
            set_help(toggle, name)
        label_help = {"Engine": "engine", "Model": "model", "Device": "device", "Spoken Language": "language",
                      "Temperature": "temperature", "Beam Size (when Temp = 0)": "beam",
                      "Best Of (when Temp > 0)": "best", "Initial Prompt": "initial_prompt"}
        for label in self.stack.widget(0).findChildren(QLabel):
            if label.text() in label_help:
                set_help(label, label_help[label.text()])
        for label in self.stack.widget(3).findChildren(QLabel):
            key = {"Theme": "theme", "Minimize to tray": "minimize_to_tray",
                   "Close to tray instead of exiting": "close_to_tray",
                   "Keep original recording audio": "keep_recording_audio",
                   "Appearance": "appearance_section", "Behavior & Storage": "behavior_section",
                   "Model Memory": "memory_section"}.get(label.text())
            if key:
                set_help(label, key)
        self.model_selector.setToolTip(model_help(self.model_selector.currentText()))

    @staticmethod
    def _get_transcription_settings(parent) -> dict:
        """Read controller-provided settings data while preserving legacy callers."""
        controller = getattr(parent, "app_controller", None)
        getter = getattr(controller, "get_transcription_settings", None)
        if not callable(getter):
            return {}
        try:
            settings = getter()
            return settings if isinstance(settings, dict) else {}
        except (RuntimeError, ValueError):
            return {}

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Segmented tab switcher + stacked pages (replaces QTabWidget).
        self.segmented = SegmentedTabBar()
        self.stack = QStackedWidget()
        self.segmented.currentChanged.connect(self.stack.setCurrentIndex)

        self.content_pane = QFrame()
        self.content_pane.setObjectName("ContentPane")
        pane_layout = QVBoxLayout(self.content_pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.addWidget(self.stack)

        layout.addWidget(self.segmented)
        layout.addWidget(self.content_pane, 1)

        self._create_transcription_tab()
        self._create_post_processing_tab()
        self._create_notes_tab()
        self._create_application_tab()
        self._create_about_tab()

        self.segmented.setCurrentIndex(0)
        self.tab_widget = self.segmented  # backward-compat alias for app_window.py

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch()
        self.close_button = QPushButton("Close")
        self.close_button.setFont(QFont("Inter", 11, QFont.Weight.Medium))
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFixedWidth(100)
        self.close_button.clicked.connect(self.accept)
        button_layout.addWidget(self.close_button)
        layout.addLayout(button_layout)
        self.tab_widget = self.segmented

    def _add_page(self, widget: QWidget, label: str, icon: QIcon | None = None):
        """Register a page: one segment in the switcher + one page in the stack."""
        self.stack.addWidget(widget)
        self.segmented.addTab(label, icon)

    def _create_transcription_tab(self):
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidgetResizable(True)
        # Reserve the scrollbar gutter before Advanced expands so fields keep
        # the same horizontal position and width in both states.
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)
        scroll_area.setWidget(content)
        tab_layout.addWidget(scroll_area)

        # Engine
        engine_layout = QVBoxLayout()
        engine_layout.setSpacing(6)
        engine_label = QLabel("Engine")
        engine_label.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        self.engine_selector = QComboBox()
        self.engine_selector.setFont(QFont("Inter", 13))
        self.engine_selector.setCursor(Qt.CursorShape.PointingHandCursor)
        self.engine_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.engine_selector.setMinimumWidth(260)
        self.engine_selector.setMinimumContentsLength(18)
        engines = self.transcription_settings.get("engines", [])
        for engine_info in engines:
            engine_id = getattr(engine_info, "engine_id", "")
            if not engine_id:
                continue
            self.engine_selector.addItem(self._engine_display_name(engine_id), engine_id)
            self._engine_models[engine_id] = list(getattr(engine_info, "models", ()))
        if not self._engine_models:
            self.engine_selector.addItem("OpenAI Whisper", "openai-whisper")
            self._engine_models["openai-whisper"] = list(self.models)
        engine_layout.addWidget(engine_label)
        engine_layout.addWidget(self.engine_selector)
        layout.addLayout(engine_layout)

        # Model
        model_layout = QVBoxLayout()
        model_layout.setSpacing(6)
        model_label = QLabel("Model")
        model_label.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        self.model_selector = QComboBox()
        self.model_selector.setFont(QFont("Inter", 13))
        self.model_selector.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.model_selector.setMinimumWidth(260)
        self.model_selector.setMinimumContentsLength(18)
        self.model_status_label = QLabel()
        self.model_status_label.setFont(QFont("Inter", 11))
        self.model_status_label.setObjectName("helper_text")
        model_layout.addWidget(model_label)
        model_layout.addWidget(self.model_selector)
        model_layout.addWidget(self.model_status_label)
        layout.addLayout(model_layout)

        # Device
        device_layout = QVBoxLayout()
        device_layout.setSpacing(6)
        device_label = QLabel("Device")
        device_label.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        self.device_selector = QComboBox()
        self.device_selector.setFont(QFont("Inter", 13))
        self.device_selector.setCursor(Qt.CursorShape.PointingHandCursor)
        self.device_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.device_selector.setMinimumWidth(260)
        self.device_selector.setMinimumContentsLength(18)
        self.device_status_label = QLabel()
        self.device_status_label.setFont(QFont("Inter", 11))
        self.device_status_label.setObjectName("helper_text")
        self._populate_devices()
        device_layout.addWidget(device_label)
        device_layout.addWidget(self.device_selector)
        device_layout.addWidget(self.device_status_label)
        layout.addLayout(device_layout)

        # Language
        lang_layout = QVBoxLayout()
        lang_layout.setSpacing(6)
        lang_label = QLabel("Spoken Language")
        lang_label.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        self.language_combo_box = QComboBox()
        self.language_combo_box.setFont(QFont("Inter", 13))
        self.language_combo_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.language_combo_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.language_combo_box.setMinimumWidth(260)
        self.language_combo_box.setMinimumContentsLength(18)
        self.language_combo_box.addItem("Select language...")
        self.language_combo_box.addItems(self.languages)
        self.language_combo_box.model().item(0).setEnabled(False)

        language_name = self.lang_code_to_name.get(self.current_language or "")
        if language_name:
            self.language_combo_box.setCurrentText(language_name)

        self.language_combo_box.currentTextChanged.connect(self._on_language_changed)
        lang_layout.addWidget(lang_label)
        lang_layout.addWidget(self.language_combo_box)
        layout.addLayout(lang_layout)

        # Initialize the cascade before connecting user-change signals.
        selected_engine = str(
            self.transcription_settings.get("selected_engine", "openai-whisper")
        )
        engine_index = self.engine_selector.findData(selected_engine)
        self.engine_selector.setCurrentIndex(max(0, engine_index))
        selected_model = str(
            self.transcription_settings.get("selected_model", self.current_model or "")
        )
        if selected_model:
            self._selected_models[selected_engine] = selected_model
        self._populate_models(selected_engine)
        self._update_device_status()

        self.engine_selector.currentIndexChanged.connect(self._on_engine_changed)
        self.model_selector.currentTextChanged.connect(self._on_model_changed)
        self.device_selector.currentIndexChanged.connect(self._on_device_changed)

        self._create_advanced_section(layout)

        layout.addStretch()
        self._add_page(tab, "Transcription")

    def _create_advanced_section(self, parent_layout: QVBoxLayout):
        """Create the collapsible advanced settings section."""
        # Advanced Section Header (Toggle)
        self.adv_toggle_btn = QPushButton("Advanced Parameters")
        self.adv_toggle_btn.setObjectName("advanced_toggle")
        self.adv_toggle_btn.setIcon(get_icon("chevron-down", theme=self.theme))
        self.adv_toggle_btn.setFont(QFont("Inter", 12))
        self.adv_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Inherit the regular themed button frame from the first paint. A local
        # declaration-only stylesheet conflicts with the appended focus rules.
        
        self.adv_content = QWidget()
        self.adv_content.setVisible(False)
        adv_layout = QVBoxLayout(self.adv_content)
        adv_layout.setContentsMargins(16, 8, 0, 8)
        adv_layout.setSpacing(12)

        # Get initial settings
        parent = self.parent()
        controller = getattr(parent, "app_controller", None)
        self._config_manager = getattr(controller, "config_manager", None)
        if self._config_manager:
            adv_opts = self._config_manager.get_advanced_transcription_options()
        else:
            from core.config_manager import ConfigManager
            adv_opts = ConfigManager.DEFAULT_ADVANCED.copy()

        # Temperature
        temp_layout = QHBoxLayout()
        temp_label = QLabel("Temperature")
        temp_label.setFont(QFont("Inter", 11))
        self.adv_temperature = QDoubleSpinBox()
        self.adv_temperature.setRange(0.0, 1.0)
        self.adv_temperature.setSingleStep(0.1)
        self.adv_temperature.setValue(adv_opts.get("temperature", 0.0))
        temp_layout.addWidget(temp_label)
        temp_layout.addWidget(self.adv_temperature)
        adv_layout.addLayout(temp_layout)

        # Beam Size
        beam_layout = QHBoxLayout()
        beam_label = QLabel("Beam Size (when Temp = 0)")
        beam_label.setFont(QFont("Inter", 11))
        self.adv_beam_size = QSpinBox()
        self.adv_beam_size.setRange(1, 10)
        self.adv_beam_size.setValue(adv_opts.get("beam_size", 5))
        beam_layout.addWidget(beam_label)
        beam_layout.addWidget(self.adv_beam_size)
        adv_layout.addLayout(beam_layout)

        # Best Of
        best_layout = QHBoxLayout()
        best_label = QLabel("Best Of (when Temp > 0)")
        best_label.setFont(QFont("Inter", 11))
        self.adv_best_of = QSpinBox()
        self.adv_best_of.setRange(1, 10)
        self.adv_best_of.setValue(adv_opts.get("best_of", 5))
        best_layout.addWidget(best_label)
        best_layout.addWidget(self.adv_best_of)
        adv_layout.addLayout(best_layout)
        self.adv_dependency_hint = QLabel()
        self.adv_dependency_hint.setObjectName("helper_text")
        self.adv_dependency_hint.setWordWrap(True)
        adv_layout.addWidget(self.adv_dependency_hint)
        
        # Condition on previous text
        condition_row = QHBoxLayout()
        condition_label = QLabel("Condition on Previous Text")
        condition_label.setFont(QFont("Inter", 11))
        set_help(condition_label, "condition")
        self.adv_condition = ToggleSwitch(theme=self.theme)
        self.adv_condition.setAccessibleName("Condition on Previous Text")
        self.adv_condition.setChecked(adv_opts.get("condition_on_previous_text", True))
        condition_row.addWidget(condition_label)
        condition_row.addStretch()
        condition_row.addWidget(self.adv_condition)
        adv_layout.addLayout(condition_row)
        
        # Initial Prompt
        prompt_label = QLabel("Initial Prompt")
        prompt_label.setFont(QFont("Inter", 11))
        self.adv_prompt = PromptTextEdit()
        self.adv_prompt.setFont(QFont("Inter", 11))
        self.adv_prompt.setPlaceholderText("Optional prompt to guide transcription style...")
        self.adv_prompt.setFixedHeight(110)
        prompt_val = adv_opts.get("initial_prompt")
        if prompt_val:
            self.adv_prompt.setPlainText(str(prompt_val))
        adv_layout.addWidget(prompt_label)
        adv_layout.addWidget(self.adv_prompt)
        
        # Reset Button
        reset_btn = self.adv_reset_btn = QPushButton("Reset to defaults")
        reset_btn.setFont(QFont("Inter", 11))
        reset_btn.setIcon(get_icon("rotate-ccw", theme=self.theme))
        reset_btn.clicked.connect(self._reset_advanced_settings)
        adv_layout.addWidget(reset_btn)

        # Connect signals
        self.adv_toggle_btn.clicked.connect(self._toggle_advanced_section)
        self.adv_temperature.valueChanged.connect(self._update_advanced_ui_state)
        self.adv_temperature.valueChanged.connect(self._save_advanced_settings)
        self.adv_beam_size.valueChanged.connect(self._save_advanced_settings)
        self.adv_best_of.valueChanged.connect(self._save_advanced_settings)
        self.adv_condition.toggled.connect(self._save_advanced_settings)
        self._prompt_save_timer = QTimer(self)
        self._prompt_save_timer.setSingleShot(True)
        self._prompt_save_timer.setInterval(300)
        self._prompt_save_timer.timeout.connect(self._save_advanced_settings)
        self.adv_prompt.textChanged.connect(self._on_advanced_prompt_changed)

        parent_layout.addWidget(self.adv_toggle_btn)
        parent_layout.addWidget(self.adv_content)
        
        self._update_advanced_ui_state()

    def _toggle_advanced_section(self):
        visible = not self.adv_content.isVisible()
        self.adv_content.setVisible(visible)
        self.adv_toggle_btn.setIcon(get_icon("chevron-up" if visible else "chevron-down", theme=self.theme))

    def _update_advanced_ui_state(self):
        is_zero = (self.adv_temperature.value() == 0.0)
        self.adv_beam_size.setEnabled(is_zero)
        self.adv_best_of.setEnabled(not is_zero)
        self.adv_dependency_hint.setText("Best Of is used only when Temperature is greater than 0." if is_zero
                                         else "Beam Size is only used when Temperature is 0.")

    def _on_advanced_prompt_changed(self):
        """Batch user typing while keeping programmatic changes immediately durable."""
        if self.adv_prompt.user_edit_in_progress:
            self._prompt_save_timer.start()
        else:
            self._save_advanced_settings()

    def hideEvent(self, event):
        """Persist pending prompt input when Settings closes before the timeout."""
        if hasattr(self, "_prompt_save_timer") and self._prompt_save_timer.isActive():
            self._save_advanced_settings()
        super().hideEvent(event)

    def _save_advanced_settings(self):
        if not self._config_manager:
            return
        self._prompt_save_timer.stop()
            
        # Get existing full options so we don't drop thresholds not in UI
        options = self._config_manager.get_advanced_transcription_options()
        
        options["temperature"] = self.adv_temperature.value()
        options["beam_size"] = self.adv_beam_size.value()
        options["best_of"] = self.adv_best_of.value()
        options["condition_on_previous_text"] = self.adv_condition.isChecked()
        prompt_val = self.adv_prompt.toPlainText().strip()
        options["initial_prompt"] = prompt_val if prompt_val else None

        try:
            self._config_manager.set_advanced_transcription_options(options)
        except (OSError, ValueError):
            # Do not let an I/O exception escape a Qt signal handler. Keep the
            # user's visible draft so it can be retried after fixing storage.
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Settings error", "The transcription settings could not be saved.")
        
    def _reset_advanced_settings(self):
        from core.config_manager import ConfigManager
        defaults = ConfigManager.DEFAULT_ADVANCED.copy()
        
        self.adv_temperature.setValue(defaults["temperature"])
        self.adv_beam_size.setValue(defaults["beam_size"])
        self.adv_best_of.setValue(defaults["best_of"])
        self.adv_condition.setChecked(defaults["condition_on_previous_text"])
        self.adv_prompt.setPlainText(defaults["initial_prompt"] or "")
        
        # Saving happens automatically via valueChanged signals for spinboxes, 
        # but to be safe we explicitly save.
        self._save_advanced_settings()

    @staticmethod
    def _engine_display_name(engine_id: str) -> str:
        """Return a presentation label for a stable engine identifier."""
        names = {
            "openai-whisper": "OpenAI Whisper",
            "faster-whisper": "Faster Whisper",
        }
        return names.get(engine_id, engine_id)

    def _populate_models(self, engine_id: str) -> None:
        """Replace model options for an engine without intermediate load requests."""
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        for option in self._engine_models.get(engine_id, []):
            model_name = getattr(option, "name", option if isinstance(option, str) else "")
            if model_name:
                self.model_selector.addItem(str(model_name))
        selected_model = self._selected_models.get(engine_id, "")
        if selected_model and self.model_selector.findText(selected_model) >= 0:
            self.model_selector.setCurrentText(selected_model)
        self.model_selector.blockSignals(False)
        self._update_model_status()

    def _populate_devices(self) -> None:
        """Add device modes and disable explicit GPU when CUDA is unavailable."""
        display_names = {"auto": "Auto", "gpu": "GPU", "cpu": "CPU"}
        devices = self.transcription_settings.get("devices", [])
        for device in devices:
            mode = str(getattr(device, "mode", ""))
            if not mode:
                continue
            self.device_selector.addItem(display_names.get(mode, mode), mode)
            item = self.device_selector.model().item(self.device_selector.count() - 1)
            if item is not None and not bool(getattr(device, "available", False)):
                item.setEnabled(False)
        if self.device_selector.count() == 0:
            self.device_selector.addItem("Auto", "auto")
            self.device_selector.addItem("CPU", "cpu")

        selected_mode = str(
            self.transcription_settings.get("selected_device_mode", "auto")
        )
        selected_index = self.device_selector.findData(selected_mode)
        self.device_selector.setCurrentIndex(max(0, selected_index))

    def _update_model_status(self) -> None:
        """Show whether the selected engine model is cached locally."""
        engine_id = str(self.engine_selector.currentData() or "")
        model_name = self.model_selector.currentText()
        downloaded = False
        for option in self._engine_models.get(engine_id, []):
            option_name = getattr(option, "name", option if isinstance(option, str) else "")
            if option_name == model_name:
                downloaded = bool(getattr(option, "downloaded", False))
                break
        self.model_status_label.setText(
            "Downloaded" if downloaded else "Not downloaded — confirmation required before download"
        )
        self.model_selector.setToolTip(model_help(model_name))

    def _update_device_status(self) -> None:
        """Explain the current Auto device resolution inline."""
        mode = str(self.device_selector.currentData() or "auto")
        resolved = "cpu"
        for device in self.transcription_settings.get("devices", []):
            if getattr(device, "mode", "") == mode:
                resolved = str(getattr(device, "resolved_device", "cpu"))
                break
        self.device_status_label.setText(
            f"Auto currently resolves to {resolved.upper()}" if mode == "auto" else ""
        )
        self.device_status_label.setVisible(mode == "auto")

    def _emit_transcription_selection(self) -> None:
        """Emit the complete cascade selection as one controller payload."""
        engine_id = str(self.engine_selector.currentData() or "")
        model_name = self.model_selector.currentText()
        device_mode = str(self.device_selector.currentData() or "auto")
        if engine_id and model_name:
            self._selected_models[engine_id] = model_name
            self.modelChangeRequested.emit(
                {"engine": engine_id, "model": model_name, "device_mode": device_mode}
            )

    def _create_post_processing_tab(self):
        """Mount provider settings inside the existing navigation and scroll area."""
        from widgets.settings_panel import PostProcessingPanel
        self.post_panel = PostProcessingPanel(self.parent().app_controller)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.post_panel)
        self._add_page(scroll, "Post-processing")

    def _create_notes_tab(self):
        """Mount folder management without changing the Settings navigation."""
        from widgets.settings_panel import NotesPanel
        self.notes_panel = NotesPanel(self.parent().app_controller)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.notes_panel)
        self._add_page(scroll, "Notes")

    def _create_application_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # Appearance
        app_layout = QVBoxLayout()
        app_label = QLabel("Appearance")
        app_label.setObjectName("section_title")
        app_label.setFont(QFont("Inter", 14, QFont.Weight.DemiBold))
        app_layout.addWidget(app_label)

        theme_layout = QVBoxLayout()
        theme_label = QLabel("Theme")
        theme_label.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        self.theme_selector = QComboBox()
        self.theme_selector.setFont(QFont("Inter", 13))
        self.theme_selector.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_selector.addItem("Light", "light")
        self.theme_selector.addItem("Dark", "dark")
        if self.current_theme:
            self.theme_selector.setCurrentIndex(max(0, self.theme_selector.findData(self.current_theme)))
        self.theme_selector.currentIndexChanged.connect(lambda: self._on_theme_changed(self.theme_selector.currentData()))
        theme_layout.addWidget(theme_label)
        theme_layout.addWidget(self.theme_selector)
        app_layout.addLayout(theme_layout)
        layout.addLayout(app_layout)

        layout.addSpacing(10)

        # Persisted window behavior and microphone audio retention.
        behav_label = QLabel("Behavior & Storage")
        behav_label.setObjectName("section_title")
        behav_label.setFont(QFont("Inter", 14, QFont.Weight.DemiBold))
        layout.addWidget(behav_label)

        def create_toggle_row(text, tooltip):
            """Build a label and a working application preference toggle."""
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(text)
            lbl.setFont(QFont("Inter", 12))
            lbl.setToolTip(tooltip)
            row.addWidget(lbl)
            row.addStretch()
            from widgets.toggle_switch import ToggleSwitch
            toggle = ToggleSwitch(theme=self.theme)
            toggle.setToolTip(tooltip)
            row.addWidget(toggle)
            return row, toggle

        row1, self._tray_toggle = create_toggle_row("Minimize to tray", "Keep the app in the system tray when minimized.")
        row2, self._close_tray_toggle = create_toggle_row("Close to tray instead of exiting", "Use Exit in the tray menu to quit completely.")
        row3, self._keep_audio_toggle = create_toggle_row("Keep original recording audio", "Retain microphone WAV files in the app-local recordings folder. Imported files are not copied.")
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)
        self._application_toggles = {
            "minimize_to_tray": self._tray_toggle,
            "close_to_tray": self._close_tray_toggle,
            "keep_recording_audio": self._keep_audio_toggle,
        }
        self._reload_application_settings()
        for name, toggle in self._application_toggles.items():
            toggle.toggled.connect(lambda checked, key=name: self._set_application_setting(key, checked))
        self.parent().app_controller.application_settings_changed.connect(self._reload_application_settings)

        # One exclusive policy rather than independent toggles. All alternatives
        # remain available, even while a captured policy runs in the background.
        memory_label = QLabel("Model Memory")
        memory_label.setObjectName("section_title")
        layout.addWidget(memory_label)
        self.memory_group = QButtonGroup(self)
        self.memory_group.setExclusive(True)
        self.memory_buttons = {}
        for mode, text in (("keep_loaded", "Keep model loaded"),
                           ("release_before_postprocessing", "Release before post-processing"),
                           ("release_after_transcription", "Release after transcription")):
            button = QPushButton(text)
            button.setObjectName("MemoryMode")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(text)
            set_help(button, "memory_" + mode)
            self.memory_group.addButton(button)
            self.memory_buttons[mode] = button
            button.clicked.connect(lambda checked=False, value=mode: self._set_memory_mode(value))
            layout.addWidget(button)
        self.model_state_label = QLabel()
        set_help(self.model_state_label, "memory_state")
        layout.addWidget(self.model_state_label)
        self.load_model_button = QPushButton("Load Model Now")
        set_help(self.load_model_button, "memory_load_now")
        self.load_model_button.clicked.connect(self.parent().app_controller.load_model_now)
        layout.addWidget(self.load_model_button)
        self.unload_model_button = QPushButton("Unload Model Now")
        set_help(self.unload_model_button, "memory_unload_now")
        self.unload_model_button.clicked.connect(self.parent().app_controller.unload_model_now)
        layout.addWidget(self.unload_model_button)
        controller = self.parent().app_controller
        for signal in (controller.model_memory_changed, controller.model_memory_busy_changed,
                       controller.model_loading, controller.model_ready, controller.model_load_failed,
                       controller.processing_started, controller.processing_finished,
                       controller.recording_state_changed, controller.shutdown_ready):
            signal.connect(self._reload_model_memory)
        self._reload_model_memory()

        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(tab)
        self._add_page(scroll, "Application")

    def _reload_model_memory(self):
        """Synchronize exclusive selection and manual-release availability."""
        controller = self.parent().app_controller
        mode = controller.config_manager.get_model_memory_mode()
        self.memory_buttons[mode].setChecked(True)
        self.unload_model_button.setEnabled(controller.can_unload_model())
        self.load_model_button.setEnabled(controller.can_load_model())
        self.model_state_label.setText(controller.model_memory_status())

    def _set_memory_mode(self, mode):
        """Persist one mode and restore the prior checked control on storage errors."""
        try:
            self.parent().app_controller.set_model_memory_mode(mode)
        except (ValueError, OSError):
            from PyQt6.QtWidgets import QMessageBox
            self._reload_model_memory()
            QMessageBox.warning(self, "Settings error", "The model memory setting could not be saved.")

    def _reload_application_settings(self):
        settings = self.parent().app_controller.get_application_settings()
        for name, toggle in self._application_toggles.items():
            toggle.blockSignals(True)
            toggle.setChecked(settings[name])
            toggle.blockSignals(False)
            toggle._on_toggled(settings[name])

    def _set_application_setting(self, name, enabled):
        try:
            self.parent().app_controller.set_application_setting(name, enabled)
        except (ValueError, OSError):
            from PyQt6.QtWidgets import QMessageBox
            self._reload_application_settings()
            QMessageBox.warning(self, "Settings error", "The application setting could not be saved.")

    def _create_about_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("FeinNote")
        title.setObjectName("app_heading")
        set_help(title, "app")
        title.setFont(QFont("Inter", 20, QFont.Weight.Bold))
        layout.addWidget(title)

        version = QLabel("v0.2.1")
        version.setFont(QFont("Inter", 12))
        version.setObjectName("helper_text")
        set_help(version, "version")
        layout.addWidget(version)

        info_text = """
        <style>
            ul { margin-top: 5px; margin-bottom: 5px; padding-left: 20px; }
            li { margin-bottom: 5px; }
        </style>
        <p>Local audio transcription powered by <b>Whisper and Faster Whisper</b>.<br>
        A portable, local-first workspace for recordings, notes and folders.</p>
        <p>Audio stays on your device during transcription. Optional LLM refinement
        edits text using a local server or your chosen cloud provider.</p>
        <p>Export TXT, DOCX or PDF documents, or SRT/VTT subtitles from original
        transcription timestamps. Tray controls keep recording close at hand.</p>

        <p><b>Tips:</b></p>
        <ul>
            <li>Select a history card to open it</li>
            <li>Use Copy and Export in the editor area</li>
            <li>Right-click a note card for additional actions</li>
            <li>Organize notes in folders and optionally refine their text</li>
        </ul>
        """
        info_label = QLabel(info_text)
        set_help(info_label, "tab_about")
        info_label.setFont(QFont("Inter", 12))
        info_label.setWordWrap(True)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)

        layout.addStretch()

        github_btn = QPushButton("  LokiSkardina/FeinNote")
        github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        github_btn.setFont(QFont("Inter", 11))
        github_btn.setMinimumWidth(220)

        github_btn.setIcon(get_icon("external-link", theme=self.theme))
        github_btn.setIconSize(QSize(18, 18))

        github_btn.setObjectName("github_btn")
        self.github_btn = github_btn
        github_btn.clicked.connect(self._open_github)
        layout.addWidget(github_btn)

        self._add_page(tab, "About")

    def _on_engine_changed(self, _index: int):
        """Refresh model choices and emit the completed engine cascade."""
        engine_id = str(self.engine_selector.currentData() or "")
        self._populate_models(engine_id)
        self._emit_transcription_selection()

    def _on_model_changed(self, _text: str):
        """Refresh cache status and emit the completed model cascade."""
        self._update_model_status()
        self._emit_transcription_selection()

    def _on_device_changed(self, _index: int):
        """Refresh Auto resolution and emit the completed device cascade."""
        self._update_device_status()
        self._emit_transcription_selection()

    def _on_language_changed(self, text: str):
        if text != "Select language...":
            code = self.lang_name_to_code.get(text)
            self.languageChangeRequested.emit(code)

    def _on_theme_changed(self, text: str):
        self.themeChangeRequested.emit(text)

    def _open_github(self):
        QDesktopServices.openUrl(QUrl("https://github.com/LokiSkardina/WhisperNote"))

    def set_selected_model(self, model_name: str):
        self.model_selector.blockSignals(True)
        self.model_selector.setCurrentText(model_name)
        self.model_selector.blockSignals(False)
        self.current_model = model_name
        engine_id = str(self.engine_selector.currentData() or "")
        if engine_id:
            self._selected_models[engine_id] = model_name
        self._update_model_status()

    def restore_transcription_selection(self):
        """Restore the actual selection after a cancelled or rejected load request."""
        settings = self._get_transcription_settings(self.parent())
        if not settings:
            return
        self.transcription_settings = settings
        self._engine_models = {info.engine_id: list(info.models) for info in settings["engines"]}
        engine = settings["selected_engine"]
        # Rebuild from committed state so a failed candidate cannot poison per-engine memory.
        self._selected_models = dict(settings.get("selected_models", {}))
        self._selected_models[engine] = settings["selected_model"]
        self.engine_selector.blockSignals(True)
        self.engine_selector.setCurrentIndex(self.engine_selector.findData(engine))
        self.engine_selector.blockSignals(False)
        self._populate_models(engine)
        self.device_selector.blockSignals(True)
        self.device_selector.setCurrentIndex(self.device_selector.findData(settings["selected_device_mode"]))
        self.device_selector.blockSignals(False)
        self._update_device_status()

        language = self.lang_code_to_name.get(settings.get("selected_language"))
        if language:
            self.set_selected_language(language)
        self.language_combo_box.setEnabled(self.engine_selector.isEnabled() and not settings["selected_model"].endswith(".en"))

    def set_selected_language(self, language_name: str):
        self.language_combo_box.blockSignals(True)
        self.language_combo_box.setCurrentText(language_name)
        self.language_combo_box.blockSignals(False)

    def force_language(self, language_code: str):
        name = self.lang_code_to_name.get(language_code, "")
        if name:
            self.set_selected_language(name)
        self.language_combo_box.setEnabled(False)

    def set_controls_enabled(self, enabled: bool):
        self.post_panel.setEnabled(enabled)
        self.notes_panel.setEnabled(enabled)
        self.engine_selector.setEnabled(enabled)
        self.model_selector.setEnabled(enabled)
        self.device_selector.setEnabled(enabled)
        if enabled:
            model_requires_english = self.model_selector.currentText().endswith(".en")
            self.language_combo_box.setEnabled(not model_requires_english)
        else:
            self.language_combo_box.setEnabled(False)

    def update_theme(self, theme: dict):
        self.theme = theme
        self._apply_theme()

    def _apply_segmented_styles(self) -> None:
        if not self.theme:
            return

        T = self.theme
        self.segmented.setStyleSheet(f"""
            QFrame#SegTrack {{
                background: {T['SIDEBAR_BG']};
                border: 1px solid {T['BORDER']};
                border-radius: 10px;
            }}
            QPushButton#SegButton {{
                background: transparent;
                color: {T['GRAY_TEXT']};
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 7px 10px;
                font-weight: 500;
            }}
            QPushButton#SegButton:hover:!checked {{
                color: {T['DARK_TEXT']};
                background: {T['HOVER_BG']};
            }}
            QPushButton#SegButton:checked {{
                background: {T['WHITE_BG']};
                color: {T['DARK_TEXT']};
                border: 1px solid {T['BORDER']};
            }}
        """)

        self.content_pane.setFrameShape(QFrame.Shape.NoFrame)
        self.content_pane.setStyleSheet(f"""
            QFrame#ContentPane {{
                background: {T['WHITE_BG']};
                border: none;
                border-radius: 0px;
            }}
        """)

    def _apply_theme(self):
        if not self.theme:
            return

        T = self.theme
        self.setStyleSheet(settings_stylesheet(T))
        for button in self.memory_buttons.values():
            button.setStyleSheet(
                f"QPushButton {{ text-align: left; color: {T['DARK_TEXT']}; background: {T['SIDEBAR_BG']}; "
                f"border: 1px solid {T['BORDER']}; border-radius: 12px; padding: 8px 12px; }} "
                f"QPushButton:hover {{ background: {T['BUTTON_HOVER_BG']}; }} "
                f"QPushButton:checked {{ background: {T['WHITE_BG']}; border-color: {T['BLUE_ACCENT']}; font-weight: 600; }} "
                f"QPushButton:focus {{ border-color: {T['BLUE_ACCENT']}; }}")
        # Hide native arrow buttons only; text input, keys and wheel still work.
        for spin in self.findChildren(QAbstractSpinBox):
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.post_panel.update_theme(T)
        self.notes_panel.update_theme(T)
        self.adv_reset_btn.setIcon(get_icon("rotate-ccw", theme=T))
        self.github_btn.setIcon(get_icon("external-link", theme=T))
        self.adv_toggle_btn.setIcon(get_icon("chevron-up" if not self.adv_content.isHidden() else "chevron-down", theme=T))

        self._apply_segmented_styles()

        # Propagate theme to ToggleSwitch instances
        for toggle in (self._tray_toggle, self._close_tray_toggle, self._keep_audio_toggle, self.adv_condition):
            if toggle:
                toggle.apply_theme(T)
