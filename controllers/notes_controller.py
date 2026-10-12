# Responsible for: coordinating note persistence, selection, search, and autosave.

"""Controller that mediates note repository data and the View's editor events."""

import logging
import math
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from core.worker import Worker

from core.autosave import AutosaveManager
from core.notes.notes_repository import NotesRepository


logger = logging.getLogger("WhisperNote.notes_controller")


class NotesController(QObject):
    """Coordinate note lifecycle events without constructing UI widgets.

    Args:
        repository: Repository that owns note JSON files.
        autosave: Delayed active-note persistence service.
    """

    notes_loaded = pyqtSignal(object)
    note_created = pyqtSignal(object, str)
    note_opened = pyqtSignal(object, str)
    note_deleted = pyqtSignal(str)
    notes_filtered = pyqtSignal(object)
    active_note_cleared = pyqtSignal()
    note_autosaved = pyqtSignal(str, str)
    autosave_error = pyqtSignal(str)
    error = pyqtSignal(str)
    folders_changed = pyqtSignal()

    def __init__(self, repository: NotesRepository, autosave: AutosaveManager | None = None) -> None:
        """Initialize repository coordination and forward autosave outcomes."""
        super().__init__()
        self.repository = repository
        self.autosave = autosave or AutosaveManager(delay_ms=1500)
        self.active_filepath: str | None = None
        self.viewing_original = False
        self.active_folder = "inbox"
        self.search_text = ""
        self._cleanup_queue = []
        self._cleanup_jobs = []
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.timeout.connect(self._cleanup_batch)
        self.autosave.saved.connect(self._on_autosave_saved)
        self.autosave.error.connect(self.autosave_error)

    def set_text_getter(self, callback: Callable[[], str]) -> None:
        """Set the View callback used by autosave to read editor text.

        Args:
            callback: Zero-argument callable returning the editor's full text.
        """
        # Original is a display-only view. Never feed it into final-text autosave,
        # including implicit saves triggered by selection, shutdown or cleanup.
        self.autosave.set_text_getter(lambda: self.autosave.last_saved_text if self.viewing_original else callback())

    def select_version(self, original):
        """Flush final edits before changing display; keep the view unchanged on save failure."""
        if not self.active_filepath or not self.autosave.force_save():
            return False
        try:
            data = self.repository.read_note(self.active_filepath)
            if not self.has_refined_version(data):
                return False
            self.viewing_original = bool(original)
            self.note_opened.emit(data, self.active_filepath)
            return True
        except (OSError, ValueError):
            self.error.emit("Could not read the saved note version.")
            return False

    @staticmethod
    def has_refined_version(data):
        """Recognize confirmed completion, never infer refinement from unequal texts."""
        state = data.get("postprocessing")
        return (isinstance(state, dict) and state.get("status") == "completed"
                and isinstance(data.get("raw_text"), str))

    def load_notes(self) -> None:
        """Load ordered notes and emit them for sidebar rendering."""
        try:
            self.notes_loaded.emit(self.repository.load_notes(self.active_folder))
            if self.search_text:
                self.filter_notes(self.search_text)
        except OSError as error:
            logger.error("Unable to load notes", exc_info=True)
            self.error.emit(str(error))

    def create_transcription_note(self, full_text: str, duration_seconds: float | int | None,
                                   raw_text=None, folder_id=None, segments=None, audio_path=None, source_name=None,
                                   defer_cleanup=False):
        """Persist a successful transcription and make it the active note.

        Args:
            full_text: Non-empty transcribed text.
            duration_seconds: Audio duration used for existing metadata display.
        """
        text = full_text.strip()
        if not text:
            return
        created_at = datetime.now()
        timestamp = int(created_at.timestamp())
        note_data, filepath = self.repository.create_note(
            full_text=text,
            metadata=f"{self._format_duration(duration_seconds)} • {created_at.strftime('%Y-%m-%d • %H:%M')}",
            timestamp=timestamp,
            now=created_at,
            folder_id=folder_id or self.active_folder,
            raw_text=raw_text,
            segments=segments,
            audio_path=audio_path,
            source_name=source_name,
        )
        if note_data["folder_id"] == self.active_folder:
            can_open = self.clear_active_note()
            self.note_created.emit(note_data, filepath)
            # The result is already safe on disk; do not replace unsaved edits
            # merely because a background transcription has just completed.
            if can_open:
                self._set_active_note(filepath, text)
                self.note_opened.emit(note_data, filepath)
            if self.search_text:
                self.filter_notes(self.search_text)
        if not defer_cleanup:
            self.schedule_cleanup(note_data["folder_id"])
        return filepath

    def list_folders(self):
        """Return folders for settings and sidebar rendering."""
        return self.repository.folders.list_folders()

    def latest_note_text(self):
        """Read final text of the newest existing note across all folders, or None."""
        import math
        notes = self.repository.load_notes()
        def creation_time(item):
            # New notes have sub-second created_at precision; legacy timestamp
            # remains a fallback. Editing a file must not make it the newest note.
            for key in ("created_at", "timestamp"):
                try:
                    value = float(item[0].get(key, 0))
                    if math.isfinite(value):
                        return value
                except (TypeError, ValueError):
                    continue
            return 0
        return str(max(notes, key=creation_time)[0]["full_text"]) if notes else None

    def select_folder(self, folder_id):
        """Save edits, switch folder, and reload its sidebar notes."""
        if folder_id not in {f["id"] for f in self.list_folders()}:
            folder_id = "inbox"
        if not self.clear_active_note():
            return False
        self.active_folder = folder_id
        self.load_notes()
        self.folders_changed.emit()
        return True

    def save_folder(self, name, color, icon, cleanup, folder_id=None):
        """Persist folder changes and notify all folder Views."""
        result = self.repository.folders.save_folder(name, color, icon, cleanup, folder_id)
        self.folders_changed.emit()
        return result

    def delete_folder(self, folder_id):
        """Move all readable notes to Inbox before removing a confirmed user folder."""
        if folder_id == "inbox":
            raise ValueError("Inbox cannot be deleted.")
        if not self.clear_active_note():
            return False
        for _, path in self.repository.load_notes(folder_id):
            self.repository.move_note(path, "inbox")
        self.repository.folders.remove_empty_folder(folder_id)
        if self.active_folder == folder_id:
            self.active_folder = "inbox"
        self.load_notes()
        self.folders_changed.emit()
        return True

    def move_note(self, filepath, folder_id):
        """Save pending edits and move a note before refreshing the current folder."""
        if filepath == self.active_filepath and not self.clear_active_note():
            return
        self.repository.move_note(filepath, folder_id)
        self.load_notes()

    def cleanup_preview(self, scope, days=30, folder_id=None):
        """Return exact paths for confirmation of a manual cleanup request."""
        from core.notes.cleanup_service import CleanupService
        notes = self.repository.load_notes((folder_id or self.active_folder) if scope == "folder" else None)
        if scope == "days":
            return CleanupService.candidates(notes, {"mode": "days", "value": days})
        if scope not in ("folder", "all"):
            raise ValueError("Unknown cleanup scope.")
        return [path for _, path in notes]

    def delete_paths(self, paths):
        """Queue only the confirmed snapshot for deletion in short GUI batches."""
        self._cleanup_queue.extend(path for path in paths if path not in self._cleanup_queue)
        if self._cleanup_queue:
            self._cleanup_timer.start(1)

    def schedule_cleanup(self, folder_id=None):
        """Plan retention using each folder's own rules and queue bounded deletion."""
        from core.notes.cleanup_service import CleanupService
        folders = [f for f in self.list_folders() if (folder_id is None or f["id"] == folder_id)
                   and f["cleanup"]["mode"] != "disabled"]
        if not folders:
            return
        def plan():
            try:
                return {"plans": [(f, CleanupService.candidates(self.repository.load_notes(f["id"]), f["cleanup"]))
                                  for f in folders]}
            except (OSError, ValueError):
                return {"plans": []}
        worker, thread = Worker(plan), QThread(self)
        worker.moveToThread(thread)
        worker.signals.result.connect(self._planned_cleanup)
        worker.signals.finished.connect(thread.quit)
        worker.signals.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._cleanup_jobs.remove((thread, worker)))
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._cleanup_jobs.append((thread, worker))
        thread.start()

    def _planned_cleanup(self, result):
        current = {f["id"]: f for f in self.list_folders()}
        for folder, paths in result["plans"]:
            # Ignore a plan if settings changed while it was being calculated.
            if current.get(folder["id"]) == folder:
                self.delete_paths(paths)

    def _cleanup_batch(self):
        for _ in range(min(10, len(self._cleanup_queue))):
            path = self._cleanup_queue.pop(0)
            if os.path.isfile(path):
                self.delete_note(path)
        if not self._cleanup_queue:
            self._cleanup_timer.stop()
            self.load_notes()

    def has_background_work(self):
        """Return whether a confirmed cleanup or retention scan is still pending."""
        return bool(self._cleanup_queue or self._cleanup_jobs)

    def select_note(self, filepath: str) -> None:
        """Force-save the old note, open a selected note, and enable autosave.

        Args:
            filepath: JSON note selected by a history card.
        """
        try:
            if not self.force_save():
                return
            note_data = self.repository.read_note(filepath)
            self._set_active_note(filepath, str(note_data["full_text"]))
            self.note_opened.emit(note_data, filepath)
        except (OSError, ValueError) as error:
            logger.error("Unable to open note %s", filepath, exc_info=True)
            self.error.emit(str(error))

    def request_autosave(self) -> None:
        """Schedule a delayed save after editor text changes."""
        self.autosave.on_text_changed()

    def force_save(self) -> bool:
        """Save pending edits and return whether it is safe to leave the editor."""
        return self.autosave.force_save()

    def clear_active_note(self) -> bool:
        """Clear a saved editor, returning False and retaining edits on write failure."""
        if not self.force_save():
            return False
        self.autosave.clear_active_note()
        self.active_filepath = None
        self.viewing_original = False
        self.active_note_cleared.emit()
        return True

    def delete_note(self, filepath: str) -> None:
        """Delete a note and clear active editor state when it was selected.

        Args:
            filepath: JSON note path requested for deletion.
        """
        try:
            if filepath == self.active_filepath and not self.clear_active_note():
                return
            self.repository.delete_note(filepath)
            self.note_deleted.emit(filepath)
        except (OSError, ValueError) as error:
            logger.error("Unable to delete note %s", filepath, exc_info=True)
            self.error.emit(str(error))

    def filter_notes(self, search_text: str) -> None:
        """Emit matching paths so the View can show or hide its cards.

        Args:
            search_text: Case-insensitive full-text search query.
        """
        query = search_text.lower()
        self.search_text = search_text
        try:
            matching_paths = [
                filepath
                for note_data, filepath in self.repository.load_notes(self.active_folder)
                if query in str(note_data.get("full_text", "")).lower()
            ]
            self.notes_filtered.emit(matching_paths)
        except OSError as error:
            logger.error("Unable to filter notes", exc_info=True)
            self.error.emit(str(error))

    def _set_active_note(self, filepath: str, full_text: str) -> None:
        """Configure autosave for the selected existing note."""
        self.active_filepath = filepath
        self.viewing_original = False
        self.autosave.set_active_note(filepath, full_text)

    def _on_autosave_saved(self, filepath: str) -> None:
        """Forward a saved note's updated text for history-card preview refresh."""
        try:
            note_data: dict[str, Any] = self.repository.read_note(filepath)
            self.note_autosaved.emit(filepath, str(note_data["full_text"]))
        except (OSError, ValueError) as error:
            self.autosave_error.emit(str(error))

    @staticmethod
    def _format_duration(seconds: float | int | None) -> str:
        """Format duration exactly as the legacy note metadata did."""
        if type(seconds) not in (int, float) or seconds < 0:
            return "..."
        try:
            if not math.isfinite(seconds):
                return "..."
        except OverflowError:
            return "..."
        total_seconds = int(seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        return f"{hours:d}:{minutes:02d}:{seconds_part:02d}" if hours else f"{minutes:02d}:{seconds_part:02d}"
