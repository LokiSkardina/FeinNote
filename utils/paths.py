# Responsible for: separating portable persistent data from bundled resources.

import sys
import os


def get_app_root():
    """Return the executable directory when packaged, or the source project root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    if "__compiled__" in globals():
        # Nuitka's original executable location is separate from onefile resources.
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_path(*parts):
    """Return a portable app-local data path, never a bundle extraction path."""
    return os.path.join(get_app_root(), *parts)


def get_resource_path(relative_path):
    """Resolve a bundled resource, supporting packaged builds without _MEIPASS."""
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", get_app_root())
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)
