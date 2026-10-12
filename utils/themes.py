# Responsible for: FeinNote palettes and shared themed control styles.

light_theme = {
    "WHITE_BG": "#fdf6e3",
    "SIDEBAR_BG": "#eee8d5",
    "NOTE_BG": "#fdf6e3",
    "HOVER_BG": "#e1dbcd",
    "ACTIVE_BG": "#d6d0c4",
    "ACTIVE_CARD_BG": "#e1dbcd",
    "BORDER": "#d6d0c4",
    "DARK_TEXT": "#586e75",
    "GRAY_TEXT": "#657b83",
    "LIGHT_GRAY_TEXT": "#839496",
    "STATUS_SUCCESS": "#859900",
    "STATUS_ERROR": "#dc322f",
    "STATUS_LOADING": "#b58900",
    "BLUE_ACCENT": "#268bd2",
    "RED_RECORD": "#dc322f",
    "ORANGE_PAUSE": "#cb4b16",
    "GREEN_RESUME": "#859900",
    "BUTTON_HOVER_BG": "rgba(147, 161, 161, 0.25)",
}

dark_theme = {
    "WHITE_BG": "#002b36",
    "SIDEBAR_BG": "#073642",
    "NOTE_BG": "#002b36",
    "HOVER_BG": "#586e75",
    "ACTIVE_BG": "#657b83",
    "ACTIVE_CARD_BG": "#586e75",
    "BORDER": "#586e75",
    "DARK_TEXT": "#839496",
    "GRAY_TEXT": "#657b83",
    "LIGHT_GRAY_TEXT": "#93a1a1", 
    "STATUS_SUCCESS": "#859900",
    "STATUS_ERROR": "#dc322f",
    "STATUS_LOADING": "#b58900",
    "BLUE_ACCENT": "#268bd2",
    "RED_RECORD": "#dc322f",
    "ORANGE_PAUSE": "#cb4b16",
    "GREEN_RESUME": "#859900",
    "BUTTON_HOVER_BG": "rgba(131, 148, 150,0.2)",
}


def scrollbar_stylesheet(theme):
    """Return the same flat, arrow-free scrollbars for settings and the main view."""
    return f"""
        QScrollBar:vertical {{ background: transparent; border: none; width: 8px; margin: 0; }}
        QScrollBar:horizontal {{ background: transparent; border: none; height: 8px; margin: 0; }}
        QScrollBar::handle {{ background: {theme['BORDER']}; border-radius: 4px; }}
        QScrollBar::handle:vertical {{ min-height: 28px; }}
        QScrollBar::handle:horizontal {{ min-width: 28px; }}
        QScrollBar::handle:hover {{ background: {theme['LIGHT_GRAY_TEXT']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: none; border: none; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow {{ width: 0; height: 0; }}
    """


def dialog_palette(theme):
    """Return explicit text/background roles independent of the Windows theme."""
    from PyQt6.QtGui import QColor, QPalette

    palette = QPalette()
    roles = {
        "Window": "WHITE_BG", "WindowText": "DARK_TEXT",
        "Base": "WHITE_BG", "AlternateBase": "SIDEBAR_BG", "Text": "DARK_TEXT",
        "Button": "SIDEBAR_BG", "ButtonText": "DARK_TEXT",
        "ToolTipBase": "SIDEBAR_BG", "ToolTipText": "DARK_TEXT",
        "Highlight": "ACTIVE_BG", "HighlightedText": "DARK_TEXT",
        "PlaceholderText": "DARK_TEXT", "Link": "BLUE_ACCENT",
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for role, token in roles.items():
            palette.setColor(group, getattr(QPalette.ColorRole, role), QColor(theme[token]))
    return palette


def message_dialog_stylesheet(theme):
    """Style shared confirmation/warning/error text and buttons using active tokens."""
    return f"""
        QMessageBox {{ background-color: {theme['WHITE_BG']}; color: {theme['DARK_TEXT']}; }}
        QLabel {{ color: {theme['DARK_TEXT']}; background: transparent; }}
        QTextEdit {{ color: {theme['DARK_TEXT']}; background-color: {theme['WHITE_BG']}; }}
        QPushButton {{ background-color: {theme['SIDEBAR_BG']}; color: {theme['DARK_TEXT']};
            border: 1px solid {theme['BORDER']}; border-radius: 6px; padding: 6px 16px; min-width: 60px; }}
        QPushButton:hover {{ background-color: {theme['HOVER_BG']}; }}
        QPushButton:focus {{ border-color: {theme['BLUE_ACCENT']}; }}
        QPushButton[danger="true"] {{ color: {theme['STATUS_ERROR']}; }}
    """


def tooltip_stylesheet(theme):
    """Keep native and delayed help readable using the active application colors."""
    return (f"QToolTip {{ background: {theme['SIDEBAR_BG']}; color: {theme['DARK_TEXT']}; "
            f"border: 1px solid {theme['BORDER']}; padding: 6px; }}")


def settings_stylesheet(theme):
    """Return shared typography, controls and interaction states for settings/pickers."""
    t = theme or light_theme
    return f"""
        QWidget {{ font-family: Inter; font-size: 12pt; color: {t['DARK_TEXT']}; background: {t['WHITE_BG']}; }}
        QDialog, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t['WHITE_BG']}; }}
        QLabel {{ background: transparent; border: none; }}
        QLabel#section_title {{ font-size: 14pt; font-weight: 600; }}
        QLabel#app_heading {{ font-size: 20pt; font-weight: 700; }}
        QLabel#helper_text {{ font-size: 11pt; color: {t['GRAY_TEXT']}; }}
        QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background: {t['SIDEBAR_BG']}; border: 1px solid {t['BORDER']};
            border-radius: 7px; padding: 8px 12px; selection-background-color: {t['BLUE_ACCENT']};
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ min-height: 22px; }}
        QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {t['BLUE_ACCENT']}; }}
        QComboBox:hover {{ border-color: {t['LIGHT_GRAY_TEXT']}; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
        QComboBox QAbstractItemView, QListView {{ background: {t['SIDEBAR_BG']}; border: 1px solid {t['BORDER']}; border-radius: 7px; selection-background-color: {t['ACTIVE_BG']}; }}
        QListView::item {{ padding: 6px; border-radius: 6px; }}
        QListView::item:hover {{ background: {t['BUTTON_HOVER_BG']}; }}
        QListView::item:selected {{ background: {t['ACTIVE_BG']}; }}
        QPushButton {{ background: {t['SIDEBAR_BG']}; border: 1px solid {t['BORDER']}; border-radius: 7px; padding: 8px 12px; min-height: 22px; }}
        QPushButton:hover {{ background: {t['BUTTON_HOVER_BG']}; }}
        QPushButton#github_btn {{ text-align: left; }}
        QPushButton#advanced_toggle {{ text-align: left; }}
        QPushButton:focus {{ border-color: {t['BLUE_ACCENT']}; }}
        QPushButton[danger="true"] {{ color: {t['STATUS_ERROR']}; }}
        QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTextEdit:disabled {{
            background: {t['NOTE_BG']}; color: {t['GRAY_TEXT']}; border-color: {t['BORDER']};
        }}
        QCheckBox {{ spacing: 10px; background: transparent; }}
        QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {t['BORDER']}; border-radius: 5px; background: {t['SIDEBAR_BG']}; }}
        QCheckBox::indicator:checked {{ background: {t['BLUE_ACCENT']}; border-color: {t['BLUE_ACCENT']}; }}
    """ + scrollbar_stylesheet(t) + tooltip_stylesheet(t)
