# Responsible for: shared format availability, filenames and safe export persistence.

import os
from pathlib import Path
import tempfile

from core.exporter.formats.documents import DocxExporter, PdfExporter
from core.exporter.formats.subtitles import SrtExporter, VttExporter, subtitle_cues
from core.exporter.formats.text import TextExporter


class ExportService:
    """Provide one export implementation for editor and history-card actions."""

    FORMATS = ("txt", "docx", "pdf", "srt", "vtt")
    EXPORTERS = {"txt": TextExporter, "docx": DocxExporter, "pdf": PdfExporter,
                 "srt": SrtExporter, "vtt": VttExporter}

    @classmethod
    def available_formats(cls, note) -> tuple[str, ...]:
        """Return subtitle formats only when the note has valid original cues."""
        try:
            subtitle_cues(note)
        except (ValueError, OverflowError):
            return cls.FORMATS[:3]
        return cls.FORMATS

    @classmethod
    def destination_path(cls, path, format_name: str) -> Path:
        """Add the selected extension, replacing a different known export suffix."""
        if format_name not in cls.FORMATS:
            raise ValueError("Unsupported export format.")
        destination = Path(path)
        suffix = destination.suffix.lower()
        if suffix == f".{format_name}":
            return destination
        if suffix.lstrip(".") in cls.FORMATS:
            return destination.with_suffix(f".{format_name}")
        return Path(str(destination) + f".{format_name}")

    def export(self, note, destination, format_name: str, *, overwrite: bool = False) -> Path:
        """Render and atomically save a note; replacing a file requires caller approval."""
        path = self.destination_path(destination, format_name)
        if path.exists() and not overwrite:
            raise FileExistsError(f"The file already exists: {path.name}")
        contents = self.EXPORTERS[format_name]().render(note)
        descriptor, temporary = tempfile.mkstemp(prefix=".feinnote_export_", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(contents)
            if overwrite:
                os.replace(temporary, path)
            elif os.name == "nt":
                # Windows rename publishes atomically and refuses an existing
                # destination. Unlike hard links, it also works on portable
                # filesystems and restricted environments without link support.
                os.rename(temporary, path)
            else:
                # Exclusive publication also protects a file created while the
                # export was rendering. Both names are on the same filesystem.
                os.link(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path
