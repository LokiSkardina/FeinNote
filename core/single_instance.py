# Responsible for: per-portable-directory process ownership and local window activation.

import hashlib
import os
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    """Hold an OS file lock and accept activation requests through Qt local IPC."""

    activated = pyqtSignal()

    def __init__(self, directory, parent=None):
        """Scope both the lock and endpoint to the resolved portable data directory."""
        super().__init__(parent)
        self.root = Path(directory).resolve()
        identity = os.path.normcase(str(self.root)).encode("utf-8")
        self.name = "FeinNote-" + hashlib.sha256(identity).hexdigest()[:32]
        self._file = None
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)

    def acquire_or_activate(self, timeout_ms=3000):
        """Return True only for the owner; otherwise notify it or fail closed."""
        self.root.mkdir(parents=True, exist_ok=True)
        stream = open(self.root / ".feinnote.lock", "a+b")
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            # The owner may still be starting its IPC listener. Never steal its
            # lock based on age; the OS releases ownership after a process crash.
            deadline = time.monotonic() + timeout_ms / 1000
            while time.monotonic() < deadline:
                socket = QLocalSocket()
                socket.connectToServer(self.name)
                if socket.waitForConnected(150):
                    socket.write(b"activate\n")
                    socket.waitForBytesWritten(500)
                    socket.disconnectFromServer()
                    return False
                time.sleep(0.05)
            raise RuntimeError("FeinNote is already using this folder, but its window could not be activated. Please wait and try again.")
        self._file = stream
        # Only the proven owner may remove a stale Unix socket. Windows uses
        # named pipes; the file lock is needed because pipe names are not exclusive.
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.close()
            raise RuntimeError("Could not create the FeinNote activation endpoint.")
        return True

    def _accept(self):
        """An accepted local connection is a request to restore the existing window."""
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            self.activated.emit()

    def close(self):
        """Stop IPC and release ownership; the harmless lock file may remain on disk."""
        self.server.close()
        if self._file is not None:
            self._file.close()
            self._file = None
