# Responsible for: recording lifecycle state, duration timer, watchdog, and cleanup.

"""Controller-owned recording state machine built around ``AudioRecorder``."""

import logging
import os

import pyaudio
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.recorder import AudioRecorder


logger = logging.getLogger("WhisperNote.recording_controller")


class RecordingController(QObject):
    """Own idle, recording, and paused state without manipulating UI widgets.

    Args:
        recorder: Optional low-level recorder. A default recorder is created for
            application-owned temporary recording workflow.
    """

    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"

    state_changed = pyqtSignal(str)
    duration_changed = pyqtSignal(int)
    stop_requested = pyqtSignal()
    recording_ready = pyqtSignal(str, int)
    recording_cancelled = pyqtSignal()
    recording_failed = pyqtSignal(object, int)
    recording_invalid = pyqtSignal(str, int, bool)
    watchdog_triggered = pyqtSignal()

    def __init__(self, recorder: AudioRecorder | None = None) -> None:
        """Create timers and subscribe to low-level recorder outcomes."""
        super().__init__()
        self.recorder = recorder or AudioRecorder()
        self.state = self.IDLE
        self.duration_seconds = 0
        self.is_stopping = False
        self._cancel_requested = False
        self._shutting_down = False
        self._completion_handled = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_timer)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_stop_timeout)
        self.recorder.recording_finished.connect(self._on_recording_finished)
        self.recorder.recording_failed.connect(self._on_recording_failed)

    @staticmethod
    def is_microphone_available() -> bool:
        """Return whether PyAudio can find at least one microphone input device."""
        audio: pyaudio.PyAudio | None = None
        try:
            audio = pyaudio.PyAudio()
            info = audio.get_host_api_info_by_index(0)
            device_count = int(info.get("deviceCount", 0))
            return any(
                audio.get_device_info_by_host_api_device_index(0, index).get("maxInputChannels", 0) > 0
                for index in range(device_count)
            )
        except Exception:
            logger.error("Unable to check microphone availability", exc_info=True)
            return False
        finally:
            if audio:
                audio.terminate()

    def start(self) -> bool:
        """Start a new recording session from idle state.

        Returns:
            ``True`` if the state transition occurred, otherwise ``False``.
        """
        if not self.can_start():
            return False
        self.duration_seconds = 0
        self.is_stopping = False
        self._cancel_requested = False
        self._completion_handled = False
        self._set_state(self.RECORDING)
        self.duration_changed.emit(self.duration_seconds)
        self.duration_seconds += 1
        self._timer.start(1000)
        try:
            self.recorder.start_recording()
        except OSError:
            self._timer.stop()
            self._set_state(self.IDLE)
            self.recording_failed.emit(None, 0)
            return False
        return True

    def pause(self) -> bool:
        """Pause a running recording session.

        Returns:
            ``True`` when recording entered the paused state.
        """
        if self.state != self.RECORDING or self.is_stopping:
            return False
        self.recorder.pause_recording()
        self._timer.stop()
        self._set_state(self.PAUSED)
        return True

    def resume(self) -> bool:
        """Resume a paused recording session.

        Returns:
            ``True`` when recording resumed.
        """
        if self.state != self.PAUSED or self.is_stopping:
            return False
        self.recorder.resume_recording()
        self._timer.start(1000)
        self._set_state(self.RECORDING)
        return True

    def stop(self) -> bool:
        """Request normal recording finalization and start the existing watchdog.

        Returns:
            ``True`` when a stop request was accepted.
        """
        if self.state not in (self.RECORDING, self.PAUSED) or self.is_stopping:
            return False
        self.is_stopping = True
        self._timer.stop()
        self._watchdog.start(3000)
        self.recorder.manual_stop()
        self.stop_requested.emit()
        return True

    def cancel(self) -> bool:
        """Cancel current recording and remove its temporary output after it exits.

        Returns:
            ``True`` when cancellation was accepted.
        """
        if self.state == self.IDLE:
            return False
        self._cancel_requested = True
        self.is_stopping = False
        self._timer.stop()
        self._watchdog.stop()
        self.recorder.stop_recording()
        self._set_state(self.IDLE)
        self.duration_seconds = 0
        self.duration_changed.emit(self.duration_seconds)
        self.recording_cancelled.emit()
        # The completion callback owns deletion of this exact session. A delayed
        # callback using output_filename could otherwise delete a later recording.
        return True

    def shutdown(self) -> None:
        """Stop an active recorder while the application is closing."""
        self._timer.stop()
        self._watchdog.stop()
        self._shutting_down = True
        if self.has_active_capture() or self.state != self.IDLE:
            self.recorder.stop_recording()

    def has_active_capture(self) -> bool:
        """Return whether the recorder thread still needs time to release resources."""
        return self.recorder.is_running() is True

    def can_start(self) -> bool:
        """Return whether a new session can safely own the microphone and output."""
        return self.state == self.IDLE and not (self.has_active_capture() or self._shutting_down or self._cancel_requested)

    def _advance_timer(self) -> None:
        """Emit elapsed duration using the legacy displayed-time cadence."""
        self.duration_changed.emit(self.duration_seconds)
        self.duration_seconds += 1

    def _on_recording_finished(self, filepath: object) -> None:
        """Validate completed WAV output and emit a transcription-ready file."""
        if self._completion_handled:
            return
        self._completion_handled = True
        self._timer.stop()
        self._watchdog.stop()
        self.is_stopping = False
        path = str(filepath) if filepath else ""
        if self._shutting_down:
            self._set_state(self.IDLE)
            # Preserve a partial recording for recovery instead of starting new work.
            return
        if self._cancel_requested:
            self._cancel_requested = False
            self._set_state(self.IDLE)
            self._delete_file(path or self.recorder.output_filename)
            return

        if not path or not os.path.exists(path):
            self._set_state(self.IDLE)
            self.recording_invalid.emit(path, self.duration_seconds, True)
            return

        duration = self.duration_seconds
        self._set_state(self.IDLE)
        try:
            is_too_small = os.path.getsize(path) < 32000
        except OSError:
            is_too_small = True
        if is_too_small:
            self._delete_file(path)
            self.recording_invalid.emit(path, duration, duration > 5)
            return
        self.recording_ready.emit(path, duration)

    def _on_recording_failed(self, filepath: object) -> None:
        """Forward an interrupted recording and any saved partial WAV file."""
        if self._completion_handled:
            return
        self._completion_handled = True
        self._timer.stop()
        self._watchdog.stop()
        self.is_stopping = False
        self._set_state(self.IDLE)
        if self._shutting_down:
            return
        if self._cancel_requested:
            self._cancel_requested = False
            self._delete_file(str(filepath) if filepath else self.recorder.output_filename)
            return
        self.recording_failed.emit(filepath, self.duration_seconds)

    def _on_stop_timeout(self) -> None:
        """Recover the UI state from a recorder thread that did not stop in time."""
        logger.error("Recording stop watchdog fired")
        self.watchdog_triggered.emit()
        # Do not expose idle while capture still owns its output. A delayed Qt
        # completion and watchdog recovery must share the same one-shot handler.
        if not self.has_active_capture():
            self._on_recording_finished(self.recorder.output_filename)

    def _set_state(self, state: str) -> None:
        """Change state once and notify the View layer."""
        if self.state != state:
            self.state = state
            self.state_changed.emit(state)

    def _delete_temp_file(self) -> None:
        """Delete the standard temporary recording file after cancellation."""
        self._delete_file(self.recorder.output_filename)

    @staticmethod
    def _delete_file(filepath: str) -> None:
        """Remove a temporary recording file while tolerating cleanup failures."""
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                logger.error("Could not delete temporary recording %s", filepath, exc_info=True)
