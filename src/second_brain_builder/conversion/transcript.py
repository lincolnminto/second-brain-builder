"""Transcripts: WebVTT output and the Markdown of audio and video Sources.

Times of a ``Segment`` are seconds from the start of the Source.
"""

import bisect
import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from second_brain_builder.domain import Segment

ASSETS_PREFIX = "assets/"

# A paragraph of the Markdown transcript ends at a pause or when it gets long.
_PARAGRAPH_PAUSE_SECONDS = 2.0
_PARAGRAPH_MAX_CHARS = 600
_MIN_CUE_SECONDS = 0.001
_WHITESPACE = re.compile(r"\s+")
_BACKTICK_RUN = re.compile(r"`+")


@dataclass(frozen=True)
class Frame:
    """A video frame saved as an Asset, with the text that OCR found in it."""

    timestamp: float
    image_name: str
    text_lines: list[str] = field(default_factory=list)


def clean_segments(segments: Iterable[Segment]) -> list[Segment]:
    """Sort segments, drop empty ones, and give each one a positive duration."""
    cleaned: list[Segment] = []
    for segment in segments:
        text = _WHITESPACE.sub(" ", segment.text).strip()
        if not text:
            continue
        start = max(0.0, segment.start)
        end = max(segment.end, start + _MIN_CUE_SECONDS)
        cleaned.append(Segment(start=start, end=end, text=text))
    cleaned.sort(key=lambda item: (item.start, item.end))
    return cleaned


def format_clock(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS``."""
    whole = int(max(0.0, seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _vtt_time(seconds: float) -> str:
    millis = round(max(0.0, seconds) * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _vtt_escape(text: str) -> str:
    # Escaping '>' also makes a '-->' in the text harmless.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_vtt(segments: Sequence[Segment]) -> str:
    """Render segments as a WebVTT file."""
    blocks = ["WEBVTT"]
    for segment in clean_segments(segments):
        timing = f"{_vtt_time(segment.start)} --> {_vtt_time(segment.end)}"
        blocks.append(f"{timing}\n{_vtt_escape(segment.text)}")
    return "\n\n".join(blocks) + "\n"


def _paragraphs(segments: Sequence[Segment]) -> list[str]:
    """Group segments into paragraphs, each one led by its start time in bold."""
    paragraphs: list[str] = []
    current: list[Segment] = []

    def flush() -> None:
        if current:
            # Escape HTML like Docling's Markdown export, so the text never reads as markup.
            text = html.escape(" ".join(segment.text for segment in current), quote=False)
            paragraphs.append(f"**{format_clock(current[0].start)}** {text}")
            current.clear()

    for segment in segments:
        if current:
            pause = segment.start - current[-1].end
            length = sum(len(item.text) + 1 for item in current)
            if pause >= _PARAGRAPH_PAUSE_SECONDS or length >= _PARAGRAPH_MAX_CHARS:
                flush()
        current.append(segment)
    flush()
    return paragraphs


def _transcript_link(vtt_name: str) -> str:
    return f"Transcript with timings: [{vtt_name}]({ASSETS_PREFIX}{vtt_name})"


def render_audio_markdown(segments: Sequence[Segment], vtt_name: str) -> str:
    """Markdown for an audio Source: a link to the WebVTT Asset, then the transcript."""
    parts = [_transcript_link(vtt_name), *_paragraphs(clean_segments(segments))]
    return "\n\n".join(parts) + "\n"


def _fenced(lines: Sequence[str]) -> str:
    """A fenced code block that no backtick run in the text can close early."""
    text = "\n".join(lines)
    longest = max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def render_video_markdown(
    segments: Sequence[Segment], frames: Sequence[Frame], vtt_name: str
) -> str:
    """Markdown for a video Source.

    Each frame starts a section whose heading is the frame time. The section holds
    the frame image, the text that OCR found in the frame, and the transcript up to
    the next frame. Transcript before the first frame comes first, without a heading.
    """
    ordered_frames = sorted(frames, key=lambda frame: frame.timestamp)
    transcript = clean_segments(segments)
    parts = [_transcript_link(vtt_name)]

    boundaries = [frame.timestamp for frame in ordered_frames]
    sections: list[list[Segment]] = [[] for _ in range(len(ordered_frames) + 1)]
    for segment in transcript:
        sections[bisect.bisect_right(boundaries, segment.start)].append(segment)

    parts.extend(_paragraphs(sections[0]))
    for frame, section in zip(ordered_frames, sections[1:], strict=True):
        clock = format_clock(frame.timestamp)
        parts.append(f"## {clock}")
        parts.append(f"![Frame at {clock}]({ASSETS_PREFIX}{frame.image_name})")
        if frame.text_lines:
            parts.append("Text in the frame:")
            parts.append(_fenced(frame.text_lines))
        parts.extend(_paragraphs(section))
    return "\n\n".join(parts) + "\n"
