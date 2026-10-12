# Responsible for: sidebar folder selection and the Refine toggle presentation.

"""Sidebar UI controls that delegate folder and refinement actions to controllers."""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy, QStyle, QStyleOptionButton
from PyQt6.QtGui import QPainter, QPen, QFont, QFontMetrics
from PyQt6.QtCore import Qt, QSize
from widgets.toggle_switch import ToggleSwitch
from utils.icon_manager import get_icon, get_pixmap
from widgets.settings_help import HELP, set_help


def folder_icon(folder):
    """Render the folder's Lucide name in its saved custom color."""
    return get_icon(folder.get("icon", "folder"), color=folder.get("color"))


class FolderDropdownButton(QPushButton):
    """A button styled like a dropdown selector with an SVG icon and a QPainter-drawn chevron.

    The chevron is drawn inline via QPainter to match the Lucide-style stroke weight
    used throughout the project, avoiding Unicode glyph substitution issues.
    """

    def __init__(self, text: str, icon_name: str | None = None, parent=None):
        """Create the folder dropdown button.

        Args:
            text: Display label (e.g., "Inbox").
            icon_name: Optional SVG icon filename in assets/icons/.
            parent: Optional parent widget.
        """
        super().__init__(text, parent)
        self._full_text = text
        self.setFont(QFont("Inter", 12))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if icon_name:
            self.setIcon(get_icon(icon_name.removesuffix(".svg")))
            self.setIconSize(QSize(18, 18))
        self.setStyleSheet("text-align: left; padding-left: 12px; padding-right: 32px;")
        self.setFixedHeight(36)
        self.setToolTip(f"{HELP['destination']}\n\nCurrent folder: {text}")
        self.setAccessibleName("Note folder")

    def resizeEvent(self, event) -> None:
        """Re-apply ellipsis when the button width changes."""
        super().resizeEvent(event)
        self._update_elided_text()

    def set_folder_label(self, name):
        """Update the full folder name and refresh its elided display."""
        self._full_text = name
        self._update_elided_text()

    def _label_available_width(self) -> int:
        """Return the pixel width available for the folder name label text."""
        option = QStyleOptionButton()
        self.initStyleOption(option)
        content_rect = self.style().subElementRect(
            QStyle.SubElement.SE_PushButtonContents, option, self
        )
        available = content_rect.width()
        if not self.icon().isNull():
            available -= self.iconSize().width() + 4
        # Reserve space for the hand-drawn chevron on the right edge.
        available -= 22
        return max(0, available)

    def _update_elided_text(self) -> None:
        """Truncate the folder label with an ellipsis when it does not fit."""
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self._label_available_width()
        )
        block = self.blockSignals(True)
        super().setText(elided)
        self.blockSignals(block)
        self.setToolTip(f"{HELP['destination']}\n\nCurrent folder: {self._full_text}")

    def paintEvent(self, event) -> None:
        """Draw the base button, then overlay a small down-chevron on the right edge."""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Use the button's text color for the chevron so it adapts to themes
        pen = QPen(self.palette().buttonText().color())
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        # Draw a small V-shaped chevron aligned to the right margin
        x = self.width() - 22
        y = self.height() / 2 - 2.5
        painter.drawLine(int(x), int(y), int(x + 5), int(y + 5))
        painter.drawLine(int(x + 5), int(y + 5), int(x + 10), int(y))
        painter.end()


class PostProcessRow(QFrame):
    """A visual container showing a post-processing icon, label, and Refine toggle.

    The toggle switch is the only interactive element. The outer frame provides layout
    context and tooltip but does not accept clicks itself.
    """

    def __init__(self, theme: dict, parent=None):
        """Create the post-processing row.

        Args:
            theme: Application theme dictionary.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.theme = theme
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("PostProcessRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        # Post-processing icon
        self.icon_label = QLabel()
        self.icon_label.setPixmap(get_pixmap("wand-sparkles", theme=theme, size=18))
        layout.addWidget(self.icon_label)

        # Label — elided on resize so long names stay consistent with Post-process.
        self._full_label_text = "Refine"
        self.text_label = QLabel(self._full_label_text)
        self.text_label.setFont(QFont("Inter", 12))
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.text_label, stretch=1)

        # The controller validates whether this toggle may be enabled.
        self.toggle = ToggleSwitch(theme=self.theme)
        self.toggle.setEnabled(True)
        self.toggle.setAccessibleName("Refine new transcriptions")
        layout.addWidget(self.toggle)

        for widget in (self, self.toggle, self.text_label, self.icon_label):
            set_help(widget, "refine")
        self.setFixedHeight(36)

        # Apply initial styling
        self.apply_theme(theme)
        self._update_elided_label()

    def resizeEvent(self, event) -> None:
        """Re-apply ellipsis when the row width changes."""
        super().resizeEvent(event)
        self._update_elided_label()

    def _label_available_width(self) -> int:
        """Return the pixel width available for the post-process label text."""
        fixed_width = (
            self.layout().contentsMargins().left()
            + self.layout().contentsMargins().right()
            + self.icon_label.width()
            + self.layout().spacing()
            + self.toggle.width()
            + self.layout().spacing()
        )
        return max(0, self.width() - fixed_width)

    def _update_elided_label(self) -> None:
        """Truncate the post-process label with an ellipsis when it does not fit."""
        metrics = QFontMetrics(self.text_label.font())
        elided = metrics.elidedText(
            self._full_label_text, Qt.TextElideMode.ElideRight, self._label_available_width()
        )
        self.text_label.setText(elided)

    def apply_theme(self, theme: dict) -> None:
        """Update visual styling when the application theme changes.

        Args:
            theme: Application theme dictionary.
        """
        self.theme = theme
        self.toggle.apply_theme(theme)
        self.icon_label.setPixmap(get_pixmap("wand-sparkles", theme=theme, size=18, dpr=self.devicePixelRatioF()))
        self.text_label.setStyleSheet(f"color: {theme.get('LIGHT_GRAY_TEXT', theme['DARK_TEXT'])};")
        self.setStyleSheet(f"""
            QFrame#PostProcessRow {{
                background-color: {theme['SIDEBAR_BG']};
                border: 1px solid {theme['BORDER']};
                border-radius: 6px;
            }}
        """)
