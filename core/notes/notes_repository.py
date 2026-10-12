# Responsible for: JSON note persistence in folders and legacy Inbox storage.

"""Filesystem repository for the application's existing JSON note format."""

import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Any


logger = logging.getLogger("WhisperNote.notes_repository")


class NotesRepository:
    """Create, load, update, and delete JSON notes within the managed storage root.

    Args:
        notes_dir: Root containing legacy notes and category directories.
    """

    REQUIRED_KEYS = ("full_text", "metadata", "timestamp")

    def __init__(self, notes_dir: str) -> None:
        """Store the notes path and make sure it exists."""
        self.notes_dir = notes_dir
        os.makedirs(self.notes_dir, exist_ok=True)
        from core.notes.folder_manager import FolderManager
        self.folders = FolderManager(self.notes_dir)

    @staticmethod
    def _timestamp(note_data: dict[str, Any]) -> int:
        """Return a sortable timestamp while tolerating legacy malformed data."""
        try:
            return int(float(note_data.get("timestamp", 0)))
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _is_valid_note(cls, note_data: Any) -> bool:
        """Return whether decoded JSON has the fields required by the card View."""
        return (isinstance(note_data, dict)
                and all(key in note_data for key in cls.REQUIRED_KEYS)
                and isinstance(note_data["full_text"], str)
                and isinstance(note_data["metadata"], str))

    def _build_filepath(self, timestamp: datetime) -> str:
        """Build a filename compatible with the original minute/second convention."""
        minute_name = f"note_{timestamp.strftime('%Y-%m-%d_%H-%M')}.json"
        minute_path = os.path.join(self.notes_dir, minute_name)
        if not os.path.exists(minute_path):
            return minute_path

        second_name = f"note_{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}.json"
        second_path = os.path.join(self.notes_dir, second_name)
        if not os.path.exists(second_path):
            return second_path

        suffix = 1
        while True:
            candidate = os.path.join(
                self.notes_dir,
                f"note_{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{suffix}.json",
            )
            if not os.path.exists(candidate):
                return candidate
            suffix += 1

    def load_notes(self, folder_id: str | None = None) -> list[tuple[dict[str, Any], str]]:
        """Load valid notes with file paths, ordered newest timestamp first.

        Invalid or unreadable files are logged and ignored so one damaged note
        cannot prevent the history sidebar from loading.
        """
        notes: list[tuple[dict[str, Any], str]] = []
        try:
            directories = [self.notes_dir] + [self.folders.directory(f["id"]) for f in self.folders.list_folders()
                                              if folder_id is None or f["id"] == folder_id]
            filenames = [os.path.join(directory, name) for directory in directories
                         for name in os.listdir(directory) if name != "folders.json"]
        except OSError as error:
            logger.error("Could not enumerate notes directory %s: %s", self.notes_dir, error)
            raise

        for filename in filenames:
            if not filename.lower().endswith(".json"):
                continue
            filepath = filename
            try:
                note_data = self.read_note(filepath)
            except (OSError, json.JSONDecodeError, ValueError) as error:
                logger.error("Could not load note %s: %s", filename, error)
                continue
            if folder_id is None or note_data["folder_id"] == folder_id:
                notes.append((note_data, filepath))

        notes.sort(key=lambda note: self._timestamp(note[0]), reverse=True)
        return notes

    def read_note(self, filepath: str) -> dict[str, Any]:
        """Read and validate one note.

        Args:
            filepath: Path of a note file owned by this repository.

        Returns:
            The decoded note data.

        Raises:
            ValueError: If JSON does not have the expected note structure.
        """
        self._validate_path(filepath)
        with open(filepath, "r", encoding="utf-8") as note_file:
            note_data = json.load(note_file)
        if not self._is_valid_note(note_data):
            raise ValueError("JSON does not match the FeinNote note structure")
        # Legacy files remain in place and belong to Inbox. Physical location is
        # authoritative, allowing crash-safe moves with a single atomic rename.
        parent = os.path.dirname(os.path.abspath(filepath))
        note_data["folder_id"] = ("inbox" if parent == os.path.abspath(self.notes_dir)
                                  else os.path.basename(parent))
        note_data.setdefault("raw_text", note_data["full_text"])
        note_data.setdefault("audio_path", None)
        note_data.setdefault("created_at", note_data["timestamp"])
        note_data.setdefault("schema_version", 2)
        return note_data

    def _validate_path(self, filepath):
        from pathlib import Path
        path = Path(filepath)
        root = Path(self.notes_dir).resolve()
        allowed = {root} | {Path(self.folders.directory(f["id"])).resolve()
                            for f in self.folders.list_folders()}
        if path.is_symlink() or path.resolve().parent not in allowed or path.name == "folders.json" or path.suffix != ".json":
            raise ValueError("Note path is outside the managed storage.")

    def create_note(self, full_text: str, metadata: str, timestamp: int, now: datetime | None = None,
                    folder_id="inbox", raw_text=None, segments=None, audio_path=None, source_name=None) -> tuple[dict[str, Any], str]:
        """Create a compatible JSON note using a collision-safe filename.

        Args:
            full_text: Transcription text to save.
            metadata: Existing card metadata display text.
            timestamp: Unix timestamp used for history ordering.
            now: Optional timestamp used for reproducible filename construction.

        Returns:
            A tuple of saved note data and its absolute file path.
        """
        created_at = now or datetime.now()
        import uuid
        note_data = {"full_text": full_text, "metadata": metadata, "timestamp": timestamp,
                     "raw_text": full_text if raw_text is None else raw_text,
                     "folder_id": folder_id, "created_at": now.timestamp() if now else timestamp, "schema_version": 3,
                     "audio_path": audio_path,
                     "segments": segments or []}
        if source_name:
            note_data["source_name"] = os.path.basename(source_name)
        filepath = os.path.join(self.folders.directory(folder_id),
                                f"note_{created_at:%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex}.json")
        self._write_note(filepath, note_data)
        return note_data, filepath

    def update_note_text(self, filepath: str, full_text: str) -> dict[str, Any]:
        """Replace a note's text while preserving all existing metadata.

        Args:
            filepath: Existing note path.
            full_text: New full text to persist.

        Returns:
            Updated note data.
        """
        note_data = self.read_note(filepath)
        note_data["full_text"] = full_text
        self._write_note(filepath, note_data)
        return note_data

    def complete_refinement(self, filepath, text):
        """Atomically save a confirmed provider result without changing raw text or segments."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Refinement returned no text.")
        data = self.read_note(filepath)
        data["full_text"] = text
        data["postprocessing"] = {"status": "completed"}
        self._write_note(filepath, data)
        return data

    def delete_note(self, filepath: str) -> None:
        """Delete one note file.

        Args:
            filepath: Existing note path.
        """
        self._validate_path(filepath)
        if os.path.exists(filepath):
            os.remove(filepath)

    def move_note(self, filepath, folder_id):
        """Atomically move a note to a folder without overwriting any existing note."""
        import uuid
        note_data = self.read_note(filepath)
        directory = self.folders.directory(folder_id)
        if os.path.dirname(os.path.abspath(filepath)) == os.path.abspath(directory):
            return filepath
        destination = os.path.join(directory, f"note_{uuid.uuid4().hex}.json")
        note_data["folder_id"] = folder_id
        self._write_note(filepath, note_data)
        os.rename(filepath, destination)
        return destination

    @staticmethod
    def _write_note(filepath: str, note_data: dict[str, Any]) -> None:
        """Atomically write well-formed JSON using the legacy UTF-8 encoding.

        Args:
            filepath: Destination JSON path.
            note_data: Note structure to serialize.
        """
        directory = os.path.dirname(filepath) or "."
        # A .json temporary would be counted as a second note by retention
        # between flush and replace, potentially deleting a still-retained note.
        descriptor, temporary_path = tempfile.mkstemp(
            prefix="note_", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as note_file:
                json.dump(note_data, note_file, ensure_ascii=False, indent=4)
            os.replace(temporary_path, filepath)
        except Exception:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise
