# Responsible for: serialized, snapshot-based file transcription and refinement queues.

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from core.transcription.base_engine import TranscriptionOptions
from core.worker import Worker


class BatchController(QObject):
    """Run one operation at a time, persisting raw notes before any refinement."""

    changed = pyqtSignal(object)
    phase_changed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, app):
        """Own queue state and workers without constructing widgets."""
        super().__init__(app)
        self.app = app
        self.running = False
        self.rows = []
        self.thread = self.worker = None
        self.stopping = False
        self.phase = ""

    def start(self, paths):
        """Capture all settings and start stage A; caller holds the application busy gate."""
        self.engine = self.app.engine
        self.model = self.app.get_selected_model()
        self.language = self.app.get_selected_language()
        self.options = TranscriptionOptions(**self.app.config_manager.get_advanced_transcription_options())
        self.folder = self.app.notes_controller.active_folder
        self.memory = self.app.config_manager.get_model_memory_mode()
        self.prompt = self.app.post_processing.settings()["prompt"]
        self.provider = self.app.post_processing.provider() if self.app.refine_enabled() else None
        self.refine_enabled = self.provider is not None
        self.rows = [{"source": str(path), "name": Path(path).name, "status": "Waiting", "path": None,
                      "error": ""} for path in paths]
        self.index = -1
        self.stage = "transcribe"
        self.completed_units = 0
        self.total_units = len(self.rows) * (2 if self.provider else 1)
        self.running, self.stopping = True, False
        self._next()

    def stop(self):
        """Cancel waiting work, allowing the current native/HTTP operation to finish safely."""
        if not self.running:
            return
        self.stopping = True
        self._publish()

    def _publish(self):
        self.changed.emit({"rows": [dict(row) for row in self.rows], "index": self.index + 1,
                           "total": len(self.rows), "done": self.completed_units,
                           "units": self.total_units, "running": self.running,
                           "stopping": self.stopping, "phase": self.phase,
                           "stage": self.stage, "refine_enabled": self.refine_enabled})

    def _phase(self, phase):
        self.phase = phase
        self.phase_changed.emit(phase)
        self._publish()

    def _run(self, function, continuation):
        # Advance only after QThread.finished: result delivery alone is not an
        # engine-lifetime boundary. Catch errors without logging source text.
        self._result = None
        self._continuation = continuation
        def safe_run():
            try:
                return {"value": function()}
            except Exception:
                return {"error": "Operation failed. Any saved original is preserved."}
        self.worker, self.thread = Worker(safe_run), QThread(self)
        self.worker.moveToThread(self.thread)
        self.worker.signals.result.connect(self._receive)
        self.worker.signals.phase.connect(self._phase)
        self.worker.signals.finished.connect(self.thread.quit)
        self.worker.signals.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._operation_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def _receive(self, result):
        self._result = result

    def _operation_finished(self):
        self.thread = self.worker = None
        self._continuation(self._result or {"error": "Worker returned no result."})

    def _next(self):
        if self.stopping or self.app.is_shutting_down:
            self._finish()
            return
        self.index += 1
        if self.index >= len(self.rows):
            if self.stage == "transcribe" and self.provider and any(row["path"] for row in self.rows):
                self.stage, self.index = "refine", -1
                if self.memory == "release_before_postprocessing":
                    self._release(self._next)
                else:
                    self._next()
            else:
                self._finish()
            return
        row = self.rows[self.index]
        if self.stage == "refine":
            if not row["path"]:
                QTimer.singleShot(0, self._next)
                return
            row["status"] = "Refining"
            self._phase("Refining")
            def refine():
                raw = self.app.notes_controller.repository.read_note(row["path"])["raw_text"]
                return self.provider.process(raw, self.prompt)
            self._run(refine, self._refined)
        else:
            row["status"] = "Transcribing"
            self._phase("Transcribing")
            def transcribe():
                if not self.engine.is_model_loaded(self.model):
                    self.worker.signals.phase.emit("Loading model")
                    try:
                        if not self.engine.load_model(self.model):
                            raise RuntimeError("Model load failed")
                    except Exception:
                        self.worker.signals.phase.emit("Model load failed")
                        raise
                self.worker.signals.phase.emit("Transcribing")
                return self.engine.transcribe(row["source"], self.language, self.options)
            self._run(transcribe, self._transcribed)

    def _transcribed(self, result):
        row = self.rows[self.index]
        try:
            if "error" in result:
                raise ValueError(result["error"])
            data = result["value"]
            if not isinstance(data, dict):
                raise ValueError("Engine returned an invalid result.")
            text = str(data.get("text", "")).strip()
            if not text:
                raise ValueError("No speech detected.")
            row["path"] = self.app.notes_controller.create_transcription_note(
                text, data.get("duration"), raw_text=text, folder_id=self.folder,
                segments=data.get("segments", []), source_name=row["name"], defer_cleanup=True)
            row["status"] = "Awaiting Refine" if self.provider else "Completed"
        except (OSError, ValueError, TypeError):
            row["status"], row["error"] = "Failed", "Transcription or saving failed."
            if self.provider:
                self.completed_units += 1
        self.completed_units += 1
        self._publish()
        if self.memory == "release_after_transcription":
            self._release(self._next)
        else:
            self._next()

    def _release(self, continuation):
        self._phase("Unloading model")
        def released(result):
            if "error" in result:
                self.app.model_memory_failed.emit("Model release failed. Saved originals are preserved; queue stopped.")
                self.stopping = True
                self._finish()
            else:
                continuation()
        self._run(self.engine.unload_model, released)

    def _refined(self, result):
        row = self.rows[self.index]
        try:
            if "error" in result:
                raise ValueError(result["error"])
            self.app.save_refined_note(row["path"], result["value"])
            row["status"] = "Completed"
        except (OSError, ValueError):
            row["status"], row["error"] = "Failed", "Refinement or saving failed; original preserved. No automatic retry."
        self.completed_units += 1
        self._publish()
        self._next()

    def _finish(self):
        for row in self.rows:
            if row["status"] == "Waiting":
                row["status"] = "Cancelled"
            elif row["status"] == "Awaiting Refine":
                row["status"] = "Refine cancelled"
        self.running = False
        failed = any(row["status"] == "Failed" for row in self.rows)
        self.phase = "Stopped" if self.stopping else "Completed with errors" if failed else "Completed"
        self.index = min(self.index, len(self.rows) - 1)
        # Keep queue history, not runtime resources. Otherwise replacing the
        # application's engine leaves this queue holding the old model in memory.
        self.engine = self.provider = None
        self._result = self._continuation = None
        self._publish()
        self.app.notes_controller.schedule_cleanup(self.folder)
        self.finished.emit()
