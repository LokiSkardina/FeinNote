# Responsible for: unique temporary recordings and portable retained audio paths.

import os
import tempfile
from datetime import datetime
from pathlib import Path

from utils.paths import get_app_root


class RecordingStorage:
    """Own microphone audio only; imported files never enter this lifecycle."""

    def __init__(self, app_root=None):
        """Resolve an optional data root without creating files until needed."""
        self.root = Path(app_root or get_app_root()).resolve()
        self.directory = self.root / "recordings"
        self.temporary_directory = self.directory / ".temporary"

    def create_temporary(self):
        """Reserve a unique WAV path for a microphone session; return its path."""
        self.temporary_directory.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="recording_", suffix=".wav", dir=self.temporary_directory)
        os.close(fd)
        return path

    def _temporary(self, source):
        path = Path(source)
        if path.is_symlink() or path.resolve().parent != self.temporary_directory.resolve():
            raise ValueError("Only application-owned temporary recordings may be processed.")
        return path

    def retain(self, source, folder_id="inbox"):
        """Atomically move an owned recording to permanent storage; return a relative path."""
        path = self._temporary(source)
        import re
        if not isinstance(folder_id, str) or not re.fullmatch(r"inbox|[a-f0-9]{32}", folder_id):
            raise ValueError("Invalid recording folder ID.")
        destination_directory = self.directory / folder_id
        if destination_directory.is_symlink() or destination_directory.resolve().parent != self.directory.resolve():
            raise ValueError("Unsafe recording folder path.")
        destination_directory.mkdir(parents=True, exist_ok=True)
        fd, destination = tempfile.mkstemp(
            prefix=f"recording_{datetime.now():%Y%m%d_%H%M%S}_", suffix=".wav", dir=destination_directory)
        os.close(fd)
        try:
            # Both paths are on the app volume. Replace only our empty reservation.
            os.replace(path, destination)
        except OSError:
            Path(destination).unlink(missing_ok=True)
            raise
        return Path(destination).relative_to(self.root).as_posix()

    def discard_temporary(self, source):
        """Delete only a validated temporary recording after successful note saving."""
        self._temporary(source).unlink(missing_ok=True)
