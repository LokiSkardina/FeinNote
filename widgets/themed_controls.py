# Responsible for: themed dropdowns, delayed help and safe Settings wheel handling.

from html import escape

from PyQt6 import sip
from PyQt6.QtCore import QObject, QEvent, QPointF, QTimer, Qt
from PyQt6.QtGui import QCursor, QPainter, QPalette, QWheelEvent
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QAbstractSpinBox, QComboBox, QDialog, QLabel, QMenu, QMessageBox, QPushButton, QTextEdit, QScrollArea, QToolTip, QWidget

from utils.icon_manager import get_pixmap
from utils.themes import dialog_palette, message_dialog_stylesheet


class ButtonFocusManager(QObject):
    """Replace native dotted mouse-focus frames while retaining a keyboard ring."""

    MARKER = "/* FeinNote focus */"

    def __init__(self, root):
        """Observe only buttons owned by this window, including its dialogs."""
        super().__init__(root)
        self.root = root
        QApplication.instance().installEventFilter(self)

    def _apply(self, button):
        base = button.styleSheet().split(self.MARKER)[0]
        color = self.root.theme["BLUE_ACCENT"]
        ring = f"border: 2px solid {color};" if button.property("keyboardFocus") else ""
        style = base + self.MARKER + f"QPushButton:focus {{ outline: none; {ring} }}"
        if button.styleSheet() != style:
            button.setStyleSheet(style)

    def eventFilter(self, watched, event):
        """Differentiate mouse and keyboard focus without changing focus policies."""
        if not isinstance(watched, QPushButton) or sip.isdeleted(self.root):
            return False
        owner = watched.parent()
        while owner is not None and owner is not self.root:
            owner = owner.parent()
        if owner is not self.root:
            return False
        kind = event.type()
        if kind == QEvent.Type.FocusIn:
            watched.setProperty("keyboardFocus", event.reason() in (
                Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason))
            self._apply(watched)
        elif kind in (QEvent.Type.FocusOut, QEvent.Type.MouseButtonPress):
            watched.setProperty("keyboardFocus", False)
            self._apply(watched)
        elif kind == QEvent.Type.StyleChange and watched.hasFocus():
            self._apply(watched)
        return False


class DialogThemeManager(QObject):
    """Apply the owning window's theme to all its system dialogs, including static boxes."""

    def __init__(self, root, theme):
        """Observe dialog shows without replacing QMessageBox behavior or global OS styles."""
        super().__init__(root)
        self.root = root
        self.theme = theme
        QApplication.instance().installEventFilter(self)

    def update_theme(self, theme):
        """Refresh existing dialogs too, so open warnings never retain stale colors."""
        self.theme = theme
        for dialog in self.root.findChildren(QDialog):
            self._apply(dialog)

    def _apply(self, dialog):
        palette = dialog_palette(self.theme)
        if isinstance(dialog, QMessageBox):
            # A local rule wins over an ancestor's background-only stylesheet.
            # Primary, informative and expandable detail text share readable tokens.
            dialog.setStyleSheet(message_dialog_stylesheet(self.theme))
            # Qt's Settings QWidget rule can retain a child's resolved palette
            # across window boundaries. Local text rules make the override explicit.
            for label in dialog.findChildren(QLabel):
                label.setStyleSheet(f"color: {self.theme['DARK_TEXT']}; background: transparent;")
            for editor in dialog.findChildren(QTextEdit):
                editor.setStyleSheet(f"color: {self.theme['DARK_TEXT']}; background-color: {self.theme['WHITE_BG']};")
            # The native details button can cache its size before themed font
            # and padding take effect. Keep all captions visible after restyling.
            for button in dialog.findChildren(QPushButton):
                button.setMinimumWidth(max(60, button.fontMetrics().horizontalAdvance(button.text().replace("&", "")) + 32))
        elif callable(getattr(dialog, "update_theme", None)):
            dialog.update_theme(self.theme)
        # Apply after stylesheet polishing: Qt otherwise restores a cached
        # previous palette while repolishing children across window boundaries.
        dialog.setPalette(palette)
        if not isinstance(dialog, QMessageBox) and callable(getattr(dialog, "update_theme", None)):
            # Full custom views own their fields' backgrounds and danger styles.
            return
        for child in dialog.findChildren(QWidget):
            if not isinstance(child, QDialog):
                child_palette = child.palette()
                for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
                    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                                 QPalette.ColorRole.Base, QPalette.ColorRole.Window):
                        child_palette.setColor(group, role, palette.color(group, role))
                child.setPalette(child_palette)

    def eventFilter(self, watched, event):
        """Theme only dialogs owned by this window before they become visible."""
        if (event.type() == QEvent.Type.Show and isinstance(watched, QDialog)) or (
                event.type() == QEvent.Type.Polish and isinstance(watched, QMessageBox)):
            # QWidget.isAncestorOf stops at window boundaries; a modal dialog
            # is a separate window even though QObject ownership is unchanged.
            owner = watched.parent()
            while owner is not None and owner is not self.root:
                owner = owner.parent()
            if not sip.isdeleted(self.root) and owner is self.root:
                self._apply(watched)
        return False


class ThemedComboBox(QComboBox):
    """Keep native combo behavior while drawing a crisp, palette-aware chevron."""

    def paintEvent(self, event):
        """Paint the field normally and replace its hidden native dropdown arrow."""
        super().paintEvent(event)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        color = self.palette().color(group, QPalette.ColorRole.Text).name()
        painter = QPainter(self)
        painter.drawPixmap(self.width() - 26, (self.height() - 16) // 2,
                           get_pixmap("chevron-down", color=color, size=16, dpr=self.devicePixelRatioF()))
        painter.end()


class SettingsInteractionManager(QObject):
    """Share one hover timer and route field wheel events to their settings page."""

    def __init__(self, root, delay_ms=2000, exclude_dialogs=False):
        """Observe only root's widget subtree; preserve native typing and clicks."""
        super().__init__(root)
        self.root = root
        self.exclude_dialogs = exclude_dialogs
        self.target = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self._show)
        QApplication.instance().installEventFilter(self)

    def cancel(self):
        """Cancel pending hover help and hide a tooltip already on screen."""
        self.timer.stop()
        self.target = None
        QToolTip.hideText()

    def _tooltip_target(self, widget):
        while widget is not None:
            if widget.toolTip():
                return widget
            if widget is self.root:
                break
            widget = widget.parentWidget()
        return None

    def _show(self):
        target = self.target
        if target is None or sip.isdeleted(target) or not target.isVisible():
            self.cancel()
            return
        # Recheck the pointer instead of showing stale help after a scroll/hide.
        hovered = QApplication.widgetAt(QCursor.pos())
        if hovered is None or self._tooltip_target(hovered) is not target:
            self.cancel()
            return
        text = "<qt>" + escape(target.toolTip()).replace("\n", "<br>") + "</qt>"
        QToolTip.showText(QCursor.pos(), text, target, target.rect())

    def eventFilter(self, watched, event):
        """Suppress native tooltip timing and accidental combo/spin wheel edits."""
        if sip.isdeleted(self.root) or sip.isdeleted(watched):
          return False
        if not isinstance(watched, QWidget) or not (watched is self.root or self.root.isAncestorOf(watched)):
            return False
        if self.exclude_dialogs and isinstance(watched.window(), QDialog):
            return False
        # Menus own action-level tooltips rather than widget-level help.
        if isinstance(watched, QMenu):
            return False
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            view = watched.parentWidget()
            if isinstance(view, QAbstractItemView) and watched is view.viewport():
                index = view.indexAt(event.pos())
                if index.isValid() and index.data(Qt.ItemDataRole.ToolTipRole):
                    # Preserve native item help (queue errors, icon names).
                    # A parent tooltip must not hide the hovered row's details.
                    self.cancel()
                    return False
            return True
        if kind == QEvent.Type.Enter:
            target = self._tooltip_target(watched)
            if target is not self.target:
                self.cancel()
                self.target = target
                if target is not None:
                    self.timer.start()
        elif kind == QEvent.Type.Leave:
            if self.target is watched or watched is self.root:
                self.cancel()
        elif kind in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self.cancel()
        if kind == QEvent.Type.Wheel:
            field = watched
            while field is not self.root and not isinstance(field, (QComboBox, QAbstractSpinBox)):
                field = field.parentWidget()
                if field is None:
                    return False
            if isinstance(field, (QComboBox, QAbstractSpinBox)):
                # An open popup retains its ordinary list scrolling behavior.
                if isinstance(field, QComboBox) and field.view().isVisible():
                    return False
                area = field.parentWidget()
                while area is not None and not isinstance(area, QScrollArea):
                    area = area.parentWidget()
                if area is not None:
                    viewport = area.viewport()
                    forwarded = QWheelEvent(QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                                            event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                            event.buttons(), event.modifiers(), event.phase(), event.inverted())
                    QApplication.sendEvent(viewport, forwarded)
                event.accept()
                return True
        return False
