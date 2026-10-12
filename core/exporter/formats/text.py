# Responsible for: UTF-8 plain-text note export.

from core.exporter.base_exporter import BaseExporter, final_text


class TextExporter(BaseExporter):
    """Export the exact final note text without markup or a byte-order mark."""

    def render(self, note) -> bytes:
        """Return the final text encoded as UTF-8, preserving whitespace."""
        return final_text(note).encode("utf-8")
