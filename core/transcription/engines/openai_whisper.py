# Responsible for: local OpenAI Whisper model loading and transcription.

"""OpenAI Whisper implementation of the shared transcription engine contract."""

import gc
import logging
import os
import sys
import tempfile
import time
from typing import Any

import whisper
from pydub import AudioSegment

from core.transcription.base_engine import BaseTranscriptionEngine, TranscriptionOptions
from utils.paths import get_app_root


logger = logging.getLogger("WhisperNote.transcription")


class OpenAIWhisperEngine(BaseTranscriptionEngine):
    """Load local OpenAI Whisper models and transcribe supported audio files."""

    PROMPTS = {
        "ru": "Привет. Это пример текста, записанного с хорошей пунктуацией. Здесь есть запятые, точки, дефисы — всё как положено. Текст разбит на предложения.",
        "en": "Hello. This is a sample text with proper punctuation. It includes commas, periods, and hyphens. The text is divided into sentences.",
        "de": "Hallo. Dies ist ein Beispieltext mit korrekter Zeichensetzung. Er enthält Kommas, Punkte und Bindestriche.",
        "fr": "Bonjour. Ceci est un exemple de texte avec une ponctuation correcte. Il comprend des virgules, des points et des tirets.",
        "es": "Hola. Este es un texto de ejemplo con puntuación adecuada. Incluye comas, puntos y guiones.",
    }

    def __init__(self, models_dir: str | None = None, device: str | None = None) -> None:
        """Initialize model state and configure the bundled ffmpeg compatibility path.

        Args:
            models_dir: Optional Whisper model cache directory.
            device: Resolved CPU or CUDA device; None retains Whisper's default.
        """
        self.model: Any | None = None
        self.current_model_name: str | None = None
        self.models_dir = models_dir or os.path.join(get_app_root(), "models")
        self.device = device
        os.makedirs(self.models_dir, exist_ok=True)
        self._configure_ffmpeg()

    @staticmethod
    def _configure_ffmpeg() -> None:
        """Use bundled ffmpeg binaries when the application package provides them."""
        if getattr(sys, "frozen", False):
            base_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            bundled_bin = os.path.join(base_path, "assets", "bin")
            if not os.path.exists(bundled_bin):
                bundled_bin = os.path.join(os.path.dirname(sys.executable), "assets", "bin")
        else:
            bundled_bin = os.path.join(get_app_root(), "assets", "bin")

        ffmpeg_path = os.path.join(bundled_bin, "ffmpeg.exe")
        ffprobe_path = os.path.join(bundled_bin, "ffprobe.exe")
        if os.path.exists(ffmpeg_path):
            AudioSegment.converter = ffmpeg_path
            AudioSegment.ffprobe = ffprobe_path
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + bundled_bin

    def is_model_loaded(self, model_name: str) -> bool:
        """Return whether ``model_name`` is already the active loaded model."""
        return self.model is not None and self.current_model_name == model_name

    def unload_model(self) -> None:
        """Drop PyTorch weights, collect cycles and release unused CUDA allocations."""
        import torch

        if self.model is None:
            return
        # Keep only the device descriptor, not a model/tensor reference, before
        # collecting. CPU unload must not initialize or manipulate CUDA.
        device = str(getattr(self.model, "device", self.device))
        self.model = None
        gc.collect()
        if device.startswith("cuda") and torch.cuda.is_available():
            with torch.cuda.device(device):
                torch.cuda.empty_cache()

    def load_model(self, model_name: str) -> bool:
        """Load a Whisper model from the local cache or through Whisper's downloader.

        Args:
            model_name: Whisper model identifier.

        Returns:
            ``True`` when the requested model is ready.

        Raises:
            Exception: Any model loading error raised by Whisper.
        """
        if self.is_model_loaded(model_name):
            return True

        logger.info("Loading Whisper model '%s' from %s", model_name, self.models_dir)
        load_options = {"download_root": self.models_dir}
        if self.device is not None:
            load_options["device"] = self.device
        self.model = whisper.load_model(model_name, **load_options)
        self.current_model_name = model_name
        if hasattr(self.model, "dims"):
            logger.info("Model dimensions: %s", self.model.dims)
        logger.info("Whisper model '%s' loaded", model_name)
        return True

    @staticmethod
    def _process_audio_before_transcription(raw_audio_path: str) -> tuple[str, float]:
        """Normalize audio to the existing 16 kHz mono WAV input when needed.

        Args:
            raw_audio_path: Original audio path selected or recorded by the user.

        Returns:
            A tuple of normalized path and original audio duration in seconds.
        """
        processed_audio_path = None
        try:
            # Own the input handle explicitly, including pydub's fast WAV path.
            with open(raw_audio_path, "rb") as source:
                sound = AudioSegment.from_file(source)
            duration = float(sound.duration_seconds)
            if sound.frame_rate == 16000 and sound.channels == 1 and raw_audio_path.lower().endswith(".wav"):
                return raw_audio_path, duration

            # Never write beside imported audio: a matching processed filename
            # can belong to the user, and the source directory may be read-only.
            directory = os.path.join(get_app_root(), "recordings", ".temporary")
            os.makedirs(directory, exist_ok=True)
            descriptor, processed_audio_path = tempfile.mkstemp(prefix="normalized_", suffix=".wav", dir=directory)
            os.close(descriptor)
            sound.set_frame_rate(16000).set_channels(1).export(processed_audio_path, format="wav").close()
            return processed_audio_path, duration
        except Exception as error:
            if processed_audio_path is not None:
                try:
                    os.remove(processed_audio_path)
                except OSError:
                    logger.warning("Could not remove incomplete normalized audio", exc_info=True)
            logger.error("Audio preprocessing failed; using original input: %s", error)
            return raw_audio_path, 0.0

    def transcribe(
        self, audio_path: str, language: str | None = None, options: TranscriptionOptions | None = None
    ) -> dict[str, Any]:
        """Transcribe an audio file using the active Whisper model.

        Args:
            audio_path: Source audio path.
            language: Optional Whisper language code.
            options: Optional decoding parameters.

        Returns:
            Normalized transcription result with text, segments, and duration.

        Raises:
            RuntimeError: If the model is not loaded or the input is missing.
            Exception: Any error reported by Whisper during transcription.
        """
        if self.model is None:
            raise RuntimeError("The model is not loaded.")
        if not os.path.exists(audio_path):
            raise RuntimeError(f"File not found: {audio_path}")

        processed_path, duration = self._process_audio_before_transcription(audio_path)
        
        opts = options or TranscriptionOptions()
        whisper_options: dict[str, Any] = {
            "task": "transcribe",
            "temperature": opts.temperature,
            "condition_on_previous_text": opts.condition_on_previous_text,
            "no_speech_threshold": opts.no_speech_threshold,
            "logprob_threshold": opts.log_prob_threshold,
            "compression_ratio_threshold": opts.compression_ratio_threshold,
        }
        
        if opts.temperature == 0.0:
            whisper_options["beam_size"] = opts.beam_size
            whisper_options["patience"] = opts.patience
        else:
            whisper_options["best_of"] = opts.best_of
            
        if language:
            whisper_options["language"] = language
            
        if opts.initial_prompt:
            whisper_options["initial_prompt"] = opts.initial_prompt
        elif language and language in self.PROMPTS:
            whisper_options["initial_prompt"] = self.PROMPTS[language]

        logger.info(
            "Starting transcription for %s with model '%s' and language '%s'",
            processed_path, self.current_model_name, language or "auto-detect",
        )
        started_at = time.time()
        try:
            whisper_result = self.model.transcribe(processed_path, **whisper_options)
            logger.info("Transcription completed in %.2f seconds", time.time() - started_at)
            return {
                "text": str(whisper_result.get("text", "")),
                "segments": list(whisper_result.get("segments") or []),
                "duration": duration,
            }
        finally:
            if processed_path != audio_path and os.path.exists(processed_path):
                try:
                    os.remove(processed_path)
                except OSError as error:
                    logger.warning("Could not delete processed audio %s: %s", processed_path, error)
