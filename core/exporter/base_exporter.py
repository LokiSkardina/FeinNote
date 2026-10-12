# Responsible for: export interface and shared export behavior

"""Contract for exporters that render a note without changing its stored data."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any


class BaseExporter(ABC):
    """Render a note snapshot into the bytes of one supported file format."""

    @abstractmethod
    def render(self, note: Mapping[str, Any]) -> bytes:
        """Return encoded file contents for the supplied note, or raise on invalid data."""


def final_text(note: Mapping[str, Any]) -> str:
    """Return the final editable text used by document and plain-text exports."""
    text = note.get("full_text", "")
    if not isinstance(text, str):
        raise ValueError("The note does not contain valid text.")
    return text
