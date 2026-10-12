# Responsible for: rendering note previews and emitting note action requests.

import os
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QGraphicsDropShadowEffect, QWidget, QMenu
from PyQt6.QtGui import QFont, QIcon, QFontMetrics, QColor, QCursor
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer
from utils.themes import light_theme, dark_theme
from utils.paths import get_resource_path
from utils.icon_manager import get_icon
from core.exporter.service import ExportService
from core.exporter.formats.subtitles import UNAVAILABLE
from widgets.settings_help import HELP, set_help

class HistoryCardWidget(QFrame):
    card_clicked = pyqtSignal(str, QWidget)
    copy_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str, QWidget)
    move_requested = pyqtSignal(str, str)
    export_requested = pyqtSignal(str, str)

    def __init__(self, note_data, file_path, theme):
        super().__init__()
        self.note_data = note_data
        self.file_path = file_path
        self.full_text = note_data["full_text"]
        self.theme = theme
        self.folder_options = []

        self.setFixedHeight(125)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("HistoryCardWidget")
        self.setProperty("state", "normal")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setXOffset(0)
        shadow.setYOffset(1)
        shadow.setColor(QColor(0, 0, 0, 15))
        self.setGraphicsEffect(shadow)

        self.root_layout = QHBoxLayout(self)
        self.root_layout.setContentsMargins(16, 12, 12, 12)
        self.root_layout.setSpacing(12)

        text_container = QWidget()
        text_layout = QVBoxLayout(text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(6)

        self.max_preview_lines = 3
        self._preview_src = self.full_text.replace('\n', ' ')

        self.preview_label = QLabel()
        self.preview_label.setFont(QFont("Inter", 14))
        self.preview_label.setWordWrap(True)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignTop)

        fm = QFontMetrics(self.preview_label.font())
        self.preview_label.setFixedHeight(fm.lineSpacing() * self.max_preview_lines)

        QTimer.singleShot(0, self._update_preview_label)
        
        text_layout.addWidget(self.preview_label)

        metadata_label = QLabel(note_data["metadata"])
        metadata_label.setObjectName("metadata_label")
        metadata_label.setFont(QFont("Inter", 11))
        set_help(metadata_label, "note_metadata")

        text_layout.addStretch()
        text_layout.addWidget(metadata_label)

        self.actions_container = QWidget()
        self.actions_container.setFixedWidth(56)
        actions_layout = QHBoxLayout(self.actions_container)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        self.delete_btn = QPushButton(icon=get_icon("trash-2", theme=theme))
        self.delete_btn.setFixedSize(24, 24)
        self.delete_btn.setIconSize(QSize(14, 14))
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.clicked.connect(self.on_delete_clicked)
        self.delete_btn.hide()
        set_help(self.delete_btn, "delete_note")
        self.delete_btn.setAccessibleName("Delete note")
        source = note_data.get("source_name")
        self.setToolTip(HELP["note_open"] + (f"\n\nSource: {source}" if source else ""))

        actions_layout.addWidget(self.delete_btn)

        self.root_layout.addWidget(text_container)
        self.root_layout.addStretch()
        self.root_layout.addWidget(self.actions_container, alignment=Qt.AlignmentFlag.AlignCenter)

        self._apply_card_theme()

    def update_theme(self, new_theme):
        self.theme = new_theme
        self._apply_card_theme()
        self.update()

    def _apply_card_theme(self):
        T = self.theme
        self.delete_btn.setIcon(get_icon("trash-2", theme=T))
        full_stylesheet = f"""
            #HistoryCardWidget QWidget {{ background-color: transparent; }}
            #HistoryCardWidget QLabel {{ background-color: transparent; color: {T['DARK_TEXT']}; }}                
            #HistoryCardWidget[state="normal"] {{ background-color: {T['NOTE_BG']}; border-radius: 12px; border: 1px solid transparent; }}
            #HistoryCardWidget[state="hover"] {{ background-color: {T['HOVER_BG']}; border-radius: 12px; border: 1px solid {T['BORDER']}; }}
            #HistoryCardWidget[state="active"] {{ background-color: {T['ACTIVE_CARD_BG']}; border: 0px solid {T['BLUE_ACCENT']}; border-radius: 12px; }}
            #HistoryCardWidget QPushButton {{ background-color: transparent; border: none; border-radius: 6px; padding: 4px; }}
            #HistoryCardWidget QPushButton:hover {{ background-color: {T['BUTTON_HOVER_BG']}; }}
            """
        self.setStyleSheet(full_stylesheet)
        
        metadata_label = self.findChild(QLabel, "metadata_label")
        if metadata_label:
            metadata_label.setStyleSheet(f"color: {T['GRAY_TEXT']}; background-color: transparent;")

    def setState(self, state: str):
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview_label()

    def _update_preview_label(self):
        w = self.preview_label.width()
        if w <= 0:
            return
        fm = QFontMetrics(self.preview_label.font())
        self.preview_label.setText(
            self._elide_multiline(self._preview_src, fm, w, self.max_preview_lines)
        )

    @staticmethod
    def _elide_multiline(text: str, fm: QFontMetrics, width: int, max_lines: int) -> str:
        words = " ".join(text.split()).split(" ")
        lines, cur = [], ""
        i = 0
        while i < len(words):
            nxt = (cur + " " + words[i]) if cur else words[i]
            if fm.horizontalAdvance(nxt) <= width:
                cur = nxt
                i += 1
            else:
                if len(lines) == max_lines - 1:
                    rest = (cur + " " + " ".join(words[i:])).strip()
                    lines.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, width))
                    return "\n".join(lines)
                lines.append(cur)
                cur = ""
        if cur:
            if len(lines) == max_lines - 1 and fm.horizontalAdvance(cur) > width:
                lines.append(fm.elidedText(cur, Qt.TextElideMode.ElideRight, width))
            else:
                lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = fm.elidedText(lines[-1], Qt.TextElideMode.ElideRight, width)
        return "\n".join(lines)

    def enterEvent(self, event):
        if self.property("state") != "active":
            self.setState("hover")
        self.delete_btn.show()
        super().enterEvent(event)

    def refresh_hover(self):
        """Reconcile hover after layout changes even when the cursor has not moved."""
        point = QCursor.pos()
        hit = QApplication.widgetAt(point)
        hovered = (self.isVisible() and self.isEnabled() and self.rect().contains(self.mapFromGlobal(point))
                   and hit is not None and (hit is self or self.isAncestorOf(hit)))
        self.delete_btn.setVisible(hovered)
        if self.property("state") != "active":
            self.setState("hover" if hovered else "normal")

    def leaveEvent(self, event):
        if self.property("state") != "active":
            self.setState("normal")
        self.delete_btn.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.card_clicked.emit(self.full_text, self)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """Show the note action menu and release it after dismissal."""
        menu = self.create_context_menu()
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()

    def create_context_menu(self):
        """Build themed actions with export availability derived from original cues."""
        menu = QMenu(self)
        font = QFont(self.font())
        font.setPointSizeF(max(11.0, font.pointSizeF()))
        font.setWeight(QFont.Weight.Medium)
        menu.setFont(font)
        menu.setToolTipsVisible(True)
        T = self.theme
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {T['WHITE_BG']};
                color: {T['DARK_TEXT']};
                border: 1px solid {T['BORDER']};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 8px 32px 8px 12px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {T['HOVER_BG']};
                color: {T['DARK_TEXT']};
            }}
            QMenu::item:disabled {{
                color: {T['LIGHT_GRAY_TEXT']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {T['BORDER']};
                margin: 6px 10px;
            }}
            QMenu::right-arrow {{ margin-right: 4px; }}
        """)

        open_action = menu.addAction(get_icon("file-text", theme=T), "Open")
        copy_action = menu.addAction(get_icon("copy", theme=T), "Copy text")
        set_help(open_action, "note_open")
        set_help(copy_action, "note_copy")
        open_action.triggered.connect(lambda: self.card_clicked.emit(self.full_text, self))
        copy_action.triggered.connect(self.on_copy_clicked)

        export_menu = menu.addMenu(get_icon("download", theme=T), "Export as")
        export_menu.setFont(font)
        export_menu.setToolTipsVisible(True)
        set_help(export_menu.menuAction(), "export_note")
        export_menu.setStyleSheet(menu.styleSheet())
        available = ExportService.available_formats(self.note_data)
        for name in ExportService.FORMATS:
            export_action = export_menu.addAction(name.upper())
            export_action.setEnabled(name in available)
            if name not in available:
                export_action.setToolTip(UNAVAILABLE)
            elif name in ("srt", "vtt"):
                export_action.setToolTip("Export original transcription segments and timestamps.")
            else:
                set_help(export_action, "export_document")
            export_action.triggered.connect(lambda checked=False, fmt=name: self.export_requested.emit(self.file_path, fmt))

        menu.addSeparator()
        move_menu = menu.addMenu(get_icon("folder", theme=T), "Move to folder")
        move_menu.setFont(font)
        move_menu.setStyleSheet(menu.styleSheet())
        move_menu.setToolTipsVisible(True)
        set_help(move_menu.menuAction(), "note_move")
        for folder in self.folder_options:
            move_action = move_menu.addAction(folder["name"])
            move_action.setToolTip(f"{HELP['note_move']}\n\nDestination: {folder['name']}")
            move_action.setEnabled(folder["id"] != self.note_data.get("folder_id", "inbox"))
            move_action.triggered.connect(lambda checked=False, target=folder["id"]: self.move_requested.emit(self.file_path, target))
        delete_action = menu.addAction(get_icon("trash-2", theme=T), "Delete note")
        set_help(delete_action, "delete_note")
        delete_action.triggered.connect(self.on_delete_clicked)
        return menu

    def on_copy_clicked(self):
        QApplication.clipboard().setText(self.full_text)
        self.copy_clicked.emit(self.full_text)

    def on_delete_clicked(self):
        self.delete_clicked.emit(self.file_path, self)

    def update_preview(self, new_text):
        self.full_text = new_text
        self.note_data["full_text"] = new_text
        self._preview_src = new_text.replace('\n', ' ')
        self._update_preview_label()
