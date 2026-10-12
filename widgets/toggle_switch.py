"""Reusable custom UI toggle switch widget with native animation."""

from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, QRectF, QPoint, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QPainterPath, QColor, QPen
from PyQt6.QtWidgets import QAbstractButton

class ToggleSwitch(QAbstractButton):
    """A visually polished toggle switch (pill and thumb) replacing standard checkboxes.
    
    Provides smooth QPropertyAnimation for state changes and dynamic theming support.
    Does not own any persistence or business logic; it acts solely as a View component.
    """

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.theme = theme
        self._thumb_position = 2.0
        
        self.animation = QPropertyAnimation(self, b"thumb_position", self)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.setDuration(200)
        
        self.toggled.connect(self._on_toggled)

    def apply_theme(self, theme: dict) -> None:
        """Update the component colors dynamically based on the application theme.
        
        Args:
            theme: Theme dictionary containing semantic colors (e.g., BLUE_ACCENT).
        """
        self.theme = theme
        self.update()

    @pyqtProperty(float)
    def thumb_position(self) -> float:
        """Return the current horizontal position of the thumb."""
        return self._thumb_position

    @thumb_position.setter
    def thumb_position(self, pos: float) -> None:
        """Set the horizontal position of the thumb and trigger a repaint."""
        self._thumb_position = pos
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        """Trigger the sliding animation when the button's state changes."""
        self.animation.stop()
        if checked:
            self.animation.setEndValue(float(self.width() - 22.0))
        else:
            self.animation.setEndValue(2.0)
        self.animation.start()

    def paintEvent(self, event) -> None:
        """Draw the pill track and circular thumb based on current state and theme."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        if self.theme is None:
            return

        # Determine colors based on state
        is_checked = self.isChecked()
        is_disabled = not self.isEnabled()
        
        if is_disabled:
            track_color = QColor(self.theme.get("NOTE_BG", "#e2e8f0"))
            thumb_color = QColor(self.theme.get("BORDER", "#cbd5e1"))
        elif is_checked:
            track_color = QColor(self.theme.get("BLUE_ACCENT", "#0ea5e9"))
            thumb_color = QColor("#ffffff")
        else:
            track_color = QColor(self.theme.get("BORDER", "#cbd5e1"))
            thumb_color = QColor("#ffffff")

        # Subtle hover indication
        if self.underMouse() and not is_disabled:
            # Darken track slightly on hover if checked, or darken gray if unchecked
            track_color = track_color.darker(110)

        # Draw the rounded track
        track_rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(track_rect, self.height() / 2, self.height() / 2)
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawPath(path)

        # Draw the thumb
        thumb_radius = (self.height() - 4) / 2
        thumb_rect = QRectF(self._thumb_position, 2, thumb_radius * 2, thumb_radius * 2)
        
        # Optional: Add a subtle shadow to the thumb if not disabled
        if not is_disabled:
            painter.setBrush(QColor(0, 0, 0, 40))
            shadow_rect = thumb_rect.translated(0, 1)
            painter.drawEllipse(shadow_rect)
            
        painter.setBrush(thumb_color)
        if is_disabled:
            # Just to give it a slight outline so it doesn't blend completely
            painter.setPen(QPen(QColor(0, 0, 0, 20), 1))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            
        painter.drawEllipse(thumb_rect)
        
    def sizeHint(self) -> QSize:
        return QSize(44, 24)
