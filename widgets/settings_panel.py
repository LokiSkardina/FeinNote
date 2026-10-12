# Responsible for: settings and configuration user interface controls

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QDoubleSpinBox, QSpinBox, QPushButton, QMessageBox, QCheckBox, QDialog,
)
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap
from PyQt6.QtCore import Qt
from core.postprocessing.service import DEFAULT_PROMPT
from widgets.sidebar_controls import folder_icon
from utils.icon_manager import get_icon, resolve_name
from utils.themes import light_theme
from widgets.themed_controls import ThemedComboBox as QComboBox
from widgets.settings_help import set_help
from widgets.toggle_switch import ToggleSwitch


def configure_form(form):
    """Place labels above fields using common settings spacing."""
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setContentsMargins(0, 0, 0, 0)
    form.setVerticalSpacing(10)


def section_title(text, help_key):
    """Create a section header styled by the shared settings stylesheet."""
    label = QLabel(text)
    label.setObjectName("section_title")
    set_help(label, help_key)
    return label


class ModelSelector(QComboBox):
    """Search catalog labels while persisting IDs and permitting manual model names."""

    def __init__(self):
        """Use the existing themed combo with case-insensitive substring completion."""
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer().setFilterMode(Qt.MatchFlag.MatchContains)

    def text(self):
        """Resolve exact catalog labels even while an editable combo retains its old index."""
        index = self.findText(self.currentText(), Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchCaseSensitive)
        if index >= 0:
            return self.itemData(index) or self.currentText()
        return self.currentText().strip()

    def setText(self, value):
        """Restore a saved ID even when no catalog is available."""
        index = self.findData(value)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setCurrentIndex(-1)
            self.setEditText(value)

    def set_catalog(self, models):
        """Replace suggestions without changing the user's current ID or draft."""
        selected = self.text()
        self.clear()
        for model in models:
            self.addItem(f"{model['name']} ({model['id']})", model["id"])
        self.setText(selected)


class PostProcessingPanel(QWidget):
    """Edit non-secret provider configuration through AppController."""

    def __init__(self, controller, parent=None):
        """Build a provider form and masked credential replacement field."""
        super().__init__(parent)
        self.setFont(QFont("Inter", 12))
        self.controller = controller
        self._testing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        layout.addWidget(section_title("Provider & Model", "provider_section"))
        form = QFormLayout()
        configure_form(form)
        self.provider = QComboBox()
        for label, value in (("Disabled", "disabled"), ("Local server", "local"), ("Cloud API", "cloud")):
            self.provider.addItem(label, value)
        form.addRow("Provider", self.provider)
        self.preset = QComboBox()
        for label, value in (("Custom OpenAI-compatible", "custom"), ("OpenRouter", "openrouter"), ("LM Studio (Local)", "lm_studio")):
            self.preset.addItem(label, value)
        form.addRow("Preset", self.preset)
        set_help(self.preset, "preset")
        self.connection = QWidget()
        fields = QFormLayout(self.connection)
        configure_form(fields)
        self.url, self.model, self.key = QLineEdit(), ModelSelector(), QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Leave blank to keep saved key")
        self.clear_key = QCheckBox("Remove saved API key")
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(0.1)
        fields.addRow("Base URL", self.url)
        fields.addRow("Model", self.model)
        self.refresh_models = QPushButton("Refresh Models")
        set_help(self.refresh_models, "refresh_models")
        self.refresh_models.clicked.connect(self._refresh_models)
        fields.addRow(self.refresh_models)
        fields.addRow("API key", self.key)
        fields.addRow(self.clear_key)
        self.refinement = QWidget()
        refinement_layout = QVBoxLayout(self.refinement)
        refinement_layout.setContentsMargins(0, 0, 0, 0)
        refinement_layout.setSpacing(12)
        refinement_layout.addWidget(section_title("Refinement Parameters", "refinement_section"))
        parameters = QFormLayout()
        configure_form(parameters)
        refinement_layout.addLayout(parameters)
        parameters.addRow("Temperature", self.temperature)
        self.prompt = QTextEdit()
        self.prompt.setFixedHeight(180)
        parameters.addRow("Prompt", self.prompt)
        reset = QPushButton("Reset prompt to default")
        self._icon_buttons = {reset: "rotate-ccw"}
        reset.clicked.connect(lambda: self.prompt.setPlainText(DEFAULT_PROMPT))
        parameters.addRow(reset)
        layout.addLayout(form)
        layout.addWidget(self.connection)
        actions = QHBoxLayout()
        save, self.test = QPushButton("Save settings"), QPushButton("Test connection")
        self._icon_buttons.update({save: "save", self.test: "server"})
        save.clicked.connect(self._save)
        self.test.clicked.connect(self._test)
        actions.addWidget(save)
        actions.addWidget(self.test)
        layout.addLayout(actions)
        self.message = QLabel("Cloud providers receive transcription text only. Audio stays local.")
        self.message.setWordWrap(True)
        self.message.setObjectName("helper_text")
        set_help(self.message, "post_status")
        layout.addWidget(self.message)
        layout.addWidget(self.refinement)
        layout.addStretch()
        settings = controller.get_post_settings()
        self.preset.setCurrentIndex(max(0, self.preset.findData(settings.get("preset", "custom"))))
        self.provider.setCurrentIndex(max(0, self.provider.findData(settings["provider"])))
        self.url.setText(settings["base_url"])
        self.model.setText(settings["model"])
        self.temperature.setValue(settings["temperature"])
        self.prompt.setPlainText(settings["prompt"])
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self._catalog_id = None
        controller.models_received.connect(self._models_received)
        self._provider_changed()
        controller.connection_tested.connect(self._tested)
        for button in self.findChildren(QPushButton):
            button.setMinimumHeight(32)
        for widget, key in ((self.provider, "provider"), (self.preset, "preset"), (self.url, "url"), (self.model, "post_model"),
                            (self.key, "key"), (self.clear_key, "clear_key"), (self.temperature, "post_temperature"),
                            (self.prompt, "post_prompt"), (save, "save_post"), (self.test, "test"), (reset, "reset_post")):
            set_help(widget, key)
            label = fields.labelForField(widget) or form.labelForField(widget) or parameters.labelForField(widget)
            if label is not None:
                set_help(label, key)

    def update_theme(self, theme):
        """Refresh recolored action icons without changing provider form values."""
        for button, name in self._icon_buttons.items():
            button.setIcon(get_icon(name, theme=theme))

    def _provider_changed(self):
        enabled = self.provider.currentData() != "disabled"
        self.connection.setEnabled(enabled)
        self.refinement.setEnabled(enabled)
        self.test.setEnabled(enabled and not self._testing)
        self.key.clear()
        self.clear_key.setChecked(False)

    def _save(self):
        try:
            self.controller.save_post_settings(dict(
                provider=self.provider.currentData(), preset=self.preset.currentData(), base_url=self.url.text(), model=self.model.text(),
                temperature=self.temperature.value(), prompt=self.prompt.toPlainText()),
                "" if self.clear_key.isChecked() else (self.key.text() or None))
            self.key.clear()
            self.clear_key.setChecked(False)
            self.message.setText("Settings saved. API keys are kept in Windows Credential Manager.")
            return True
        except (ValueError, RuntimeError, OSError) as error:
            # Service errors are controlled messages; never display transport exceptions.
            self.message.setText(str(error))
            return False

    def _preset_changed(self):
        # Only explicit preset selection changes endpoints; loading old settings
        # never infers a preset or rewrites a working custom configuration.
        preset = self.preset.currentData()
        if preset == "custom":
            return
        kind, url = (("cloud", "https://openrouter.ai/api/v1") if preset == "openrouter"
                     else ("local", "http://localhost:1234/v1"))
        draft_key = self.key.text()
        self.provider.setCurrentIndex(self.provider.findData(kind))
        self.key.setText(draft_key)
        self.url.setText(url)

    def _refresh_models(self):
        import uuid
        self._catalog_id = uuid.uuid4().hex
        self._catalog_connection = (self.provider.currentData(), self.url.text())
        self.refresh_models.setEnabled(False)
        self.message.setText("Loading model catalog…")
        try:
            self.controller.refresh_post_models(dict(provider=self.provider.currentData(), base_url=self.url.text()),
                                                "" if self.clear_key.isChecked() else (self.key.text() or None), self._catalog_id)
        except (ValueError, RuntimeError, OSError):
            self._models_received({"id": self._catalog_id, "error": "Check the provider URL and credentials."})

    def _models_received(self, result):
        if result["id"] != self._catalog_id:
            return
        self.refresh_models.setEnabled(True)
        if self._catalog_connection != (self.provider.currentData(), self.url.text()):
            self.message.setText("Connection changed. Refresh the catalog again when ready.")
            return
        if "error" in result:
            self.message.setText(result["error"])
            return
        self.model.set_catalog(result["models"])
        self.message.setText("Catalog loaded. Availability here does not guarantee chat completion access.")

    def _test(self):
        if self._save():
            self._testing = True
            self.test.setEnabled(False)
            self.message.setText("Testing connection…")
            try:
                self.controller.test_post_connection()
            except Exception:
                # A dispatch failure may occur before a worker can emit completion.
                self._tested("Connection test failed to start. Please try again.")

    def _tested(self, message):
        self._testing = False
        self.test.setEnabled(self.provider.currentData() != "disabled")
        self.message.setText(message)


class NotesPanel(QWidget):
    """Manage folder appearance, retention, and confirmed manual cleanup."""

    def __init__(self, controller, parent=None):
        """Build a compact form backed by the existing notes controller."""
        super().__init__(parent)
        self.setFont(QFont("Inter", 12))
        self.theme = light_theme
        self._icon_name = "folder"
        self.app_controller = controller
        self.controller = controller.notes_controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        layout.addWidget(section_title("Folder appearance", "folder_section"))
        form = QFormLayout()
        configure_form(form)
        self.folders, self.name = QComboBox(), QLineEdit()
        self.color, self.icon, self.mode = QComboBox(), QPushButton(), QComboBox()
        self.icon.clicked.connect(self._choose_icon)
        self.color.currentIndexChanged.connect(self._refresh_icon)
        for color in ("#64748b", "#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#ec4899"):
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(color))
            self.color.addItem(QIcon(swatch), color, color)
        for label, mode in (("Disabled", "disabled"), ("Keep latest notes", "count"), ("Delete older notes", "days")):
            self.mode.addItem(label, mode)
        self.value = QSpinBox()
        self.value.setRange(1, 1000000)
        self.value.setValue(30)
        for label, widget in (("Folder", self.folders), ("Name", self.name), ("Color", self.color),
                              ("Icon", self.icon), ("Automatic cleanup", self.mode)):
            form.addRow(label, widget)
        self.value_label = QLabel("Retention value")
        form.addRow(self.value_label, self.value)
        layout.addLayout(form)
        actions = QHBoxLayout()
        new, save, self.delete = QPushButton("New folder"), QPushButton("Save folder"), QPushButton("Delete folder")
        self._icon_buttons = {new: "folder-plus", save: "save", self.delete: "trash-2"}
        self.delete.setProperty("danger", True)
        new.clicked.connect(self._new)
        save.clicked.connect(self._save)
        self.delete.clicked.connect(self._delete)
        for button in (new, save, self.delete):
            actions.addWidget(button)
        layout.addLayout(actions)
        info = QLabel("Cleanup runs at startup and after new notes. Deletion is permanent.")
        info.setWordWrap(True)
        info.setObjectName("helper_text")
        set_help(info, "cleanup_schedule")
        layout.addWidget(info)
        layout.addSpacing(8)
        layout.addWidget(section_title("Manual cleanup", "cleanup_section"))
        confirmation_row = QHBoxLayout()
        confirmation_label = QLabel("Confirm note deletion")
        self.confirm_deletion = ToggleSwitch(theme=self.theme)
        self.confirm_deletion.setAccessibleName("Confirm note deletion")
        confirmation_help = "Ask before deleting one note. Bulk cleanup always requires confirmation."
        confirmation_label.setToolTip(confirmation_help)
        self.confirm_deletion.setToolTip(confirmation_help)
        confirmation_row.addWidget(confirmation_label)
        confirmation_row.addStretch()
        confirmation_row.addWidget(self.confirm_deletion)
        layout.addLayout(confirmation_row)
        self._reload_confirmation()
        self.confirm_deletion.toggled.connect(self._set_confirmation)
        self.app_controller.note_preferences_changed.connect(self._reload_confirmation)
        all_notes = QPushButton("Delete all notes")
        folder_notes = QPushButton("Delete notes in selected folder")
        old_notes = QPushButton("Delete older notes in all folders")
        self.days = QSpinBox()
        self.days.setRange(1, 1000000)
        self.days.setValue(30)
        for button, scope in ((all_notes, "all"), (folder_notes, "folder")):
            button.clicked.connect(lambda checked=False, s=scope: self._cleanup(s))
            button.setProperty("danger", True)
            self._icon_buttons[button] = "trash-2"
            layout.addWidget(button)
        days_label = QLabel("Delete notes older than (days)")
        set_help(days_label, "manual_days")
        layout.addWidget(days_label)
        layout.addWidget(self.days)
        old_notes.clicked.connect(lambda: self._cleanup("days"))
        old_notes.setProperty("danger", True)
        self._icon_buttons[old_notes] = "trash-2"
        layout.addWidget(old_notes)
        self.message = QLabel()
        set_help(self.message, "folder_status")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addStretch()
        self.folders.currentIndexChanged.connect(self._selected)
        self.mode.currentIndexChanged.connect(self._cleanup_mode_changed)
        self.controller.folders_changed.connect(self.reload)
        self.reload()
        for button in self.findChildren(QPushButton):
            button.setMinimumHeight(32)
        for widget, key in ((self.folders, "folder"), (self.name, "name"), (self.color, "color"),
                            (self.icon, "icon"), (self.mode, "cleanup"), (self.days, "manual_days"),
                            (new, "new_folder"), (save, "save_folder"), (self.delete, "delete_folder"),
                            (all_notes, "delete_all"), (folder_notes, "delete_folder_notes"), (old_notes, "delete_old")):
            set_help(widget, key)
            label = form.labelForField(widget)
            if label is not None:
                set_help(label, key)

    def _reload_confirmation(self):
        """Synchronize the persisted single-note preference without triggering a write."""
        self.confirm_deletion.blockSignals(True)
        self.confirm_deletion.setChecked(self.app_controller.confirm_note_deletion())
        self.confirm_deletion.blockSignals(False)
        self.confirm_deletion._on_toggled(self.confirm_deletion.isChecked())

    def _set_confirmation(self, enabled):
        """Save the preference and restore the control if persistence fails."""
        try:
            self.app_controller.set_confirm_note_deletion(enabled)
        except (ValueError, OSError):
            self._reload_confirmation()
            self.message.setText("The note deletion preference could not be saved.")

    def _cleanup_mode_changed(self):
        mode = self.mode.currentData()
        set_help(self.value, "count" if mode == "count" else "days")
        set_help(self.value_label, "count" if mode == "count" else "days")
        self.value_label.setText("Number of notes to keep" if mode == "count" else "Delete notes older than (days)")
        self.value_label.setVisible(mode != "disabled")
        self.value.setVisible(mode != "disabled")
        self.value.setEnabled(mode != "disabled")

    def _refresh_icon(self):
        self.icon.setText(self._icon_name)
        self.icon.setIcon(get_icon(self._icon_name, color=self.color.currentData() or "#64748b"))

    def _choose_icon(self):
        from widgets.icon_picker import IconPicker
        picker = IconPicker(self._icon_name, self.color.currentData(), self.theme, self)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.selected_name():
            self._icon_name = picker.selected_name()
            self._refresh_icon()

    def update_theme(self, theme):
        """Update action icons and the next picker while keeping unsaved edits."""
        self.theme = theme
        self.confirm_deletion.apply_theme(theme)
        for button, name in self._icon_buttons.items():
            button.setIcon(get_icon(name, theme=theme))
        self._refresh_icon()

    def reload(self):
        """Refresh folder choices while retaining the settings selection."""
        current = self.folders.currentData() or self.controller.active_folder
        self.folders.blockSignals(True)
        self.folders.clear()
        for folder in self.controller.list_folders():
            self.folders.addItem(folder_icon(folder), folder["name"], folder["id"])
        self.folders.setCurrentIndex(max(0, self.folders.findData(current)))
        self.folders.blockSignals(False)
        self._selected()

    def _selected(self):
        folder = next((f for f in self.controller.list_folders() if f["id"] == self.folders.currentData()), None)
        if folder is None:
            return
        self.name.setText(folder["name"])
        self.name.setEnabled(folder["id"] != "inbox")
        self.delete.setEnabled(folder["id"] != "inbox")
        if self.color.findData(folder["color"]) < 0:
            self.color.addItem(folder["color"], folder["color"])
        self.color.setCurrentIndex(self.color.findData(folder["color"]))
        self._icon_name = resolve_name(folder["icon"])
        self._refresh_icon()
        self.mode.setCurrentIndex(self.mode.findData(folder["cleanup"]["mode"]))
        self.value.setValue(folder["cleanup"]["value"])
        self.value.setEnabled(self.mode.currentData() != "disabled")
        self._cleanup_mode_changed()

    def _new(self):
        self.folders.setCurrentIndex(-1)
        self.name.setEnabled(True)
        self.name.clear()
        self.name.setFocus()
        self.delete.setEnabled(False)
        self.mode.setCurrentIndex(0)

    def _save(self):
        try:
            folder_id = self.controller.save_folder(self.name.text(), self.color.currentData(), self._icon_name,
                {"mode": self.mode.currentData(), "value": self.value.value()}, self.folders.currentData())
            self.folders.setCurrentIndex(self.folders.findData(folder_id))
            self.message.setText("Folder saved.")
        except (ValueError, OSError) as error:
            self.message.setText(str(error))

    def _delete(self):
        folder_id = self.folders.currentData()
        if not folder_id or folder_id == "inbox":
            return
        count = len(self.controller.cleanup_preview("folder", folder_id=folder_id))
        if QMessageBox.question(self, "Delete folder", f"Delete {self.folders.currentText()} and move {count} notes to Inbox?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            if not self.controller.delete_folder(folder_id):
                self.message.setText("Could not save the active note. Folder deletion was cancelled.")
                return
            self.app_controller.select_folder(self.controller.active_folder)
        except (ValueError, OSError) as error:
            self.message.setText(str(error))
            self.controller.load_notes()

    def _cleanup(self, scope):
        if scope == "folder":
            folder_id = self.folders.currentData()
            if folder_id is None:
                return
            paths = self.controller.cleanup_preview("folder", folder_id=folder_id)
            description = f"in {self.folders.currentText()}"
        else:
            paths = self.controller.cleanup_preview(scope, self.days.value())
            description = "in all folders" if scope == "all" else f"older than {self.days.value()} days in all folders"
        if not paths:
            self.message.setText("No matching notes.")
            return
        if QMessageBox.question(self, "Permanently delete notes", f"Permanently delete {len(paths)} notes {description}? This cannot be undone.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.controller.delete_paths(paths)
            self.message.setText(f"Queued {len(paths)} notes for deletion.")
