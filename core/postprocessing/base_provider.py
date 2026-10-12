# Responsible for: postprocessing provider interface and shared behavior

from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """Contract for text-only post-processing adapters."""

    @abstractmethod
    def process(self, text: str, prompt: str) -> str:
        """Return edited text for source text and an editing instruction."""

    @abstractmethod
    def test_connection(self) -> None:
        """Check connectivity without editing notes or requesting generation."""
