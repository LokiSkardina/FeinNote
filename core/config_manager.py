# Responsible for: application configuration persistence and access.

"""Configuration loading and persistence for the application."""

import json
import logging
import math
import os
import tempfile
import time
from typing import Any

from utils.paths import get_app_root


logger = logging.getLogger("WhisperNote.config")


class ConfigManager:
    """Own application settings and persist compatible values to ``config.json``.

    Args:
        config_path: Optional absolute path for the configuration file.

    The manager tolerates a missing or malformed file, migrates legacy model
    settings in memory, and writes only when an explicit setter is called.
    """

    OPENAI_WHISPER = "openai-whisper"
    FASTER_WHISPER = "faster-whisper"
    DEFAULT_MODELS = {
        OPENAI_WHISPER: "tiny",
        FASTER_WHISPER: "tiny",
    }
    DEFAULT_ADVANCED = {
        "temperature": 0.0,
        "beam_size": 5,
        "best_of": 5,
        "patience": 1.0,
        "condition_on_previous_text": True,
        "initial_prompt": None,
        "no_speech_threshold": 0.6,
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
    }
    DEFAULTS = {
        "engine": OPENAI_WHISPER,
        "model": "tiny",
        "models": DEFAULT_MODELS,
        "device_mode": "auto",
        "theme": "light",
        "language": None,
        "advanced_transcription": DEFAULT_ADVANCED,
    }
    APPLICATION_DEFAULTS = {
        "minimize_to_tray": False,
        "close_to_tray": False,
        "keep_recording_audio": False,
    }
    MODEL_MEMORY_MODES = ("keep_loaded", "release_before_postprocessing", "release_after_transcription")

    def get_model_memory_mode(self) -> str:
        """Return one memory policy; legacy or unknown values keep the model loaded."""
        value = self._config.get("model_memory_mode", "keep_loaded")
        return value if value in self.MODEL_MEMORY_MODES else "keep_loaded"

    def set_model_memory_mode(self, mode: str) -> None:
        """Validate and persist a policy, rolling back memory if saving fails."""
        if mode not in self.MODEL_MEMORY_MODES:
            raise ValueError("Unknown model memory mode.")
        previous = dict(self._config)
        self._config["model_memory_mode"] = mode
        try:
            self._save()
        except Exception:
            self._config = previous
            raise

    def get_application_settings(self) -> dict[str, bool]:
        """Return validated application preferences, defaulting legacy configs to off."""
        stored = self.get_section("application")
        return {name: stored[name] if type(stored.get(name)) is bool else default
                for name, default in self.APPLICATION_DEFAULTS.items()}

    def set_application_setting(self, name: str, enabled: bool) -> None:
        """Persist one known boolean preference without dropping the other settings."""
        if name not in self.APPLICATION_DEFAULTS or type(enabled) is not bool:
            raise ValueError("Unknown application setting or invalid boolean value.")
        settings = self.get_application_settings()
        settings[name] = enabled
        self.set_section("application", settings)

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize the manager and load the current configuration."""
        self.config_path = config_path or os.path.join(get_app_root(), "config.json")
        self._config = self._load()

    def _load(self) -> dict[str, Any]:
        """Read configuration and normalize legacy transcription fields in memory."""
        try:
            with open(self.config_path, "r", encoding="utf-8") as config_file:
                raw_config = json.load(config_file)
            if not isinstance(raw_config, dict):
                raise ValueError("Configuration root must be an object")
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as error:
            logger.warning("Configuration load failed; using defaults: %s", error)
            return self._default_config()

        # Preserve unrelated settings while adding current fields without an
        # eager migration write during application startup.
        config = dict(raw_config)
        legacy_model = self._valid_text(raw_config.get("model")) or self.DEFAULT_MODELS[
            self.OPENAI_WHISPER
        ]
        raw_models = raw_config.get("models")
        models = dict(raw_models) if isinstance(raw_models, dict) else {}
        models[self.OPENAI_WHISPER] = (
            self._valid_text(models.get(self.OPENAI_WHISPER)) or legacy_model
        )
        models[self.FASTER_WHISPER] = (
            self._valid_text(models.get(self.FASTER_WHISPER))
            or self.DEFAULT_MODELS[self.FASTER_WHISPER]
        )

        config["engine"] = (
            self._valid_text(raw_config.get("engine")) or self.OPENAI_WHISPER
        )
        config["model"] = models[self.OPENAI_WHISPER]
        config["models"] = models
        config["device_mode"] = (
            self._valid_text(raw_config.get("device_mode")) or "auto"
        )
        config.setdefault("theme", self.DEFAULTS["theme"])
        config.setdefault("language", self.DEFAULTS["language"])
        
        # Migrate advanced_transcription dictionary
        raw_advanced = raw_config.get("advanced_transcription")
        advanced = dict(raw_advanced) if isinstance(raw_advanced, dict) else {}
        config["advanced_transcription"] = self.DEFAULT_ADVANCED.copy()
        config["advanced_transcription"].update(advanced)
        
        return config

    @classmethod
    def _default_config(cls) -> dict[str, Any]:
        """Return independent default configuration data with a fresh model map."""
        config = cls.DEFAULTS.copy()
        config["models"] = cls.DEFAULT_MODELS.copy()
        config["advanced_transcription"] = cls.DEFAULT_ADVANCED.copy()
        return config

    @staticmethod
    def _valid_text(value: object) -> str | None:
        """Return a non-empty string value or ``None`` for invalid config data."""
        return value if isinstance(value, str) and value else None

    def _save(self) -> None:
        """Atomically write the current configuration to disk.

        Raises:
            OSError: If the configuration cannot be safely persisted.
        """
        directory = os.path.dirname(self.config_path) or "."
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix="config_", suffix=".json", dir=directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
                json.dump(self._config, config_file, ensure_ascii=False, indent=4)
            # Windows may briefly deny replacement while another reader holds
            # the file. Retry only these transient lock/access codes, bounded
            # to 200 ms total; persistent errors still reach the caller.
            for attempt in range(5):
                try:
                    os.replace(temporary_path, self.config_path)
                    break
                except PermissionError as error:
                    if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        except Exception:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise

    def get_engine(self) -> str:
        """Return the selected transcription engine identifier."""
        return str(self._config.get("engine", self.OPENAI_WHISPER))

    def get_confirm_note_deletion(self) -> bool:
        """Return the single-note confirmation preference; legacy/invalid values mean on."""
        value = self.get_section("notes").get("confirm_note_deletion", True)
        return value if type(value) is bool else True

    def set_confirm_note_deletion(self, enabled: bool) -> None:
        """Persist one boolean note preference without replacing folder selection."""
        if type(enabled) is not bool:
            raise ValueError("Confirm note deletion must be a boolean.")
        settings = self.get_section("notes")
        settings["confirm_note_deletion"] = enabled
        self.set_section("notes", settings)

    def get_section(self, name: str) -> dict:
        """Return an independent copy of an optional feature settings section."""
        from copy import deepcopy
        value = self._config.get(name, {})
        return deepcopy(value) if isinstance(value, dict) else {}

    def set_section(self, name: str, value: dict) -> None:
        """Persist a feature section and restore memory if the atomic write fails."""
        self._update_values({name: value})

    def _update_values(self, changes: dict) -> None:
        """Commit related fields together, restoring memory if disk persistence fails."""
        from copy import deepcopy
        previous = deepcopy(self._config)
        self._config.update(deepcopy(changes))
        try:
            self._save()
        except Exception:
            self._config = previous
            raise

    def set_engine(self, engine_id: str) -> None:
        """Persist the selected transcription engine identifier.

        Args:
            engine_id: Stable engine identifier selected by the user.
        """
        models = self.get_section("models") or self.DEFAULT_MODELS.copy()
        models.setdefault(engine_id, self.DEFAULT_MODELS.get(engine_id, "tiny"))
        self._update_values({"engine": engine_id, "models": models})

    def get_model(self, engine_id: str | None = None) -> str:
        """Return the model selected for an engine or the active engine.

        Args:
            engine_id: Optional engine identifier. The selected engine is used
                when this argument is omitted.
        """
        selected_engine = engine_id or self.get_engine()
        models = self._config.get("models")
        if isinstance(models, dict):
            model_name = self._valid_text(models.get(selected_engine))
            if model_name:
                return model_name
        return self.DEFAULT_MODELS.get(selected_engine, "tiny")

    def set_model(self, model_name: str, engine_id: str | None = None) -> None:
        """Persist a model selection scoped to one transcription engine.

        Args:
            model_name: Model identifier selected by the user.
            engine_id: Optional target engine. The selected engine is used when
                omitted.
        """
        selected_engine = engine_id or self.get_engine()
        models = self.get_section("models") or self.DEFAULT_MODELS.copy()
        models[selected_engine] = model_name
        changes = {"models": models}
        if selected_engine == self.OPENAI_WHISPER:
            changes["model"] = model_name
        self._update_values(changes)

    def get_device_mode(self) -> str:
        """Return the persisted device mode: ``auto``, ``gpu``, or ``cpu``."""
        return str(self._config.get("device_mode", "auto"))

    def set_device_mode(self, device_mode: str) -> None:
        """Persist the selected transcription device mode.

        Args:
            device_mode: Device preference selected by the user.
        """
        self._update_values({"device_mode": device_mode})

    def set_transcription_selection(
        self, engine_id: str, model_name: str, device_mode: str
    ) -> None:
        """Atomically persist engine, engine-scoped model, and device mode.

        Args:
            engine_id: Stable transcription engine identifier.
            model_name: Model selected for that engine.
            device_mode: Device preference selected by the user.
        """
        models = self.get_section("models") or self.DEFAULT_MODELS.copy()
        models[engine_id] = model_name
        changes = {"engine": engine_id, "device_mode": device_mode, "models": models}
        if engine_id == self.OPENAI_WHISPER:
            changes["model"] = model_name
        self._update_values(changes)

    def get_theme(self) -> str:
        """Return the selected application theme name."""
        return str(self._config.get("theme", self.DEFAULTS["theme"]))

    def set_theme(self, theme_name: str) -> None:
        """Persist the selected application theme name.

        Args:
            theme_name: Supported theme identifier, currently ``light`` or ``dark``.
        """
        self._update_values({"theme": theme_name})

    def get_language(self) -> str | None:
        """Return the selected Whisper language code, if one is selected."""
        language = self._config.get("language")
        return language if isinstance(language, str) and language else None

    def set_language(self, language_code: str | None) -> None:
        """Persist the selected Whisper language code.

        Args:
            language_code: Whisper language code or ``None`` for no selection.
        """
        self._update_values({"language": language_code})

    def get_advanced_transcription_options(self) -> dict[str, Any]:
        """Return independent advanced options that callers may edit before saving."""
        advanced = self._config.get("advanced_transcription")
        if not isinstance(advanced, dict):
            advanced = {}
        # Config files are user-editable. Unknown keys must not reach the
        # TranscriptionOptions constructor, and malformed values must not reach
        # Qt spinboxes or leave the controller busy before a worker is started.
        result = self.DEFAULT_ADVANCED.copy()
        for key, default in result.items():
            value = advanced.get(key, default)
            if key == "initial_prompt":
                valid = value is None or isinstance(value, str)
            elif key == "condition_on_previous_text":
                valid = type(value) is bool
            elif key in ("beam_size", "best_of"):
                valid = type(value) is int and 1 <= value <= 2147483647
            elif key.endswith("threshold") and value is None:
                # Whisper supports None to disable individual fallback checks.
                valid = True
            else:
                try:
                    valid = type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    valid = False
                if valid and key == "temperature":
                    valid = value >= 0
                elif valid and key in ("patience", "compression_ratio_threshold"):
                    valid = value > 0
                elif valid and key == "no_speech_threshold":
                    valid = 0 <= value <= 1
            if valid:
                result[key] = value
        return result

    def set_advanced_transcription_options(self, options: dict[str, Any]) -> None:
        """Persist the advanced transcription options dictionary.

        Args:
            options: Dictionary of advanced settings matching the schema.
        """
        self.set_section("advanced_transcription", options)
