# Responsible for: Qt startup, platform integration, and diagnostic resources.

import sys
import ctypes

if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "lokiskardina.whispernote.v0.1.1"
        )
    except Exception:
        pass

import os
import subprocess
import faulthandler
import logging

if getattr(sys, "frozen", False):
    internal_dir = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(sys.executable)
    candidates = []
    if internal_dir:
        candidates.append(internal_dir)
        candidates.append(os.path.join(internal_dir, "torch", "lib"))
    candidates.append(exe_dir)
    for d in candidates:
        if d and os.path.exists(d):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

import torch
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox
from utils.logger import setup_logger
from utils.paths import get_resource_path, get_data_path
from core.single_instance import SingleInstance

class NullWriter:
    def write(self, text): pass
    def flush(self): pass

if sys.stdout is None or sys.stderr is None:
    sys.stdout = NullWriter()
    sys.stderr = NullWriter()

if sys.platform == "win32":
    _orig_popen = subprocess.Popen
    def _hidden_popen(*args, **kwargs):
        if "startupinfo" not in kwargs:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = si
        return _orig_popen(*args, **kwargs)
    subprocess.Popen = _hidden_popen

def _set_taskbar_icon(hwnd: int, ico_path: str) -> None:
    if not os.path.exists(ico_path):
        return
    try:
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        user32 = ctypes.windll.user32
        hbig = user32.LoadImageW(None, ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        hsmall = user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if hbig:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hbig)
        if hsmall:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hsmall)
    except Exception:
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("FeinNote")
    instance = SingleInstance(get_data_path(), app)
    try:
        primary = instance.acquire_or_activate()
    except (OSError, RuntimeError) as error:
        QMessageBox.critical(None, "FeinNote startup", str(error))
        sys.exit(1)
    if not primary:
        sys.exit(0)
    # No controller, notes/config write, Whisper import or tray is created in a
    # second process. Keep ownership until the primary event loop has drained.
    from app_window import WhisperNoteApp
    logger = setup_logger("WhisperNote")
    os.makedirs(get_data_path("logs"), exist_ok=True)
    crash_log = open(get_data_path("logs", "crash_report.log"), "a", encoding="utf-8")
    faulthandler.enable(file=crash_log)
    try:
        ico_path = get_resource_path(os.path.join("assets", "icons", "app_icon.ico"))
        app_icon = QIcon(ico_path) if os.path.exists(ico_path) else QIcon()
        app.setWindowIcon(app_icon)
        window = WhisperNoteApp()
        instance.activated.connect(window.restore_from_tray)
        window.setWindowIcon(app_icon)
        window.show()
        if sys.platform == "win32":
            _set_taskbar_icon(int(window.winId()), ico_path)
        exit_code = app.exec()
    finally:
        faulthandler.disable()
        crash_log.close()
        logging.shutdown()
        instance.close()
    sys.exit(exit_code)
