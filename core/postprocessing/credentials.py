# Responsible for: storing provider secrets in Windows Credential Manager.

"""Native credential storage; secrets never enter application configuration."""

import ctypes
import os
from ctypes import wintypes


class CredentialStore:
    """Read and write generic FeinNote credentials for the current Windows user."""

    def __init__(self, namespace="FeinNote"):
        """Set a non-secret credential target namespace."""
        self.namespace = namespace

    def _api(self):
        if os.name != "nt":
            raise RuntimeError("Secure credential storage requires Windows.")

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        api = ctypes.WinDLL("Advapi32", use_last_error=True)
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api, Credential

    def get(self, provider):
        """Return a saved secret or an empty string when no credential exists."""
        api, credential_type = self._api()
        pointer = ctypes.POINTER(credential_type)()
        if not api.CredReadW(f"{self.namespace}/{provider}", 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return ""
            raise RuntimeError("Unable to read secure credentials.")
        try:
            return ctypes.string_at(pointer.contents.CredentialBlob,
                                    pointer.contents.CredentialBlobSize).decode("utf-16-le")
        finally:
            api.CredFree(pointer)

    def set(self, provider, secret):
        """Save a secret, or remove it when empty; return no secret-bearing data."""
        api, credential_type = self._api()
        target = f"{self.namespace}/{provider}"
        if not secret:
            if not api.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != 1168:
                raise RuntimeError("Unable to remove secure credentials.")
            return
        encoded = secret.encode("utf-16-le")
        if len(encoded) > 2560:
            raise ValueError("API key exceeds the secure storage size limit.")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = credential_type()
        credential.Type, credential.Persist = 1, 2
        credential.TargetName, credential.UserName = target, "FeinNote"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = blob
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise RuntimeError("Unable to save secure credentials.")
