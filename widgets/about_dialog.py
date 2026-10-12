# Responsible for: the legacy About dialog and its live theme-aware presentation.

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QWidget
from PyQt6.QtGui import QFont, QIcon, QDesktopServices
from PyQt6.QtCore import Qt, QUrl, QSize
import os
from utils.paths import get_resource_path
from utils.themes import light_theme, dialog_palette, tooltip_stylesheet
from widgets.settings_help import set_help
from widgets.themed_controls import SettingsInteractionManager

class AboutDialog(QDialog):
    """Show application information using the same active theme as other dialogs."""

    def __init__(self, parent=None, theme=None):
        """Build the existing About content and apply the supplied theme."""
        super().__init__(parent)
        
        self.setWindowTitle("About FeinNote")
        
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.MSWindowsFixedSizeDialogHint)
        self.setFixedSize(480, 440) 

        icon_path = get_resource_path(os.path.join("assets", "icons", "app_icon.svg"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        theme = theme or light_theme
        bg_color = theme['WHITE_BG']
        text_color = theme['DARK_TEXT']
        gray_text = theme['DARK_TEXT']
        btn_bg = theme['SIDEBAR_BG']
        btn_hover = theme['HOVER_BG']
        
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg_color};
            }}
            QLabel {{
                color: {text_color};
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(12)
        
        title = QLabel("FeinNote")
        set_help(title, "app")
        title.setFont(QFont("Inter", 24, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        version = QLabel("v0.2.1")
        set_help(version, "version")
        version.setFont(QFont("Inter", 12))
        version.setStyleSheet(f"color: {gray_text};")
        self.version_label = version
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)
        
        layout.addSpacing(10)
        
        info_text = f"""
        <style>
            p {{ line-height: 140%; margin-bottom: 8px; }}
            .highlight {{ font-weight: bold; }}
            .subtle {{ font-size: 11px; }}
        </style>
        
        <p align="center">
            Local audio transcription powered by <b>OpenAI Whisper</b>.<br>
            Runs entirely on your device. <b>Your data stays local.</b>
        </p>
        
        <hr>
        
        <p>
            • <b>Models:</b> Various sizes available (Tiny to Large).<br>
            • <b>Languages:</b> Auto-detection & manual selection.<br>
            • <b>Workflow:</b> Drag & Drop files or record via Microphone.<br>
            • <b>Smart:</b> Auto-saving and text editing.<br>
            • <b>Formats:</b> mp3, wav, m4a, ogg, flac.
        </p>
        
        <p class="subtle" align="center">
            Performance depends on your hardware (GPU recommended).<br>
            Large models require 8GB+ RAM.
        </p>
        """
        
        info_label = QLabel(info_text)
        set_help(info_label, "tab_about")
        info_label.setFont(QFont("Inter", 11))
        info_label.setWordWrap(True)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)
        
        layout.addStretch()
        
        github_btn = QPushButton("  LokiSkardina/FeinNote")
        self.github_button = github_btn
        set_help(github_btn, "repository")
        github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        github_btn.setFont(QFont("Inter", 11))
        
        gh_icon_path = get_resource_path(os.path.join("assets", "icons", "github.svg"))
        if os.path.exists(gh_icon_path):
            github_btn.setIcon(QIcon(gh_icon_path))
            github_btn.setIconSize(QSize(18, 18))
            
        github_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {text_color};
                border: 1px solid {btn_bg};
                border-radius: 6px;
                padding: 8px;
                text-align: center;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
                border: 1px solid {gray_text};
            }}
        """)
        github_btn.clicked.connect(self.open_github)
        layout.addWidget(github_btn)
        
        layout.addSpacing(8)
        
        self.ok_button = QPushButton("Close")
        set_help(self.ok_button, "close_dialog")
        self.ok_button.setFont(QFont("Inter", 11, QFont.Weight.Medium))
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_button.setFixedHeight(38)
        self.ok_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {text_color};
                border: none;
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
        """)
        self.ok_button.clicked.connect(self.accept)
        
        layout.addWidget(self.ok_button)
        self.update_theme(theme)
        self.interactions = SettingsInteractionManager(self)

    def update_theme(self, theme):
        """Refresh labels, buttons and palette without rebuilding the dialog."""
        self.setPalette(dialog_palette(theme))
        self.setStyleSheet(f"QDialog {{ background-color: {theme['WHITE_BG']}; }} "
                          f"QLabel {{ color: {theme['DARK_TEXT']}; background: transparent; }}" + tooltip_stylesheet(theme))
        self.version_label.setStyleSheet(f"color: {theme['DARK_TEXT']};")
        self.github_button.setStyleSheet(
            f"QPushButton {{ background-color: {theme['SIDEBAR_BG']}; color: {theme['DARK_TEXT']}; "
            f"border: 1px solid {theme['SIDEBAR_BG']}; border-radius: 6px; padding: 8px; text-align: center; }} "
            f"QPushButton:hover {{ background-color: {theme['HOVER_BG']}; border-color: {theme['DARK_TEXT']}; }}")
        self.ok_button.setStyleSheet(
            f"QPushButton {{ background-color: {theme['SIDEBAR_BG']}; color: {theme['DARK_TEXT']}; "
            "border: none; border-radius: 8px; } "
            f"QPushButton:hover {{ background-color: {theme['HOVER_BG']}; }}")

    def open_github(self):
        """Open the existing repository link in the default browser."""
        QDesktopServices.openUrl(QUrl("https://github.com/LokiSkardina/WhisperNote"))
