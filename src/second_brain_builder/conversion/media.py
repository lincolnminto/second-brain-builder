"""Audio and video: probing, speech recognition, scene frames, and OCR.

ffmpeg and ffprobe always run with the demuxer forced from the detected media type
and with only the ``file`` protocol allowed, so a playlist disguised as a media
file cannot make them read other files. Docling and RapidOCR are imported only
inside functions.
"""

import json
import logging
import math
import shutil
import subprocess
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from second_brain_builder.conversion.formats import SourceFormat
from second_brain_builder.conversion.models import ConversionError, SourceMetadata
from second_brain_builder.conversion.transcript import Frame
from second_brain_builder.domain import Segment, Transcriber

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter
    from PIL.Image import Image
    from rapidocr import RapidOCR

_log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# Provider transcription APIs limit the upload size (25 MB for OpenAI). Ten minutes
# of 16 kHz mono 16-bit WAV is 19.2 MB.
PROVIDER_CHUNK_SECONDS = 600
_PROBE_TIMEOUT_SECONDS = 120
_MIN_DECODE_TIMEOUT_SECONDS = 600
# subprocess cannot wait much longer than 24 days (OverflowError), so a timeout that
# comes from a duration in the file is always clamped.
_MAX_DECODE_TIMEOUT_SECONDS = 2 * 24 * 3600
_ERROR_TAIL_CHARS = 600
# Scene sampling settings of Docling's video pipeline for scene changes.
_MAX_FRAMES = 200
_MIN_SCENE_SECONDS = 2.0
_PROBE_FPS = 1.0
_SMOOTH_WINDOW = 2
_AUTHOR_TAGS = ("artist", "album_artist", "author", "composer")


@dataclass(frozen=True)
class MediaProbe:
    """What ffprobe reports about an audio or video Source."""

    metadata: SourceMetadata
    has_audio: bool


def require_ffmpeg() -> None:
    """Raise ``ConversionError`` if ffmpeg or ffprobe is missing."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise ConversionError(
            f"{' and '.join(missing)} not found. Audio and video conversion needs ffmpeg."
        )


def _input_args(path: Path, fmt: SourceFormat) -> list[str]:
    if fmt.ffmpeg_demuxer is None:
        raise ConversionError(f"ffmpeg has no demuxer for {fmt.media_type}.")
    return [
        "-protocol_whitelist",
        "file",
        "-f",
        fmt.ffmpeg_demuxer,
        "-i",
        f"file:{path.resolve()}",
    ]


def _run(argv: list[str], what: str, timeout: float) -> str:
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ConversionError(f"{what} did not finish in time.") from error
    if completed.returncode != 0:
        tail = completed.stderr.strip()[-_ERROR_TAIL_CHARS:] or "no output"
        raise ConversionError(f"{what} failed: {tail}")
    return completed.stdout


def probe(path: Path, fmt: SourceFormat) -> MediaProbe:
    """Duration, title, author, and whether there is an audio stream."""
    output = _run(
        [
            "ffprobe",
            "-v",
            "error",
            *_input_args(path, fmt),
            "-show_entries",
            "format=duration:format_tags:stream=codec_type",
            "-of",
            "json",
        ],
        "ffprobe",
        _PROBE_TIMEOUT_SECONDS,
    )
    try:
        data = json.loads(output)
    except json.JSONDecodeError as error:
        raise ConversionError("ffprobe returned output that is not JSON.") from error
    section = data.get("format") or {}
    tags = {str(key).lower(): str(value) for key, value in (section.get("tags") or {}).items()}
    try:
        duration: float | None = float(section["duration"])
    except (KeyError, TypeError, ValueError):
        duration = None
    if duration is not None and not (math.isfinite(duration) and duration >= 0):
        duration = None
    author = next((tags[key].strip() for key in _AUTHOR_TAGS if tags.get(key, "").strip()), None)
    streams = data.get("streams") or []
    return MediaProbe(
        metadata=SourceMetadata(
            title=tags.get("title", "").strip() or None,
            author=author,
            duration_seconds=duration,
        ),
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
    )


def extract_audio(
    path: Path, fmt: SourceFormat, wav: Path, duration: float | None, max_seconds: float
) -> float:
    """Decode at most ``max_seconds`` of the audio track to 16 kHz mono 16-bit WAV.

    ``duration`` comes from the file header, which can be wrong, so ffmpeg stops at
    ``max_seconds`` whatever the header says. Return the seconds of audio decoded.
    """
    expected = min(duration if duration is not None else max_seconds, max_seconds)
    timeout = min(max(_MIN_DECODE_TIMEOUT_SECONDS, 2 * expected), _MAX_DECODE_TIMEOUT_SECONDS)
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            *_input_args(path, fmt),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-t",
            f"{max_seconds:.3f}",
            "-f",
            "wav",
            "-y",
            f"file:{wav.resolve()}",
        ],
        "ffmpeg audio extraction",
        timeout,
    )
    try:
        with wave.open(str(wav), "rb") as reader:
            return reader.getnframes() / reader.getframerate()
    except (OSError, EOFError, wave.Error) as error:
        raise ConversionError(f"ffmpeg wrote audio that cannot be read: {error}") from error


@lru_cache(maxsize=2)
def _asr_converter(asr_model: str, whisper_dir: Path) -> "DocumentConverter":
    """Docling converter for audio that runs local Whisper. It keeps the model loaded."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import AsrPipelineOptions
    from docling.datamodel.pipeline_options_asr_model import InlineAsrNativeWhisperOptions
    from docling.document_converter import AudioFormatOption, DocumentConverter

    asr_options = InlineAsrNativeWhisperOptions(
        repo_id=asr_model,
        verbose=False,
        timestamps=True,
        word_timestamps=False,
        temperature=0.0,
    )
    # With artifacts_path set, Whisper downloads its checkpoint into that directory.
    pipeline = AsrPipelineOptions(asr_options=asr_options, artifacts_path=whisper_dir)
    return DocumentConverter(
        allowed_formats=[InputFormat.AUDIO],
        format_options={InputFormat.AUDIO: AudioFormatOption(pipeline_options=pipeline)},
    )


def transcribe_with_whisper(wav: Path, asr_model: str, models_dir: Path) -> list[Segment]:
    """Local speech recognition through Docling's ASR pipeline."""
    from docling_core.types.doc.common.source import TrackSource

    whisper_dir = models_dir / "whisper"
    whisper_dir.mkdir(parents=True, exist_ok=True)
    converter = _asr_converter(asr_model, whisper_dir.resolve())
    try:
        result = converter.convert(wav, raises_on_error=True)
    except Exception as error:  # Docling and Whisper raise many error types.
        raise ConversionError(f"Speech recognition failed: {error}") from error
    segments: list[Segment] = []
    for item in result.document.texts:
        for source in item.source:
            if isinstance(source, TrackSource):
                segments.append(
                    Segment(start=source.start_time, end=source.end_time, text=item.text)
                )
                break
    return segments


def _wav_chunks(wav: Path, work_dir: Path, seconds: int) -> Iterator[tuple[Path, float]]:
    """Split a WAV file into chunks. Yield each chunk with its start time."""
    with wave.open(str(wav), "rb") as reader:
        params = reader.getparams()
        frames_per_chunk = reader.getframerate() * seconds
        index = 0
        while data := reader.readframes(frames_per_chunk):
            chunk = work_dir / f"chunk-{index:04d}.wav"
            with wave.open(str(chunk), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(data)
            yield chunk, float(index * seconds)
            chunk.unlink()
            index += 1


def transcribe_with_provider(
    wav: Path, transcriber: Transcriber, work_dir: Path, chunk_seconds: int = PROVIDER_CHUNK_SECONDS
) -> list[Segment]:
    """Speech recognition through a Provider, one chunk of audio at a time."""
    chunk_dir = work_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Segment] = []
    for chunk, offset in _wav_chunks(wav, chunk_dir, chunk_seconds):
        try:
            returned = transcriber.transcribe(chunk, None)
        except Exception as error:  # Provider clients raise their own error types.
            raise ConversionError(f"The transcription Provider failed: {error}") from error
        segments.extend(
            Segment(start=item.start + offset, end=item.end + offset, text=item.text)
            for item in returned
        )
    return segments


def sample_frames(
    video: Path, duration: float | None
) -> tuple[list[tuple[float, "Image"]], list[str]]:
    """One representative frame per scene, with Docling's scene-change sampler.

    ``video`` must have the extension of its container: the sampler forces the
    ffmpeg demuxer from it.
    """
    from docling.utils.video_frame_sampling import FfmpegRunner, SimpleSceneChangeFrameSampler

    runner = FfmpegRunner()
    if duration:
        runner.scale_to_duration(duration)
    sampler = SimpleSceneChangeFrameSampler(
        probe_fps=_PROBE_FPS,
        min_scene_duration_seconds=_MIN_SCENE_SECONDS,
        max_frames=_MAX_FRAMES,
        smooth_window=_SMOOTH_WINDOW,
        runner=runner,
    )
    try:
        frames = sampler.sample(video)
    except Exception as error:  # Like Docling's video pipeline, go on without frames.
        _log.warning("Frame sampling failed: %s", error)
        return [], [f"Frame sampling failed, so the document has no frames: {error}"]
    warnings = [f"Frame sampling was cut short: {message}" for message in runner.timeouts]
    return [(frame.timestamp, frame.image) for frame in frames], warnings


@lru_cache(maxsize=1)
def _ocr_engine() -> "RapidOCR":
    """RapidOCR with the models that ship in its package: nothing is downloaded."""
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning"})


def ocr_lines(image: "Image") -> list[str]:
    """Lines of text that OCR finds in an image, top to bottom."""
    from rapidocr.utils.output import RapidOCROutput

    output = _ocr_engine()(image)
    if not isinstance(output, RapidOCROutput) or output.txts is None:
        return []
    return [line.strip() for line in output.txts if line.strip()]


def frames_with_text(
    sampled: list[tuple[float, "Image"]], assets_out: Path
) -> tuple[list[Frame], list[str]]:
    """Save sampled frames as PNG Assets and read their text with OCR."""
    assets_out.mkdir(parents=True, exist_ok=True)
    frames: list[Frame] = []
    warnings: list[str] = []
    for index, (timestamp, image) in enumerate(sampled, start=1):
        name = f"frame-{index:04d}.png"
        image.save(assets_out / name, format="PNG")
        try:
            lines = ocr_lines(image)
        except Exception as error:  # OCR failure on one frame must not stop the Source.
            _log.warning("OCR failed for the frame at %.1f s: %s", timestamp, error)
            warnings.append(f"OCR failed for the frame at {timestamp:.1f} s.")
            lines = []
        frames.append(Frame(timestamp=timestamp, image_name=name, text_lines=lines))
    return frames, warnings
