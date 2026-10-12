# Responsible for: reusable background task execution and signal handling.

"""Generic Qt worker objects used by controllers for non-blocking tasks."""

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal


logger = logging.getLogger("WhisperNote.worker")


class WorkerSignals(QObject):
    """Signals emitted by :class:`Worker` for completion, success, and failures."""

    finished = pyqtSignal()
    error = pyqtSignal(str)
    result = pyqtSignal(dict)
    success = pyqtSignal()
    phase = pyqtSignal(str)


class Worker(QObject):
    """Execute a callable in a ``QThread`` and forward its outcome via signals.

    Args:
        function: Callable to execute in the worker thread.
        *args: Positional arguments passed to ``function``.
        **kwargs: Keyword arguments passed to ``function``.
    """

    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Store the callable and construct the worker signal container."""
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        """Run the callable and emit a result, success signal, or traceback."""
        function_name = getattr(self.function, "__name__", type(self.function).__name__)
        try:
            logger.debug("Worker executing: %s", function_name)
            result_data = self.function(*self.args, **self.kwargs)
            if isinstance(result_data, dict):
                self.signals.result.emit(result_data)
            else:
                self.signals.success.emit()
        except Exception:
            error_text = traceback.format_exc()
            logger.error("Worker error in %s: %s", function_name, error_text)
            self.signals.error.emit(error_text)
        finally:
            self.signals.finished.emit()
            logger.debug("Worker finished: %s", function_name)
