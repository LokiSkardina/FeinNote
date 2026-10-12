# Responsible for: transcription engine catalogs, model cache state, and device availability.

"""Structured registry data shared by transcription controllers and settings views."""

import os
from dataclasses import dataclass

import torch

from utils.paths import get_app_root


@dataclass(frozen=True)
class ModelInfo:
    """Describe one engine model and whether its required weights are cached."""

    name: str
    downloaded: bool


@dataclass(frozen=True)
class EngineInfo:
    """Describe one transcription engine and its supported models."""

    engine_id: str
    models: tuple[ModelInfo, ...]


@dataclass(frozen=True)
class DeviceInfo:
    """Describe one persisted device mode and its current runtime resolution."""

    mode: str
    available: bool
    resolved_device: str


class ModelRegistry:
    """Expose engine catalogs, cache state, and safe device resolution."""

    OPENAI_WHISPER = "openai-whisper"
    FASTER_WHISPER = "faster-whisper"
    ENGINE_IDS = (OPENAI_WHISPER, FASTER_WHISPER)

    MODEL_NAMES = (
        "tiny.en", "tiny", "base.en", "base", "small.en", "small",
        "medium.en", "medium", "turbo", "large-v3",
    )
    FASTER_WHISPER_MODEL_NAMES = (
        "tiny.en", "tiny", "base.en", "base", "small.en", "small",
        "medium.en", "medium", "turbo", "large-v2", "large-v3",
    )
    FASTER_WHISPER_REPOSITORIES = {
        "tiny.en": "Systran/faster-whisper-tiny.en",
        "tiny": "Systran/faster-whisper-tiny",
        "base.en": "Systran/faster-whisper-base.en",
        "base": "Systran/faster-whisper-base",
        "small.en": "Systran/faster-whisper-small.en",
        "small": "Systran/faster-whisper-small",
        "medium.en": "Systran/faster-whisper-medium.en",
        "medium": "Systran/faster-whisper-medium",
        "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "large-v2": "Systran/faster-whisper-large-v2",
        "large-v3": "Systran/faster-whisper-large-v3",
    }
    _FILE_NAME_OVERRIDES = {"turbo": "large-v3-turbo.pt", "large-v3": "large-v3.pt"}

    def __init__(self, models_dir: str | None = None) -> None:
        """Set the root directory used by both engine-specific model caches.

        Args:
            models_dir: Optional model cache root. Defaults to ``models/``.
        """
        self.models_dir = models_dir or os.path.join(get_app_root(), "models")

    def get_engines(self) -> list[EngineInfo]:
        """Return structured records for every available transcription engine."""
        return [
            EngineInfo(
                engine_id=engine_id,
                models=tuple(self.get_model_options(engine_id)),
            )
            for engine_id in self.ENGINE_IDS
        ]

    def get_engine_ids(self) -> list[str]:
        """Return stable engine identifiers without presentation labels."""
        return list(self.ENGINE_IDS)

    def get_models(self, engine_id: str | None = None) -> list[str]:
        """Return model names for an engine, defaulting to legacy OpenAI Whisper."""
        selected_engine = engine_id or self.OPENAI_WHISPER
        if selected_engine == self.OPENAI_WHISPER:
            return list(self.MODEL_NAMES)
        if selected_engine == self.FASTER_WHISPER:
            return list(self.FASTER_WHISPER_MODEL_NAMES)
        raise ValueError(f"Unsupported transcription engine: {selected_engine}")

    def get_model_filename(self, model_name: str) -> str:
        """Return OpenAI Whisper's expected cached filename for a model."""
        return self._FILE_NAME_OVERRIDES.get(model_name, f"{model_name}.pt")

    def get_models_dir(self, engine_id: str) -> str:
        """Return the cache directory used by a supported engine."""
        if engine_id == self.OPENAI_WHISPER:
            return self.models_dir
        if engine_id == self.FASTER_WHISPER:
            return os.path.join(self.models_dir, self.FASTER_WHISPER)
        raise ValueError(f"Unsupported transcription engine: {engine_id}")

    def is_model_present(
        self, model_name: str, engine_id: str = OPENAI_WHISPER
    ) -> bool:
        """Return whether a model has complete required files in its local cache."""
        if model_name not in self.get_models(engine_id):
            return False
        if engine_id == self.OPENAI_WHISPER:
            return os.path.isfile(
                os.path.join(self.models_dir, self.get_model_filename(model_name))
            )

        repository_id = self.FASTER_WHISPER_REPOSITORIES[model_name]
        repository_cache = "models--" + repository_id.replace("/", "--")
        snapshots_dir = os.path.join(
            self.get_models_dir(engine_id), repository_cache, "snapshots"
        )
        if not os.path.isdir(snapshots_dir):
            return False

        # A snapshot is usable only after the model and its runtime config exist.
        try:
            snapshot_names = os.listdir(snapshots_dir)
        except OSError:
            return False
        return any(
            os.path.isfile(os.path.join(snapshots_dir, snapshot, "model.bin"))
            and os.path.isfile(os.path.join(snapshots_dir, snapshot, "config.json"))
            for snapshot in snapshot_names
        )

    def get_model_options(self, engine_id: str | None = None) -> list[ModelInfo]:
        """Return structured cache status for every model supported by an engine."""
        selected_engine = engine_id or self.OPENAI_WHISPER
        return [
            ModelInfo(
                name=model_name,
                downloaded=self.is_model_present(model_name, selected_engine),
            )
            for model_name in self.get_models(selected_engine)
        ]

    @staticmethod
    def is_cuda_available() -> bool:
        """Return whether PyTorch reports a usable CUDA runtime."""
        try:
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def resolve_device(self, mode: str) -> str:
        """Resolve a persisted device mode to ``cuda`` or ``cpu`` safely."""
        normalized_mode = str(mode).strip().lower()
        if normalized_mode == "auto":
            return "cuda" if self.is_cuda_available() else "cpu"
        if normalized_mode == "cpu":
            return "cpu"
        if normalized_mode == "gpu":
            if not self.is_cuda_available():
                raise RuntimeError("GPU was selected, but CUDA is not currently available.")
            return "cuda"
        raise ValueError(f"Unsupported device mode: {mode}")

    def get_device_options(self) -> list[DeviceInfo]:
        """Return structured availability and resolution for all device modes."""
        cuda_available = self.is_cuda_available()
        return [
            DeviceInfo(
                mode="auto",
                available=True,
                resolved_device="cuda" if cuda_available else "cpu",
            ),
            DeviceInfo(mode="gpu", available=cuda_available, resolved_device="cuda"),
            DeviceInfo(mode="cpu", available=True, resolved_device="cpu"),
        ]
