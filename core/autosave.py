# Responsible for: delayed, atomic persistence of the active note's editor text.

"""Delayed autosave service coordinated by ``NotesController``."""

import json
import logging
import os
import tempfile
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


logger = logging.getLogger("WhisperNote.autosave")


class AutosaveManager(QObject):
    """Save the active note after edits settle, without creating malformed JSON.

    Args:
        delay_ms: Delay between the latest edit and a disk write in milliseconds.
    """

    saved = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, delay_ms: int = 2000) -> None:
        """Create a single-shot debounce timer for the active note."""
        super().__init__()
        self.delay_ms = delay_ms
        self.last_saved_text = ""
        self.current_filepath: str | None = None
        self._get_current_text: Callable[[], str] | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._do_save)

    def set_text_getter(self, callback: Callable[[], str]) -> None:
        """Register the View callback that supplies current editor text.

        Args:
            callback: Zero-argument callable returning editor text.
        """
        if not callable(callback):
            raise TypeError("The autosave text getter must be callable")
        self._get_current_text = callback

    def set_active_note(self, filepath: str, initial_text: str) -> None:
        """Begin tracking an existing note for autosave.

        Args:
            filepath: Existing JSON note path.
            initial_text: Text currently stored in that note.
        """
        if not filepath:
            raise ValueError("An active note requires a filepath")
        self.current_filepath = filepath
        self.last_saved_text = initial_text
        self.timer.stop()

    def clear_active_note(self) -> None:
        """Stop pending work and clear the active-note reference."""
        self.current_filepath = None
        self.last_saved_text = ""
        self.timer.stop()

    def on_text_changed(self) -> None:
        """Restart the delayed save after an editor text change."""
        if not self.current_filepath:
            return
        self.timer.stop()
        self.timer.start(self.delay_ms)

    def force_save(self) -> bool:
        """Save before a context switch and return whether pending edits are safe."""
        if not self.current_filepath:
            return True
        self.timer.stop()
        return self._do_save()

    def _do_save(self) -> bool:
        """Replace the note file atomically while retaining its metadata."""
        filepath = self.current_filepath
        if not filepath or not self._get_current_text:
            return True
        try:
            # Empty text and whitespace are intentional editor changes too.
            new_text = self._get_current_text()
            if new_text == self.last_saved_text:
                return True
            if not os.path.exists(filepath):
                raise FileNotFoundError(filepath)

            with open(filepath, "r", encoding="utf-8") as note_file:
                note_data = json.load(note_file)
            if not isinstance(note_data, dict):
                raise ValueError("Note JSON root must be an object")
            note_data.setdefault("raw_text", note_data.get("full_text", ""))
            note_data["full_text"] = new_text

            # Retention scans run concurrently and must only see committed notes.
            descriptor, temporary_path = tempfile.mkstemp(
                suffix=".tmp", prefix="autosave_", dir=os.path.dirname(filepath) or "."
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as note_file:
                    json.dump(note_data, note_file, ensure_ascii=False, indent=4)
                os.replace(temporary_path, filepath)
            except Exception:
                if os.path.exists(temporary_path):
                    os.unlink(temporary_path)
                raise

            self.last_saved_text = new_text
            logger.info("Autosaved %s (%d characters)", os.path.basename(filepath), len(new_text))
            self.saved.emit(filepath)
            return True
        except json.JSONDecodeError as error:
            logger.error("Autosave failed because note JSON is invalid", exc_info=True)
            self.error.emit(f"Error JSON: {error}")
        except PermissionError:
            logger.error("Autosave failed because access was denied", exc_info=True)
            self.error.emit(f"Permission denied: {os.path.basename(filepath)}")
        except OSError as error:
            logger.error("Autosave I/O failure", exc_info=True)
            self.error.emit(f"Error writing: {error}")
        except Exception as error:
            logger.error("Autosave unexpected failure", exc_info=True)
            self.error.emit(f"Unknown error: {error}")
        return False
