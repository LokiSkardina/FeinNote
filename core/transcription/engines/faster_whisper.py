# Responsible for: local Faster Whisper model loading and transcription.

"""Faster Whisper implementation of the shared transcription engine contract."""

import gc
import logging
import math
import os
from typing import Any

from faster_whisper import WhisperModel

from core.transcription.base_engine import BaseTranscriptionEngine, TranscriptionOptions
from utils.paths import get_app_root


logger = logging.getLogger("WhisperNote.faster_whisper")


class FasterWhisperEngine(BaseTranscriptionEngine):
    """Load CTranslate2 Whisper models and transcribe with native VAD filtering.

    Args:
        models_dir: Dedicated Hugging Face cache used for Faster Whisper models.
        device: Resolved runtime device, either ``cuda`` or ``cpu``.
    """

    COMPUTE_TYPES = {"cuda": "float16", "cpu": "int8"}

    def __init__(self, models_dir: str | None = None, device: str = "cpu") -> None:
        """Initialize model state and validate the resolved runtime device."""
        normalized_device = str(device).strip().lower()
        if normalized_device not in self.COMPUTE_TYPES:
            raise ValueError("Faster Whisper device must be 'cuda' or 'cpu'.")

        self.models_dir = models_dir or os.path.join(
            get_app_root(), "models", "faster-whisper"
        )
        self.device = normalized_device
        self.compute_type = self.COMPUTE_TYPES[normalized_device]
        self.model: WhisperModel | None = None
        self.current_model_name: str | None = None
        os.makedirs(self.models_dir, exist_ok=True)

    def is_model_loaded(self, model_name: str) -> bool:
        """Return whether ``model_name`` is active on the configured device."""
        return self.model is not None and self.current_model_name == model_name

    def unload_model(self) -> None:
        """Free native CT2 weights and Python references, retaining model/device selection."""
        model = self.model
        self.model = None
        try:
            if model is not None:
                # False fully unloads weights instead of moving them into RAM.
                model.model.unload_model(to_cpu=False)
        finally:
            # Also release the wrapper/tokenizer and CT2 runtime context. Never
            # retain these in the engine even if the native unload raises.
            del model
            gc.collect()

    def load_model(self, model_name: str) -> bool:
        """Load or download a Faster Whisper model for the configured device.

        Args:
            model_name: Faster Whisper model size or compatible repository ID.

        Returns:
            ``True`` when the requested model is ready.

        Raises:
            RuntimeError: If explicit CUDA initialization is unavailable or fails.
            Exception: Any model download or loading error from Faster Whisper.
        """
        if self.is_model_loaded(model_name):
            return True

        if self.device == "cuda":
            self._validate_cuda_runtime()

        logger.info(
            "Loading Faster Whisper model '%s' on %s with %s",
            model_name,
            self.device,
            self.compute_type,
        )
        try:
            candidate = WhisperModel(
                model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.models_dir,
            )
        except Exception as error:
            if self.device == "cuda":
                raise RuntimeError(
                    "GPU was selected, but Faster Whisper could not initialize CUDA. "
                    "Check the NVIDIA driver and GPU availability."
                ) from error
            raise

        # Promote only a completely constructed model so a failed reload cannot
        # replace the last working instance inside this engine object.
        self.model = candidate
        self.current_model_name = model_name
        logger.info("Faster Whisper model '%s' loaded", model_name)
        return True

    def transcribe(
        self, audio_path: str, language: str | None = None, options: TranscriptionOptions | None = None
    ) -> dict[str, Any]:
        """Transcribe audio and normalize Faster Whisper's segment generator.

        Args:
            audio_path: Existing source audio file.
            language: Optional Whisper language code.
            options: Optional decoding parameters.

        Returns:
            A dictionary containing ``text``, ``segments``, and ``duration``.

        Raises:
            RuntimeError: If no model is loaded, input is missing, or CUDA fails.
            Exception: Any transcription error from Faster Whisper.
        """
        if self.model is None:
            raise RuntimeError("The Faster Whisper model is not loaded.")
        if not os.path.exists(audio_path):
            raise RuntimeError(f"File not found: {audio_path}")
        if self.device == "cuda":
            self._validate_cuda_runtime()
            
        opts = options or TranscriptionOptions()
        fw_options: dict[str, Any] = {
            "task": "transcribe",
            "temperature": opts.temperature,
            "condition_on_previous_text": opts.condition_on_previous_text,
            "no_speech_threshold": opts.no_speech_threshold,
            "log_prob_threshold": opts.log_prob_threshold,
            "compression_ratio_threshold": opts.compression_ratio_threshold,
            "vad_filter": True,
        }
        
        if opts.temperature == 0.0:
            fw_options["beam_size"] = opts.beam_size
            fw_options["patience"] = opts.patience
        else:
            fw_options["best_of"] = opts.best_of
            
        if language:
            fw_options["language"] = language
            
        if opts.initial_prompt:
            fw_options["initial_prompt"] = opts.initial_prompt

        logger.info(
            "Starting Faster Whisper transcription for %s on %s",
            audio_path,
            self.device,
        )
        try:
            segment_iterator, info = self.model.transcribe(audio_path, **fw_options)
            normalized_segments = [
                {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": str(segment.text),
                }
                for segment in segment_iterator
            ]
        except Exception as error:
            if self.device == "cuda":
                raise RuntimeError(
                    "Faster Whisper GPU transcription failed. CUDA may no longer "
                    "be available; check the NVIDIA driver and GPU state."
                ) from error
            raise

        duration = self._normalize_duration(getattr(info, "duration", 0.0))
        text = "".join(segment["text"] for segment in normalized_segments).strip()
        return {
            "text": text,
            "segments": normalized_segments,
            "duration": duration,
        }

    @staticmethod
    def _normalize_duration(value: object) -> float:
        """Return a non-negative numeric duration from engine metadata."""
        try:
            duration = float(value)
            return max(0.0, duration) if math.isfinite(duration) else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0.0

    @staticmethod
    def _validate_cuda_runtime() -> None:
        """Fail clearly when an explicitly selected CUDA runtime is unavailable."""
        import torch

        try:
            available = bool(torch.cuda.is_available())
        except Exception as error:
            raise RuntimeError(
                "GPU was selected, but CUDA availability could not be checked."
            ) from error
        if not available:
            raise RuntimeError("GPU was selected, but CUDA is not currently available.")
