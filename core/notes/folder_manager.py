# Responsible for: folder metadata and safe physical category directories.

"""Permanent Inbox and user-created folders in the existing JSON storage."""


class FolderManager:
    """Persist names, colors, icons and retention independently of directory IDs."""

    ICONS = ("folder", "work", "book", "star", "idea", "code", "music", "heart", "archive")

    def __init__(self, notes_dir):
        """Load folder metadata from the existing JSON storage root."""
        import os
        from pathlib import Path
        self.root = Path(notes_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "folders.json"
        self._folders = {}
        if self.path.exists():
            import json
            with self.path.open(encoding="utf-8") as source:
                data = json.load(source)
            if not isinstance(data, dict) or not isinstance(data.get("folders"), list):
                raise ValueError("Folder metadata is invalid; restore folders.json from backup.")
            for folder in data["folders"]:
                self._validate(folder)
                self._folders[folder["id"]] = dict(folder)
        if "inbox" not in self._folders:
            self._folders["inbox"] = dict(id="inbox", name="Inbox", color="#64748b",
                                         icon="folder", cleanup={"mode": "disabled", "value": 30})
            self._save()
        for folder_id in self._folders:
            os.makedirs(self.directory(folder_id), exist_ok=True)

    @classmethod
    def _validate(cls, folder):
        import re
        if not isinstance(folder, dict) or not re.fullmatch(r"inbox|[a-f0-9]{32}", str(folder.get("id", ""))):
            raise ValueError("Invalid folder ID.")
        if not isinstance(folder.get("name"), str) or not 1 <= len(folder["name"].strip()) <= 80:
            raise ValueError("Folder name must contain 1 to 80 characters.")
        # Persist names, never paths. Unknown safe names remain readable and the
        # presentation layer supplies a fallback if an asset is unavailable.
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(folder.get("icon", ""))) or not re.fullmatch(r"#[a-fA-F0-9]{6}", str(folder.get("color", ""))):
            raise ValueError("Choose a valid folder icon and color.")
        rule = folder.get("cleanup", {})
        if not isinstance(rule, dict) or rule.get("mode") not in ("disabled", "count", "days"):
            raise ValueError("Choose a valid cleanup mode.")
        if type(rule.get("value")) is not int or rule["value"] < 1:
            raise ValueError("Cleanup value must be at least 1.")

    def _save(self):
        from core.notes.notes_repository import NotesRepository
        NotesRepository._write_note(str(self.path), {"version": 1, "folders": list(self._folders.values())})

    def list_folders(self):
        """Return copies of folders with the permanent Inbox first."""
        from copy import deepcopy
        return deepcopy(sorted(self._folders.values(), key=lambda f: (f["id"] != "inbox", f["name"].casefold())))

    def directory(self, folder_id):
        """Return a validated physical folder directory inside notes/."""
        if folder_id not in self._folders:
            raise ValueError("Folder no longer exists.")
        path = self.root / folder_id
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("Unsafe folder path.")
        return str(path)

    def save_folder(self, name, color, icon, cleanup, folder_id=None):
        """Create or edit metadata; names never become filesystem paths."""
        import os
        import uuid
        from copy import deepcopy
        if folder_id is not None and folder_id not in self._folders:
            raise ValueError("Folder no longer exists.")
        folder_id = folder_id or uuid.uuid4().hex
        folder = dict(id=folder_id, name=name.strip(), color=color, icon=icon, cleanup=dict(cleanup))
        self._validate(folder)
        if folder_id == "inbox":
            folder["name"] = "Inbox"
        previous = deepcopy(self._folders)
        self._folders[folder_id] = folder
        try:
            os.makedirs(self.directory(folder_id), exist_ok=True)
            self._save()
        except Exception:
            self._folders = previous
            raise
        return folder_id

    def remove_empty_folder(self, folder_id):
        """Remove a user folder only after the caller moves every note to Inbox."""
        import os
        if folder_id == "inbox":
            raise ValueError("Inbox cannot be deleted.")
        directory = self.directory(folder_id)
        if os.listdir(directory):
            raise ValueError("Folder contains unreadable or unexpected files; it was not deleted.")
        previous = self._folders.pop(folder_id)
        try:
            self._save()
        except Exception:
            self._folders[folder_id] = previous
            raise
        os.rmdir(directory)
