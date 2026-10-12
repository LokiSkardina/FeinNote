# Responsible for: transcription engine interface and shared contract.

"""Abstract contract implemented by transcription engines."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class TranscriptionOptions:
    """Configurable decoding parameters for transcription engines."""
    temperature: float = 0.0
    beam_size: int = 5
    best_of: int = 5
    patience: float = 1.0
    condition_on_previous_text: bool = True
    initial_prompt: str | None = None
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    compression_ratio_threshold: float = 2.4


class BaseTranscriptionEngine(ABC):
    """Define the engine operations used by the application controller."""

    @abstractmethod
    def load_model(self, model_name: str) -> bool:
        """Load a named model and return whether loading completed successfully."""

    @abstractmethod
    def unload_model(self) -> None:
        """Idempotently release model resources without changing selection.

        The caller must wait for loading/transcription to finish before unloading.
        Raises an exception if resources could not be fully released.
        """

    @abstractmethod
    def is_model_loaded(self, model_name: str) -> bool:
        """Return whether the named model is active and ready for transcription."""

    @abstractmethod
    def transcribe(
        self, 
        audio_path: str, 
        language: str | None = None, 
        options: TranscriptionOptions | None = None
    ) -> dict[str, Any]:
        """Transcribe audio and return text, segments, and duration.

        Args:
            audio_path: Absolute or relative input audio path.
            language: Optional Whisper language code.
            options: Optional decoding parameters.


        Returns:
            A dictionary with ``text`` (str), ``segments`` (list), and ``duration``
            (float).

        Raises:
            RuntimeError: If no model is loaded or transcription fails.
        """
