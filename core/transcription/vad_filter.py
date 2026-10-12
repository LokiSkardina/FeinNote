# Responsible for: future voice activity detection preprocessing hook.

"""No-op VAD extension point kept out of the current transcription path."""


class VadFilter:
    """Provide a documented no-op placeholder for a future VAD preprocessing step."""

    def process(self, audio_path: str) -> str:
        """Return the original audio path without altering transcription input.

        Args:
            audio_path: Input audio file path.

        Returns:
            The same ``audio_path`` value.
        """
        return audio_path
