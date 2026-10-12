# Responsible for: application-wide transcription, configuration, recording, and note orchestration.

"""Top-level controller that exposes the workflows consumed by the View layer."""

import logging
import os
from collections.abc import Mapping
from typing import Any

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from controllers.notes_controller import NotesController
from controllers.batch_controller import BatchController
from controllers.recording_controller import RecordingController
from core.autosave import AutosaveManager
from core.config_manager import ConfigManager
from core.exporter.service import ExportService
from core.notes.notes_repository import NotesRepository
from core.transcription.engines.openai_whisper import OpenAIWhisperEngine
from core.transcription.model_registry import ModelRegistry
from core.worker import Worker
from core.postprocessing.service import PostProcessingService
from core.recording_storage import RecordingStorage
from utils.paths import get_app_root


logger = logging.getLogger("WhisperNote.app_controller")


class AppController(QObject):
    """Coordinate the application workflow without building or editing widgets.

    Args:
        config_manager: Optional configuration manager for dependency injection.
        recording_controller: Optional recording state machine.
        notes_controller: Optional notes controller.
        engine: Optional transcription engine.
        model_registry: Optional registry for the selected engine.
    """

    model_loading = pyqtSignal(str)
    model_ready = pyqtSignal()
    model_load_failed = pyqtSignal(str)
    model_selection_reverted = pyqtSignal(str)
    language_forced = pyqtSignal(str)
    language_required = pyqtSignal(str)
    microphone_unavailable = pyqtSignal()

    recording_state_changed = pyqtSignal(str)
    recording_duration_changed = pyqtSignal(int)
    recording_stop_requested = pyqtSignal()
    recording_cancelled = pyqtSignal()
    recording_invalid = pyqtSignal(str, int, bool)
    recording_interrupted = pyqtSignal(object, int)
    recording_watchdog = pyqtSignal()

    processing_started = pyqtSignal()
    transcription_completed = pyqtSignal(object)
    transcription_empty = pyqtSignal()
    transcription_failed = pyqtSignal(str)
    transcription_warning = pyqtSignal(str)
    processing_finished = pyqtSignal()
    refining_started = pyqtSignal()
    refinement_failed = pyqtSignal(str)
    post_settings_changed = pyqtSignal()
    connection_tested = pyqtSignal(str)
    models_received = pyqtSignal(object)
    application_settings_changed = pyqtSignal()
    note_preferences_changed = pyqtSignal()
    model_memory_changed = pyqtSignal()
    model_memory_busy_changed = pyqtSignal(bool)
    model_memory_failed = pyqtSignal(str)
    shutdown_ready = pyqtSignal()
    storage_warning = pyqtSignal(str)
    runtime_phase_changed = pyqtSignal(str)

    notes_loaded = pyqtSignal(object)
    note_created = pyqtSignal(object, str)
    note_opened = pyqtSignal(object, str)
    note_deleted = pyqtSignal(str)
    notes_filtered = pyqtSignal(object)
    active_note_cleared = pyqtSignal()
    note_autosaved = pyqtSignal(str, str)
    autosave_error = pyqtSignal(str)
    notes_error = pyqtSignal(str)

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        recording_controller: RecordingController | None = None,
        notes_controller: NotesController | None = None,
        engine: Any | None = None,
        model_registry: ModelRegistry | None = None,
    ) -> None:
        """Create collaborating services and connect their UI-neutral signals."""
        super().__init__()
        self.config_manager = config_manager or ConfigManager()
        self.export_service = ExportService()
        self.model_registry = model_registry or ModelRegistry()
        if engine is not None:
            self.engine = engine
            self._active_engine_id = self.get_selected_engine()
        else:
            try:
                self.engine = self._create_engine(
                    self.get_selected_engine(), self.get_resolved_device()
                )
                self._active_engine_id = self.get_selected_engine()
            except Exception:
                # Defer the user-facing failure until initialize(), after the
                # View has connected to model-load error signals.
                logger.warning(
                    "Configured engine could not be constructed during startup",
                    exc_info=True,
                )
                self.engine = self._create_engine(ModelRegistry.OPENAI_WHISPER, "cpu")
                self._active_engine_id = ModelRegistry.OPENAI_WHISPER
        self.recording_controller = recording_controller or RecordingController()
        if notes_controller:
            self.notes_controller = notes_controller
        else:
            notes_path = os.path.join(get_app_root(), "notes")
            self.notes_controller = NotesController(
                NotesRepository(notes_path), AutosaveManager(delay_ms=1500)
            )

        self.is_loading_model = False
        self._pipeline_loading = False
        self._model_load_failed = False
        self.is_processing = False
        self.is_unloading_model = False
        self._unload_thread = None
        self._unload_worker = None
        self._unload_result = None
        self._unload_for_pipeline = False
        self._pending_refinement_text = None
        self._pipeline_memory_mode = "keep_loaded"
        self._transcription_result = None
        self._transcription_error = None
        self._loading_model_name: str | None = None
        self._loading_candidate_engine: Any | None = None
        self._loading_selection: tuple[str, str, str] | None = None
        self._previous_selection: tuple[str, str, str] | None = None
        self._previous_engine: Any | None = None
        self._previous_language: str | None = None
        self._persist_loading_selection = False
        self._model_thread: QThread | None = None
        self._model_worker: Worker | None = None
        self._transcription_thread: QThread | None = None
        self._transcription_worker: Worker | None = None
        self._fallback_duration: float | int | None = None
        self.post_processing = PostProcessingService(self.config_manager)
        self._network_jobs = []
        self._refining = False
        self._pipeline_folder = "inbox"
        self._raw_note_path = None
        self._pipeline_provider = None
        self._pipeline_prompt = self.post_processing.settings()["prompt"]
        self.recording_storage = RecordingStorage()
        self._pipeline_recording = None
        self._pipeline_keep_audio = False
        self.is_shutting_down = False
        self.batch = BatchController(self)
        self.batch.finished.connect(self._on_transcription_finished)
        self.batch.phase_changed.connect(self.runtime_phase_changed)
        self.batch.phase_changed.connect(self._pipeline_phase)
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setInterval(50)
        self._shutdown_timer.timeout.connect(self._finish_shutdown_when_idle)

        self._connect_recording_signals()
        self._connect_notes_signals()

    def initialize(self) -> None:
        """Load persisted notes and begin non-blocking selected-model loading."""
        if self.get_selected_model().endswith(".en") and self.get_selected_language() != "en":
            # English-only model selection has always forced the matching language.
            self.config_manager.set_language("en")
        self.notes_controller.select_folder(self.config_manager.get_section("notes").get("active_folder", "inbox"))
        self.notes_controller.schedule_cleanup()
        self.load_selected_model()

    def select_folder(self, folder_id):
        """Switch the current history and persist the destination for new notes."""
        if not self.notes_controller.select_folder(folder_id):
            return
        settings = self.config_manager.get_section("notes")
        settings["active_folder"] = self.notes_controller.active_folder
        self.config_manager.set_section("notes", settings)

    def confirm_note_deletion(self) -> bool:
        """Return whether an ordinary single-note delete needs UI confirmation."""
        return self.config_manager.get_confirm_note_deletion()

    def set_confirm_note_deletion(self, enabled: bool) -> None:
        """Persist the single-note preference and notify open Settings views."""
        self.config_manager.set_confirm_note_deletion(enabled)
        self.note_preferences_changed.emit()

    def get_export_data(self, filepath=None, edited_text=None) -> dict:
        """Return a note snapshot, including unsaved final text only for the active note."""
        selected = filepath or self.notes_controller.active_filepath
        data = self.notes_controller.repository.read_note(selected) if selected else {"full_text": "", "segments": []}
        if edited_text is not None and not self.notes_controller.viewing_original and (not selected or selected == self.notes_controller.active_filepath):
            data["full_text"] = edited_text
        return data

    def export_note(self, note, destination, format_name, *, overwrite=False):
        """Export a captured note snapshot through the shared format service."""
        return self.export_service.export(note, destination, format_name, overwrite=overwrite)

    def get_post_settings(self):
        """Return only non-secret provider settings for the Settings View."""
        return self.post_processing.settings()

    def get_application_settings(self):
        """Return persisted Application preferences to the View."""
        return self.config_manager.get_application_settings()

    def set_model_memory_mode(self, mode: str) -> None:
        """Persist the policy for future transcriptions and refresh Settings."""
        self.config_manager.set_model_memory_mode(mode)
        self.model_memory_changed.emit()

    def can_unload_model(self) -> bool:
        """Allow manual release only while model and recording workflows are idle."""
        return (not self._is_busy() and self.recording_controller.state == RecordingController.IDLE
                and self.engine.is_model_loaded(self.get_selected_model()))

    def can_load_model(self) -> bool:
        """Allow preloading only when the selected model is not resident and the engine is idle."""
        return (not self._is_busy() and self.recording_controller.state == RecordingController.IDLE
                and not self.engine.is_model_loaded(self.get_selected_model()))

    def load_model_now(self) -> bool:
        """Preload the saved selection asynchronously; never implicitly download a model."""
        if not self.can_load_model():
            return False
        if not self.model_registry.is_model_present(self.get_selected_model(), self.get_selected_engine()):
            self.model_memory_failed.emit("Model is not downloaded. Select it in Transcription settings first.")
            return False
        return self._start_model_load(self.get_selected_engine(), self.get_selected_model(), self.get_selected_device_mode())

    def model_memory_status(self):
        """Describe actual engine residency rather than the saved model selection."""
        if self.is_loading_model or self._pipeline_loading:
            return "Loading model…"
        if self.is_unloading_model or (self.batch.running and self.batch.phase == "Unloading model"):
            return "Unloading model…"
        if self.engine.is_model_loaded(self.get_selected_model()):
            return f"Model loaded ({str(self.engine.device).upper()})"
        return "Model load failed" if self._model_load_failed else "Model unloaded"

    def _pipeline_phase(self, phase):
        self._pipeline_loading = phase == "Loading model"
        if phase == "Loading model":
            self._model_load_failed = False
        elif phase == "Model load failed":
            self._model_load_failed = True
        self.model_memory_changed.emit()

    def unload_model_now(self) -> bool:
        """Release the current model asynchronously without changing configuration."""
        if not self.can_unload_model():
            return False
        self._start_model_unload(for_pipeline=False)
        return True

    def _start_model_unload(self, for_pipeline: bool) -> None:
        """Run serialized resource release off the UI thread, retaining busy state."""
        self.is_unloading_model = True
        self.runtime_phase_changed.emit("Unloading model")
        self._unload_for_pipeline = for_pipeline
        self._unload_result = None
        engine = self.engine

        def release():
            try:
                engine.unload_model()
                return {"ok": True}
            except Exception:
                logger.exception("Transcription model release failed")
                return {"ok": False}

        worker, thread = Worker(release), QThread(self)
        worker.moveToThread(thread)
        worker.signals.result.connect(self._receive_unload_result)
        worker.signals.finished.connect(thread.quit)
        worker.signals.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_model_unloaded)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._unload_worker, self._unload_thread = worker, thread
        self.model_memory_busy_changed.emit(True)
        thread.start()

    def _receive_unload_result(self, result):
        """Hold the outcome until the release thread has completely exited."""
        self._unload_result = result

    def _on_model_unloaded(self):
        """Continue the pipeline only after release; never start an LLM on failed release."""
        succeeded = bool(self._unload_result and self._unload_result.get("ok"))
        pipeline = self._unload_for_pipeline
        self._unload_thread = self._unload_worker = None
        self.is_unloading_model = False
        if not succeeded:
            self._pending_refinement_text = None
            self.model_memory_failed.emit(
                "Could not fully release the transcription model. "
                "Any saved transcription is preserved; refinement was not started.")
        if pipeline:
            self._continue_after_release()
        self.model_memory_busy_changed.emit(False)

    def set_application_setting(self, name, enabled):
        """Persist a boolean preference and notify open settings and tray Views."""
        self.config_manager.set_application_setting(name, enabled)
        self.application_settings_changed.emit()

    def save_post_settings(self, settings, api_key=None):
        """Validate and save provider settings and publish Refine availability."""
        self.post_processing.save(settings, api_key)
        self.post_settings_changed.emit()

    def refine_enabled(self):
        """Return the persisted toggle only when a usable provider is configured."""
        return bool(self.config_manager.get_section("refine").get("enabled", False)) and self.post_processing.configured()

    def set_refine_enabled(self, enabled):
        """Reject enabling Refine until provider configuration is complete."""
        if enabled and not self.post_processing.configured():
            return False
        self.config_manager.set_section("refine", {"enabled": bool(enabled)})
        self.post_settings_changed.emit()
        return True

    def _run_network_job(self, function, callback):
        """Run a sanitized network function using the existing Qt worker contract."""
        worker, thread = Worker(function), QThread(self)
        worker.moveToThread(thread)
        worker.signals.result.connect(callback)
        worker.signals.finished.connect(thread.quit)
        worker.signals.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._network_jobs.remove((thread, worker)))
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._network_jobs.append((thread, worker))
        thread.start()

    def test_post_connection(self):
        """Test saved credentials and /models asynchronously without generation."""
        provider = self.post_processing.provider()
        if provider is None:
            raise ValueError("Configure a post-processing provider first.")
        def test():
            try:
                provider.test_connection()
                return {"message": "Connection successful. Model generation was not tested."}
            except Exception:
                return {"message": "Connection failed. Check the URL, server and API key."}
        self._run_network_job(test, lambda result: self.connection_tested.emit(result["message"]))

    def refresh_post_models(self, settings, api_key, request_id):
        """Fetch an explicitly requested catalog off-thread without saving drafts or generating text."""
        provider = self.post_processing.catalog_provider(settings, api_key)
        def fetch():
            try:
                return {"id": request_id, "models": provider.list_models()}
            except Exception:
                return {"id": request_id, "error": "Catalog request failed. Check the URL, connection and API key."}
        self._run_network_job(fetch, self.models_received.emit)

    def has_active_jobs(self):
        """Return whether closing would interrupt a live worker thread."""
        return self.is_loading_model or self.is_processing or self.is_unloading_model or bool(self._network_jobs) or self.notes_controller.has_background_work() or any(
            thread is not None
            for thread in (self._model_thread, self._transcription_thread, self._unload_thread)
            if not self._thread_deleted(thread))

    @staticmethod
    def _thread_deleted(thread):
        from PyQt6 import sip
        return thread is not None and sip.isdeleted(thread)

    def set_text_getter(self, callback: Any) -> None:
        """Register the View callback used by NotesController autosave.

        Args:
            callback: Zero-argument callable returning current editor text.
        """
        self.notes_controller.set_text_getter(callback)

    def get_selected_model(self) -> str:
        """Return the currently persisted model selection."""
        return self.config_manager.get_model()

    def get_selected_engine(self) -> str:
        """Return the currently persisted transcription engine identifier."""
        return self.config_manager.get_engine()

    def get_selected_device_mode(self) -> str:
        """Return the persisted device preference."""
        return self.config_manager.get_device_mode()

    def get_resolved_device(self) -> str:
        """Resolve the persisted device preference against current hardware."""
        return self.model_registry.resolve_device(self.get_selected_device_mode())

    def get_transcription_settings(self) -> dict[str, object]:
        """Return structured engine, model, and device data for the settings View."""
        return {
            "selected_engine": self.get_selected_engine(),
            "selected_model": self.get_selected_model(),
            "selected_models": {
                engine_id: (self.config_manager.get_model(engine_id)
                            if self.config_manager.get_model(engine_id) in self.model_registry.get_models(engine_id)
                            else ConfigManager.DEFAULT_MODELS.get(engine_id, "tiny"))
                for engine_id in self.model_registry.get_engine_ids()
            },
            "selected_language": self.get_selected_language(),
            "selected_device_mode": self.get_selected_device_mode(),
            "resolved_device": self.get_resolved_device(),
            "engines": self.model_registry.get_engines(),
            "devices": self.model_registry.get_device_options(),
        }

    def get_selected_language(self) -> str | None:
        """Return the currently persisted language selection."""
        return self.config_manager.get_language()

    def get_selected_theme(self) -> str:
        """Return the currently persisted theme selection."""
        return self.config_manager.get_theme()

    def set_theme(self, theme_name: str) -> None:
        """Persist a theme selected by the View.

        Args:
            theme_name: Theme identifier selected by the user.
        """
        self.config_manager.set_theme(theme_name)

    def get_model_names(self) -> list[str]:
        """Return models for the selected engine for the existing combo box."""
        return self.model_registry.get_models(self.get_selected_engine())

    def get_languages(self) -> dict[str, str]:
        """Return display-name to language-code data provided by Whisper."""
        return {
            name.capitalize(): code
            for code, name in self.engine_language_mapping().items()
        }

    @staticmethod
    def engine_language_mapping() -> dict[str, str]:
        """Read Whisper's current language mapping within the controller layer."""
        import whisper

        return dict(whisper.tokenizer.LANGUAGES)

    def load_selected_model(self) -> bool:
        """Load the persisted selected model asynchronously.

        Returns:
            ``True`` when a load was started or the model is already active.
        """
        return self._start_model_load(
            self.get_selected_engine(),
            self.get_selected_model(),
            self.get_selected_device_mode(),
            persist_selection=False,
        )

    def request_model_change(self, value: object, allow_download: bool = False) -> bool:
        """Accept a legacy model name or a structured transcription selection.

        Args:
            value: Model-name string or mapping with engine, model, and device mode.

        Returns:
            ``True`` if the request was accepted.
        """
        if isinstance(value, str):
            selection = {
                "engine": self.get_selected_engine(),
                "model": value,
                "device_mode": self.get_selected_device_mode(),
            }
        elif isinstance(value, Mapping):
            selection = value
        else:
            return False
        return self.request_transcription_change(selection, allow_download=allow_download)

    def requires_model_download(self, selection: Mapping[str, object]) -> bool:
        """Consult the existing registry, allowing an already resident model offline."""
        engine = str(selection.get("engine", self.get_selected_engine()))
        model = str(selection.get("model", ""))
        if engine not in self.model_registry.get_engine_ids() or model not in self.model_registry.get_models(engine):
            raise ValueError("Unknown engine or model.")
        if (engine == self._active_engine_id and model == self.get_selected_model()
                and selection.get("device_mode", self.get_selected_device_mode()) == self.get_selected_device_mode()
                and self.engine.is_model_loaded(model)):
            return False
        return not self.model_registry.is_model_present(model, engine)

    def request_transcription_change(self, selection: Mapping[str, object], allow_download: bool = False) -> bool:
        """Validate and asynchronously load an engine/model/device selection."""
        if self._is_busy() or self.recording_controller.state != RecordingController.IDLE:
            return False

        engine_id = selection.get("engine")
        model_name = selection.get("model")
        device_mode = selection.get("device_mode")
        if not all(isinstance(value, str) for value in (engine_id, model_name, device_mode)):
            return False

        engine_id = str(engine_id)
        model_name = str(model_name)
        device_mode = str(device_mode).lower()
        if engine_id not in self.model_registry.get_engine_ids():
            return False
        if model_name not in self.model_registry.get_models(engine_id):
            return False
        valid_device_modes = {
            option.mode for option in self.model_registry.get_device_options()
        }
        if device_mode not in valid_device_modes:
            return False
        # No config mutation, engine construction or worker creation before consent.
        if not allow_download and self.requires_model_download(selection):
            return False
        try:
            resolved_device = self.model_registry.resolve_device(device_mode)
        except (RuntimeError, ValueError) as error:
            self.model_selection_reverted.emit(self.get_selected_model())
            self.model_load_failed.emit(str(error))
            return False

        if (
            engine_id == self._active_engine_id
            and model_name == self.get_selected_model()
            and device_mode == self.get_selected_device_mode()
            and self.engine.is_model_loaded(model_name)
        ):
            return True

        return self._start_model_load(
            engine_id,
            model_name,
            device_mode,
            resolved_device=resolved_device,
            persist_selection=True,
        )

    def request_language_change(self, language_code: str | None) -> bool:
        """Persist a newly selected Whisper language code.

        Args:
            language_code: Whisper language code selected by the View.

        Returns:
            ``True`` if a supported selection was stored.
        """
        if self._is_busy():
            return False
        if language_code is not None and language_code not in self.engine_language_mapping():
            return False
        self.config_manager.set_language(language_code)
        return True

    def prepare_for_file_selection(self) -> bool:
        """Validate and clear active note state before showing a file picker.

        Returns:
            ``True`` when the View can safely show its file picker.
        """
        return self._prepare_for_audio_input("file")

    def start_recording(self) -> bool:
        """Validate microphone and start a controller-owned recording session.

        Returns:
            ``True`` when recording was started.
        """
        if not self._can_start_audio_input("recording"):
            return False
        if not self.recording_controller.is_microphone_available():
            self.microphone_unavailable.emit()
            return False
        if not self.notes_controller.clear_active_note():
            return False
        return self.recording_controller.start()

    def stop_recording(self) -> bool:
        """Request normal finalization of an active recording session."""
        return self.recording_controller.stop()

    def pause_recording(self) -> bool:
        """Pause the active recording session."""
        return self.recording_controller.pause()

    def resume_recording(self) -> bool:
        """Resume the active paused recording session."""
        return self.recording_controller.resume()

    def cancel_recording(self) -> bool:
        """Cancel an active recording session and clean its temporary output."""
        return self.recording_controller.cancel()

    def start_file_transcription(
        self, filepath: str, duration: float | int | None = None, prepared: bool = False
    ) -> bool:
        """Transcribe a selected or dropped audio file without blocking the View.

        Args:
            filepath: Supported local audio file path.
            duration: Optional recorder duration fallback.
            prepared: Whether active-note clearing already occurred for a picker.

        Returns:
            ``True`` when a background transcription worker was started.
        """
        if not filepath or not os.path.exists(filepath):
            self.transcription_failed.emit(f"File not found: {filepath}")
            return False
        if not prepared and not self._prepare_for_audio_input("file"):
            return False
        if prepared and (self._is_busy() or self.recording_controller.state != RecordingController.IDLE):
            return False
        return self._start_transcription(filepath, duration)

    def start_file_batch(self, paths):
        """Start a frozen, sequential multi-file pipeline with one note per source."""
        if not paths or not self._prepare_for_audio_input("file"):
            return False
        if self._active_engine_id != self.get_selected_engine():
            return False
        self.is_processing = True
        self.processing_started.emit()
        try:
            self.batch.start(paths)
        except (OSError, ValueError, RuntimeError):
            self.is_processing = False
            self.processing_finished.emit()
            self.refinement_failed.emit("Queue configuration is invalid. Check provider settings.")
            return False
        return True

    def select_note(self, filepath: str) -> None:
        """Open a history note selected by the View.

        Args:
            filepath: Note JSON path emitted by a history card.
        """
        self.notes_controller.select_note(filepath)

    def delete_note(self, filepath: str) -> None:
        """Delete a history note requested by the View.

        Args:
            filepath: Note JSON path emitted by a history card.
        """
        self.notes_controller.delete_note(filepath)

    def filter_notes(self, search_text: str) -> None:
        """Search note text and emit matching history paths for the View.

        Args:
            search_text: Case-insensitive history query.
        """
        self.notes_controller.filter_notes(search_text)

    def request_autosave(self) -> None:
        """Forward an editor change to the delayed autosave controller."""
        self.notes_controller.request_autosave()

    def clear_active_note(self) -> bool:
        """Return whether an editor reset safely saved and cleared the active note."""
        return self.notes_controller.clear_active_note()

    def shutdown(self) -> bool:
        """Stop recording only after pending edits are saved; return success."""
        if not self.notes_controller.force_save():
            return False
        self.recording_controller.shutdown()
        return True

    def request_shutdown(self):
        """Stop new work and asynchronously drain workers before emitting shutdown_ready."""
        if self.is_shutting_down:
            return True
        if not self.shutdown():
            return False
        self.is_shutting_down = True
        if self.batch.running:
            self.batch.stop()
        self._shutdown_timer.start()
        return True

    def _finish_shutdown_when_idle(self):
        if self.has_active_jobs() or self.recording_controller.has_active_capture():
            return
        self._shutdown_timer.stop()
        self.shutdown_ready.emit()

    def _connect_recording_signals(self) -> None:
        """Forward low-level recording events and start transcription when ready."""
        recording = self.recording_controller
        recording.state_changed.connect(self.recording_state_changed)
        recording.duration_changed.connect(self.recording_duration_changed)
        recording.stop_requested.connect(self.recording_stop_requested)
        recording.recording_cancelled.connect(self.recording_cancelled)
        recording.recording_invalid.connect(self.recording_invalid)
        recording.recording_failed.connect(self._on_recording_failed)
        recording.watchdog_triggered.connect(self.recording_watchdog)
        recording.recording_ready.connect(self._on_recording_ready)

    def _connect_notes_signals(self) -> None:
        """Forward note events so the View needs only one controller reference."""
        notes = self.notes_controller
        notes.notes_loaded.connect(self.notes_loaded)
        notes.note_created.connect(self.note_created)
        notes.note_opened.connect(self.note_opened)
        notes.note_deleted.connect(self.note_deleted)
        notes.notes_filtered.connect(self.notes_filtered)
        notes.active_note_cleared.connect(self.active_note_cleared)
        notes.note_autosaved.connect(self.note_autosaved)
        notes.autosave_error.connect(self.autosave_error)
        notes.error.connect(self.notes_error)

    def _can_start_audio_input(self, context: str) -> bool:
        """Validate state and language before recording or transcribing audio."""
        if self._is_busy() or self.recording_controller.state != RecordingController.IDLE:
            return False
        if not self.get_selected_language():
            self.language_required.emit(context)
            return False
        return True

    def _prepare_for_audio_input(self, context: str) -> bool:
        """Validate an audio workflow and clear the currently edited note."""
        if not self._can_start_audio_input(context):
            return False
        return self.notes_controller.clear_active_note()

    def _is_busy(self) -> bool:
        """Return whether model loading or transcription prevents a new action."""
        return self.is_loading_model or self.is_processing or self.is_unloading_model or self.is_shutting_down or any(
            thread is not None and not self._thread_deleted(thread)
            for thread in (self._model_thread, self._transcription_thread, self._unload_thread))

    def _create_engine(self, engine_id: str, resolved_device: str) -> Any:
        """Construct a concrete engine selected by validated controller data."""
        if engine_id == ModelRegistry.OPENAI_WHISPER:
            return OpenAIWhisperEngine(
                models_dir=self.model_registry.get_models_dir(engine_id),
                device=resolved_device,
            )
        if engine_id == ModelRegistry.FASTER_WHISPER:
            # Import lazily so existing OpenAI-only installations can still
            # start before the optional engine is selected and installed.
            from core.transcription.engines.faster_whisper import FasterWhisperEngine

            return FasterWhisperEngine(
                models_dir=self.model_registry.get_models_dir(engine_id),
                device=resolved_device,
            )
        raise ValueError(f"Unsupported transcription engine: {engine_id}")

    def _start_model_load(
        self,
        engine_id: str,
        model_name: str,
        device_mode: str,
        resolved_device: str | None = None,
        persist_selection: bool = False,
    ) -> bool:
        """Load a candidate engine in a thread and promote it only on success."""
        if self._is_busy() or self.recording_controller.state != RecordingController.IDLE:
            return False
        try:
            runtime_device = resolved_device or self.model_registry.resolve_device(device_mode)
            candidate_engine = self._create_engine(engine_id, runtime_device)
        except Exception as error:
            logger.error("Could not construct transcription engine", exc_info=True)
            self._model_load_failed = True
            self.model_selection_reverted.emit(self.get_selected_model())
            self.model_load_failed.emit(str(error))
            return False

        active_device = getattr(self.engine, "device", runtime_device)
        if (
            engine_id == self._active_engine_id
            and active_device == runtime_device
            and self.engine.is_model_loaded(model_name)
        ):
            if persist_selection:
                self.config_manager.set_transcription_selection(
                    engine_id, model_name, device_mode
                )
            self._enforce_loaded_model_language(model_name)
            self.model_ready.emit()
            return True

        self.is_loading_model = True
        self._model_load_failed = False
        self._loading_model_name = model_name
        self._loading_candidate_engine = candidate_engine
        self._loading_selection = (engine_id, model_name, device_mode)
        self._previous_selection = (
            self.get_selected_engine(),
            self.get_selected_model(),
            self.get_selected_device_mode(),
        )
        self._previous_engine = self.engine
        self._previous_language = self.get_selected_language()
        self._persist_loading_selection = persist_selection
        action = (
            "Loading"
            if self.model_registry.is_model_present(model_name, engine_id)
            else "Downloading"
        )
        self.model_loading.emit(f"{action} '{model_name}'")
        def load_candidate():
            # The engine contract permits False; Worker otherwise treats every
            # non-dictionary return value as success, including a failed load.
            if not candidate_engine.load_model(model_name):
                raise RuntimeError("The selected model could not be loaded.")

        worker = Worker(load_candidate)
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.signals.success.connect(self._on_model_loaded)
        worker.signals.error.connect(self._on_model_load_error)
        worker.signals.finished.connect(thread.quit)
        worker.signals.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_model_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._model_worker = worker
        self._model_thread = thread
        thread.start()
        return True

    def _on_model_thread_finished(self) -> None:
        """Drop worker references and refresh manual-release availability after loading."""
        self._model_thread = self._model_worker = None
        self.model_memory_changed.emit()

    def _on_model_loaded(self) -> None:
        """Promote a loaded candidate and atomically persist its selection."""
        selection = self._loading_selection
        candidate_engine = self._loading_candidate_engine
        if selection and candidate_engine:
            engine_id, model_name, device_mode = selection
            try:
                if self._persist_loading_selection:
                    self.config_manager.set_transcription_selection(
                        engine_id, model_name, device_mode
                    )
                self._enforce_loaded_model_language(model_name)
            except OSError as error:
                self._on_model_load_error(
                    f"Model loaded, but its selection could not be saved: {error}"
                )
                return
            self.engine = candidate_engine
            self._active_engine_id = engine_id
        self.is_loading_model = False
        self._clear_model_load_state()
        self.model_ready.emit()

    def _enforce_loaded_model_language(self, model_name: str) -> None:
        """Apply English-only restrictions after success, not on a failed candidate."""
        if model_name.endswith(".en"):
            self.config_manager.set_language("en")
            self.language_forced.emit("en")

    def _on_model_load_error(self, error_text: str) -> None:
        """Retain the prior engine and restore its atomic config selection."""
        self.is_loading_model = False
        self._model_load_failed = True
        requested_model = self._loading_model_name
        previous_selection = self._previous_selection
        if self._previous_engine is not None:
            self.engine = self._previous_engine
        if previous_selection and self._persist_loading_selection:
            previous_engine_id, previous_model, previous_device_mode = previous_selection
            try:
                self.config_manager.set_transcription_selection(
                    previous_engine_id, previous_model, previous_device_mode
                )
                if self.get_selected_language() != self._previous_language:
                    self.config_manager.set_language(self._previous_language)
            except OSError:
                logger.error("Could not restore transcription selection", exc_info=True)
        else:
            previous_model = self.get_selected_model()
        self.model_selection_reverted.emit(previous_model)
        self._clear_model_load_state()
        logger.error("Model load failed for %s", requested_model)
        self.model_load_failed.emit(error_text)

    def _clear_model_load_state(self) -> None:
        """Release candidate and rollback references after a load completes."""
        self._loading_model_name = None
        self._loading_candidate_engine = None
        self._loading_selection = None
        self._previous_selection = None
        self._previous_engine = None
        self._previous_language = None
        self._persist_loading_selection = False

    def _start_transcription(self, filepath: str, duration: float | int | None, recording=False) -> bool:
        """Run normalized engine transcription in a dedicated worker thread."""
        if self._is_busy():
            return False
        selected_model = self.get_selected_model()
        if self._active_engine_id != self.get_selected_engine():
            self.transcription_failed.emit("The selected engine is not initialized.")
            return False

        self._pipeline_memory_mode = self.config_manager.get_model_memory_mode()
        self._pending_refinement_text = None
        self._raw_note_path = None
        self._transcription_result = self._transcription_error = None
        self._pipeline_folder = self.notes_controller.active_folder
        self._pipeline_recording = filepath if recording else None
        self._pipeline_keep_audio = self.get_application_settings()["keep_recording_audio"]
        self._pipeline_provider = None
        self._pipeline_prompt = self.post_processing.settings()["prompt"]
        if self.refine_enabled():
            try:
                self._pipeline_provider = self.post_processing.provider()
            except Exception:
                self.refinement_failed.emit("Provider unavailable. The original transcription will be saved.")
        self.is_processing = True
        self._fallback_duration = duration
        self.processing_started.emit()
        
        opts_dict = self.config_manager.get_advanced_transcription_options()
        defaults = self.config_manager.DEFAULT_ADVANCED
        from core.transcription.base_engine import TranscriptionOptions
        
        temperature = opts_dict.get("temperature", defaults["temperature"])
        beam_size = opts_dict.get("beam_size", defaults["beam_size"])
        best_of = opts_dict.get("best_of", defaults["best_of"])
        
        if temperature == 0.0 and best_of != defaults["best_of"]:
            warning = "Temperature is 0.0; 'best of' setting will be ignored."
            logger.warning(warning)
            self.transcription_warning.emit(warning)
        elif temperature > 0.0 and beam_size != defaults["beam_size"]:
            warning = f"Temperature is > 0.0; 'beam size' setting will be ignored."
            logger.warning(warning)
            self.transcription_warning.emit(warning)

        options = TranscriptionOptions(**opts_dict)
        
        engine, language = self.engine, self.get_selected_language()

        def transcribe():
            # Release keeps the selected engine/model/device. Reload only when
            # needed, on this same worker, before decoding the captured input.
            if not engine.is_model_loaded(selected_model):
                worker.signals.phase.emit("Loading model")
                try:
                    if not engine.load_model(selected_model):
                        raise RuntimeError("The selected model could not be loaded.")
                except Exception:
                    worker.signals.phase.emit("Model load failed")
                    raise
            worker.signals.phase.emit("Transcribing")
            return engine.transcribe(filepath, language, options)

        worker = Worker(transcribe)
        worker.signals.phase.connect(self.runtime_phase_changed)
        worker.signals.phase.connect(self._pipeline_phase)
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.signals.result.connect(self._receive_transcription_result)
        worker.signals.error.connect(self._receive_transcription_error)
        worker.signals.finished.connect(thread.quit)
        worker.signals.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_transcription_worker_finished)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._transcription_worker = worker
        self._transcription_thread = thread
        thread.start(QThread.Priority.LowPriority)
        return True

    def _receive_transcription_result(self, result):
        """Buffer output until the worker has stopped using the engine."""
        self._transcription_result = result

    def _receive_transcription_error(self, message):
        """Buffer failures so recovery cannot start while the worker still runs."""
        self._transcription_error = message

    def _on_transcription_worker_finished(self):
        """Save and advance only after QThread.finished, never on an early result signal."""
        result, error = self._transcription_result, self._transcription_error
        self._transcription_thread = self._transcription_worker = None
        self._transcription_result = self._transcription_error = None
        if error or result is None:
            self.transcription_failed.emit(error or "The transcription worker returned no result.")
            # Decoder failures still finish a model use. Honor release-after
            # just as the batch path does, without starting refinement.
            self._advance_after_transcription(None)
        else:
            self._on_transcription_result(result)

    def _on_transcription_result(self, result: dict[str, Any]) -> None:
        """Save non-empty engine output as a note and publish normalized results."""
        text = str(result.get("text", "")).strip()
        if not text:
            self.transcription_empty.emit()
            self._advance_after_transcription(None)
            return
        duration = result.get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            duration = self._fallback_duration
        audio_path = None
        audio_warning = ""
        if self._pipeline_recording and self._pipeline_keep_audio:
            try:
                audio_path = self.recording_storage.retain(self._pipeline_recording, self._pipeline_folder or "inbox")
            except (OSError, ValueError):
                audio_warning = "Audio could not be retained. The temporary recording was preserved for recovery."
        try:
            self._raw_note_path = self.notes_controller.create_transcription_note(
                text, duration, raw_text=text, folder_id=self._pipeline_folder,
                segments=result.get("segments", []), audio_path=audio_path)
        except (OSError, ValueError):
            self.transcription_failed.emit("Could not save the transcription. Check storage permissions and free space.")
            self._advance_after_transcription(None)
            return
        if self._pipeline_recording and not self._pipeline_keep_audio:
            try:
                self.recording_storage.discard_temporary(self._pipeline_recording)
            except (OSError, ValueError):
                audio_warning = "The note was saved, but its temporary recording could not be removed."
        if audio_warning:
            self.storage_warning.emit(audio_warning)
        self.transcription_completed.emit(result)
        self._advance_after_transcription(text)

    def _advance_after_transcription(self, text):
        """Apply the captured policy after raw text/segments have been saved."""
        self._pending_refinement_text = text
        mode = self._pipeline_memory_mode
        if mode == "release_after_transcription" or (
                mode == "release_before_postprocessing" and text is not None and self._pipeline_provider is not None):
            self._start_model_unload(for_pipeline=True)
        else:
            self._continue_after_release()

    def _continue_after_release(self):
        """Start refinement from saved raw text, or finish without loading a model."""
        text = self._pending_refinement_text
        self._pending_refinement_text = None
        provider = self._pipeline_provider
        if text is not None and provider is not None and not self.is_shutting_down:
            self._refining = True
            self.refining_started.emit()
            def refine():
                try:
                    return {"text": provider.process(text, self._pipeline_prompt)}
                except Exception:
                    return {"error": "Refinement failed. The original transcription was saved."}
            self._run_network_job(refine, self._on_refined)
        else:
            self._on_transcription_finished()

    def _on_refined(self, result):
        if "error" in result:
            self.refinement_failed.emit(result["error"])
        else:
            try:
                self.save_refined_note(self._raw_note_path, result["text"])
            except (OSError, ValueError):
                self.refinement_failed.emit("Could not save refined text. The original transcription remains saved.")
        self._refining = False
        self._on_transcription_finished()

    def save_refined_note(self, path, text):
        """Persist one successful refinement and refresh the existing card and editor."""
        notes = self.notes_controller
        data = notes.repository.complete_refinement(path, text)
        if notes.active_filepath == path:
            notes.viewing_original = False
            notes.autosave.set_active_note(path, text)
            notes.note_opened.emit(data, path)
        notes.note_autosaved.emit(path, text)

    def _on_transcription_finished(self) -> None:
        """Release the busy state after a transcription worker exits."""
        if self._refining or self.is_unloading_model:
            return
        self.is_processing = False
        self._pipeline_loading = False
        self.model_memory_changed.emit()
        self._fallback_duration = None
        self._pipeline_recording = None
        self.processing_finished.emit()

    def _on_recording_ready(self, filepath: str, duration: int) -> None:
        """Start transcription for a validated completed microphone recording."""
        self._start_transcription(filepath, duration, recording=True)

    def _on_recording_failed(self, filepath: object, duration: int) -> None:
        """Report an interrupted recording and transcribe a saved partial WAV file."""
        self.recording_interrupted.emit(filepath, duration)
        if filepath and os.path.exists(str(filepath)):
            self._start_transcription(str(filepath), duration, recording=True)
