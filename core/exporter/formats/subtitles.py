# Responsible for: SRT/VTT rendering using only original transcription segments.

import math
from html import escape

from core.exporter.base_exporter import BaseExporter


UNAVAILABLE = "Subtitle timestamps are unavailable for this note."


def subtitle_cues(note) -> list[tuple[int, int, str]]:
    """Validate original segments and return millisecond cues without inventing timing."""
    segments = note.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError(UNAVAILABLE)
    cues = []
    previous_start = -1
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError(UNAVAILABLE)
        start, end, text = (segment.get(key) for key in ("start", "end", "text"))
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or not isinstance(text, str)):
            raise ValueError(UNAVAILABLE)
        try:
            start_ms, end_ms = int(start * 1000 + 0.5), int(end * 1000 + 0.5)
        except OverflowError as error:
            raise ValueError(UNAVAILABLE) from error
        if start_ms < previous_start or end_ms <= start_ms:
            raise ValueError(UNAVAILABLE)
        previous_start = start_ms
        # Blank lines delimit cues in both formats, so omit empty interior lines.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            cues.append((start_ms, end_ms, escape("\n".join(lines), quote=False)))
    if not cues:
        raise ValueError(UNAVAILABLE)
    return cues


def timestamp(milliseconds: int, separator: str) -> str:
    """Format nonnegative milliseconds as a standard SRT or VTT timestamp."""
    seconds, fraction = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{fraction:03d}"


class SrtExporter(BaseExporter):
    """Render numbered SRT cues from the original recognizer's text and timings."""

    def render(self, note) -> bytes:
        """Return UTF-8 SRT, rejecting notes without usable real segments."""
        blocks = [f"{index}\n{timestamp(start, ',')} --> {timestamp(end, ',')}\n{text}\n\n"
                  for index, (start, end, text) in enumerate(subtitle_cues(note), 1)]
        return "".join(blocks).encode("utf-8")


class VttExporter(BaseExporter):
    """Render WebVTT cues from the original recognizer's text and timings."""

    def render(self, note) -> bytes:
        """Return UTF-8 WebVTT with its required header and dot milliseconds."""
        blocks = [f"{timestamp(start, '.')} --> {timestamp(end, '.')}\n{text}\n\n"
                  for start, end, text in subtitle_cues(note)]
        return ("WEBVTT\n\n" + "".join(blocks)).encode("utf-8")
