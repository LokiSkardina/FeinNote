# Responsible for: export dialog and export option selection

import os
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core.exporter.service import ExportService
from core.exporter.formats.subtitles import UNAVAILABLE


FILTERS = {
    "txt": "Plain text (*.txt)", "docx": "Word document (*.docx)",
    "pdf": "PDF document (*.pdf)", "srt": "SubRip subtitles (*.srt)",
    "vtt": "WebVTT subtitles (*.vtt)",
}


def choose_export_destination(parent, note, suggested_name, format_name=None):
    """Return a confirmed (path, format) selection, or None after cancel/unavailable cues."""
    available = ExportService.available_formats(note)
    if format_name is not None and format_name not in available:
        QMessageBox.information(parent, "Subtitle export", UNAVAILABLE)
        return None
    formats = (format_name,) if format_name else available
    selected = formats[0]
    filename, selected_filter = QFileDialog.getSaveFileName(
        parent, "Export note", f"{suggested_name}.{selected}",
        ";;".join(FILTERS[name] for name in formats), FILTERS[selected])
    if not filename:
        return None
    selected = next((name for name in formats if FILTERS[name] == selected_filter), selected)
    destination = ExportService.destination_path(filename, selected)
    # The native dialog confirms the entered path. If suffix normalization
    # resolves to a different existing file, that target needs confirmation too.
    changed = os.path.normcase(os.path.abspath(filename)) != os.path.normcase(os.path.abspath(destination))
    if changed and destination.exists():
        answer = QMessageBox.question(
            parent, "Replace file?", f"{destination.name} already exists. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return None
    return Path(destination), selected
