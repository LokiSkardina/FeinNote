# Responsible for: temporary unused compatibility reference for the pre-refactor engine.

"""Deprecated legacy Whisper implementation retained pending user-approved deletion.

The application no longer imports this module. ``OpenAIWhisperEngine`` in
``core.transcription.engines.openai_whisper`` is the active implementation.
"""

import whisper
import time
import os
import logging
from pydub import AudioSegment
from utils.paths import get_app_root

logger = logging.getLogger('WhisperNote.transcription')

class TranscriptionCore:
    def __init__(self):
        self.model = None
        self.current_model_name = None
        
        base_dir = get_app_root()
        self.models_dir = os.path.join(base_dir, "models")
        os.makedirs(self.models_dir, exist_ok=True)

    def _process_audio_before_transcription(self, raw_audio_path):
        try:
            sound = AudioSegment.from_file(raw_audio_path)
            duration = sound.duration_seconds
            
            if sound.frame_rate == 16000 and sound.channels == 1 and raw_audio_path.endswith('.wav'):
                return raw_audio_path, duration

            normalized_sound = sound.set_frame_rate(16000).set_channels(1)
            base, _ = os.path.splitext(raw_audio_path)
            processed_audio_path = f"{base}_processed.wav"
            normalized_sound.export(processed_audio_path, format="wav")
            
            return processed_audio_path, duration
            
        except Exception as e:
            logger.error(f"Error processing audio: {e}")
            return raw_audio_path, 0 

    def is_model_loaded(self, model_name):
        return self.model is not None and self.current_model_name == model_name

    def load_model(self, model_name="medium"):
        if self.is_model_loaded(model_name):
            return True
        
        logger.info(f"Loading the Whisper model ({model_name})...")
        logger.info(f"Model storage path: {self.models_dir}")
        
        try:
            self.model = whisper.load_model(model_name, download_root=self.models_dir)
            self.current_model_name = model_name
            
            if hasattr(self.model, 'dims'):
                actual_dims = self.model.dims
                logger.info(f"   Model loaded successfully: {model_name}")
                logger.info(f"   Actual model dimensions: n_mels={actual_dims.n_mels}, n_vocab={actual_dims.n_vocab}")
            
            logger.info("The model has been loaded successfully.")
            return True
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            raise e

    def transcribe_audio(self, path_to_audio, language=None):
        if self.model is None: 
            return {"error": "The model is not loaded."}
        if not os.path.exists(path_to_audio): 
            return {"error": f"File not found: {path_to_audio}"}

        processed_path, duration = self._process_audio_before_transcription(path_to_audio)
        
        logger.info(f"Starting transcription: {processed_path} (model: {self.current_model_name}, language: {language or 'auto-detect'})")
        
        start_time = time.time()
        try:
            prompts = {              
                "ru": "Привет. Это пример текста, записанного с хорошей пунктуацией. Здесь есть запятые, точки, дефисы — всё как положено. Текст разбит на предложения.",
                "en": "Hello. This is a sample text with proper punctuation. It includes commas, periods, and hyphens. The text is divided into sentences.",
                "de": "Hallo. Dies ist ein Beispieltext mit korrekter Zeichensetzung. Er enthält Kommas, Punkte und Bindestriche.",
                "fr": "Bonjour. Ceci est un exemple de texte avec une ponctuation correcte. Il comprend des virgules, des points et des tirets.",
                "es": "Hola. Este es un texto de ejemplo con puntuación adecuada. Incluye comas, puntos y guiones."
                }
            
            transcribe_options = {
                "task": "transcribe",
                "temperature": 0.2,
                "condition_on_previous_text": True,
                "no_speech_threshold": 0.6,
                "logprob_threshold": -1.0,
                "compression_ratio_threshold": 2.4
            }
            
            if language:
                transcribe_options["language"] = language
                if language in prompts:
                    transcribe_options["initial_prompt"] = prompts[language]

            result = self.model.transcribe(processed_path, **transcribe_options)
            
            final_text = result["text"]
            
            processing_time = time.time() - start_time
            logger.info(f"Transcription completed in {processing_time:.2f} seconds.")

            return {"text": final_text, "duration": duration}
            
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return {"error": str(e)}
        finally:
            if processed_path != path_to_audio and os.path.exists(processed_path):
                try: os.remove(processed_path)
                except OSError as e: logger.warning(f"Failed to delete temporary file {processed_path}: {e}")
