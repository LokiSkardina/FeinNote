# Responsible for: searchable, lazily rendered Lucide folder icon selection.

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QListView, QVBoxLayout

from utils.icon_manager import available_icons, get_icon, resolve_name
from utils.themes import settings_stylesheet
from widgets.settings_help import set_help
from widgets.themed_controls import SettingsInteractionManager


class IconListModel(QAbstractListModel):
    """Expose names eagerly but render decorations only when requested by the view."""

    def __init__(self, color, parent=None):
        """Initialize the catalog and the folder's preview color."""
        super().__init__(parent)
        self.color = color
        self.names = list(available_icons())

    def rowCount(self, parent=QModelIndex()):
        """Return the number of filtered icons for the flat list."""
        return 0 if parent.isValid() else len(self.names)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """Return an icon name or a cached decoration for a visible cell."""
        if not index.isValid() or not 0 <= index.row() < len(self.names):
            return None
        name = self.names[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.UserRole):
            return name
        if role == Qt.ItemDataRole.DecorationRole:
            return get_icon(name, color=self.color)
        return None

    def filter(self, query):
        """Filter by case-insensitive name without reading SVG files."""
        self.beginResetModel()
        tokens = query.lower().replace(" ", "-")
        self.names = [name for name in available_icons() if tokens in name]
        self.endResetModel()


class IconPicker(QDialog):
    """Choose a Lucide name from a searchable, virtualized icon grid."""

    def __init__(self, selected, color, theme, parent=None):
        """Build a themed selector; only acceptance changes the caller's value."""
        super().__init__(parent)
        self.setWindowTitle("Choose folder icon")
        self.resize(620, 520)
        self.setStyleSheet(settings_stylesheet(theme))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search icons by name…")
        set_help(self.search, "icon_search")
        layout.addWidget(self.search)
        self.selection_label = QLabel()
        self.selection_label.setObjectName("helper_text")
        set_help(self.selection_label, "icon_selection")
        layout.addWidget(self.selection_label)
        self.grid = QListView()
        set_help(self.grid, "icon_grid")
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(QSize(132, 82))
        self.grid.setIconSize(QSize(28, 28))
        self.grid.setWordWrap(False)
        self.grid.setMouseTracking(True)
        self.model = IconListModel(color, self)
        self.grid.setModel(self.model)
        layout.addWidget(self.grid, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        set_help(self.ok_button, "icon_accept")
        set_help(buttons.button(QDialogButtonBox.StandardButton.Cancel), "icon_cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.grid.doubleClicked.connect(lambda index: self.accept())
        self.search.textChanged.connect(self._filter)
        self.grid.selectionModel().currentChanged.connect(self._selection_changed)
        layout.addWidget(buttons)
        index = self.model.index(self.model.names.index(resolve_name(selected)))
        self.grid.setCurrentIndex(index)
        self.grid.scrollTo(index)
        self.interactions = SettingsInteractionManager(self)

    def _selection_changed(self):
        self.ok_button.setEnabled(self.grid.currentIndex().isValid())
        self.selection_label.setText(f"Selected: {self.selected_name()}" if self.selected_name() else "Select an icon")

    def update_theme(self, theme):
        """Refresh the open picker when the owning application's theme changes."""
        self.setStyleSheet(settings_stylesheet(theme))

    def _filter(self, query):
        self.model.filter(query)
        self.grid.setCurrentIndex(QModelIndex())
        self._selection_changed()

    def selected_name(self):
        """Return the selected bundled name, or None when no cell is selected."""
        return self.grid.currentIndex().data(Qt.ItemDataRole.UserRole)
