"""``convert_source``: one Source in, Canonical Document body and Assets out."""

import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from second_brain_builder.conversion import documents, media
from second_brain_builder.conversion.drm import check_drm
from second_brain_builder.conversion.formats import (
    SourceFormat,
    SourceKind,
    detect_media_type,
    source_format,
)
from second_brain_builder.conversion.models import (
    ConversionError,
    ConversionOptions,
    ConversionResult,
    SourceMetadata,
)
from second_brain_builder.conversion.transcript import (
    ASSETS_PREFIX,
    clean_segments,
    render_audio_markdown,
    render_video_markdown,
    render_vtt,
)
from second_brain_builder.domain import Segment

_log = logging.getLogger(__name__)

_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_IMAGE_PLACEHOLDER = "<!-- image -->"
# Docling formats whose pipeline uses layout and OCR models from Hugging Face.
_MODEL_FORMATS = {"pdf", "image"}
# Decoded audio that ends this close to the limit, and clearly after the duration in
# the file header, was cut at the limit. ffmpeg cuts compressed audio near, not at,
# the limit (encoder delay).
_CUT_MARGIN_SECONDS = 1.0
_SECONDS_PER_HOUR = 3600


@dataclass
class _Converted:
    markdown: str
    metadata: SourceMetadata
    tools: list[str]
    warnings: list[str] = field(default_factory=list)


def convert_source(source: Path, assets_dir: Path, options: ConversionOptions) -> ConversionResult:
    """Convert one Source into its Canonical Document body and Assets.

    The markdown links images as ``assets/<name>``. Audio and video Sources also get
    a WebVTT transcript, ``assets/<stem>.vtt``, where ``<stem>`` is the Source file
    name without its extension (so call this with the Source at ``<slug>.<ext>``).
    Assets are written to ``assets_dir`` only after the conversion succeeds; files
    already in ``assets_dir`` with other names are kept. Audio and video longer than
    ``options.max_media_seconds`` are refused, and audio past that point is never
    decoded, whatever the file header says.

    Raises:
        UnsupportedFormatError: Conversion cannot read the media type.
        DrmProtectedError: The Source has DRM or needs a password.
        ConversionError: Any other failure.
    """
    if not source.is_file():
        raise ConversionError(f"The Source file {source.name} does not exist.")
    fmt = source_format(detect_media_type(source))
    check_drm(source, fmt)
    _log.info("Converting %s as %s", source.name, fmt.media_type)

    with tempfile.TemporaryDirectory(prefix="sbb-conversion-") as tmp:
        work_dir = Path(tmp)
        staged = _stage(source, fmt, work_dir)
        assets_out = work_dir / "assets"
        if fmt.kind is SourceKind.DOCUMENT:
            converted = _convert_document(staged, fmt, assets_out, options)
        elif fmt.kind is SourceKind.KINDLE:
            converted = _convert_kindle(staged, assets_out, work_dir, options)
        else:
            vtt_name = f"{_safe_stem(source.stem)}.vtt"
            converted = _convert_media(staged, fmt, assets_out, work_dir, options, vtt_name)
        markdown = _normalize(converted.markdown)
        assets = _publish_assets(markdown, assets_out, assets_dir)

    warnings = list(converted.warnings)
    if not markdown.strip():
        warnings.append("The conversion produced no text.")
    return ConversionResult(
        markdown=markdown,
        assets=assets,
        metadata=converted.metadata,
        converter=", ".join(dict.fromkeys(converted.tools)),
        warnings=warnings,
    )


def _stage(source: Path, fmt: SourceFormat, work_dir: Path) -> Path:
    """Give the Source the extension of its detected format.

    Docling, Calibre, and the frame sampler choose their reader from the extension,
    and an upload can have any name. A symbolic link avoids a copy.
    """
    staged = (work_dir / "input" / "source").with_suffix(fmt.staged_suffix)
    staged.parent.mkdir()
    try:
        staged.symlink_to(source.resolve())
    except OSError:
        shutil.copyfile(source, staged)
    return staged


def _use_models_dir(models_dir: Path) -> None:
    """Point model downloads at ``models_dir``.

    Hugging Face reads HF_HOME when it is first imported, so this works only if no
    earlier code in the process imported it. The worker image sets HF_HOME to the
    same directory, and an HF_HOME that is already set is kept.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(models_dir.resolve()))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _docling_label() -> str:
    return f"docling {documents.package_version('docling')}"


def _convert_document(
    staged: Path, fmt: SourceFormat, assets_out: Path, options: ConversionOptions
) -> _Converted:
    documents.require_docling()
    if fmt.docling_format in _MODEL_FORMATS:
        _use_models_dir(options.models_dir)
    found = documents.read_document_metadata(staged, fmt)
    output = documents.convert_document(staged, fmt, assets_out)
    warnings = list(output.warnings)
    placeholders = output.markdown.count(_IMAGE_PLACEHOLDER)
    if placeholders:
        warnings.append(
            f"{placeholders} image(s) could not be read from the Source and are left as "
            f"'{_IMAGE_PLACEHOLDER}' placeholders."
        )
    return _Converted(
        markdown=output.markdown,
        metadata=documents.merge_metadata(found, output),
        tools=[_docling_label()],
        warnings=warnings,
    )


def _convert_kindle(
    staged: Path, assets_out: Path, work_dir: Path, options: ConversionOptions
) -> _Converted:
    documents.require_docling()
    epub, calibre_label = documents.kindle_to_epub(staged, work_dir)
    converted = _convert_document(epub, source_format(documents.EPUB), assets_out, options)
    converted.tools.insert(0, calibre_label)
    return converted


def _convert_media(
    staged: Path,
    fmt: SourceFormat,
    assets_out: Path,
    work_dir: Path,
    options: ConversionOptions,
    vtt_name: str,
) -> _Converted:
    media.require_ffmpeg()
    probed = media.probe(staged, fmt)
    duration = probed.metadata.duration_seconds
    limit = options.max_media_seconds
    if duration is not None and duration > limit:
        raise ConversionError(
            f"The Source lasts {_duration_text(duration)}. Conversion accepts audio and "
            f"video of at most {_duration_text(limit)}."
        )
    tools: list[str] = []
    warnings: list[str] = []
    segments: list[Segment] = []

    if probed.has_audio:
        wav = work_dir / "audio.wav"
        decoded = media.extract_audio(staged, fmt, wav, duration, limit)
        if _was_cut(decoded, duration, limit):
            warnings.append(
                f"The audio lasts longer than {_duration_text(limit)}. Only the first "
                f"{_duration_text(limit)} were transcribed."
            )
        if options.transcriber is not None:
            segments = media.transcribe_with_provider(wav, options.transcriber, work_dir)
            tools.append("provider transcriber")
        else:
            documents.require_docling()
            _use_models_dir(options.models_dir)
            segments = media.transcribe_with_whisper(wav, options.asr_model, options.models_dir)
            tools.extend([_docling_label(), f"whisper {options.asr_model}"])
        wav.unlink()
        if not clean_segments(segments):
            warnings.append("Speech recognition found no speech.")
    else:
        warnings.append("The Source has no audio track.")

    assets_out.mkdir(parents=True, exist_ok=True)
    (assets_out / vtt_name).write_text(render_vtt(segments), encoding="utf-8")

    if fmt.kind is SourceKind.AUDIO:
        markdown = render_audio_markdown(segments, vtt_name)
    else:
        documents.require_docling()
        sampled, sample_warnings = media.sample_frames(staged, duration)
        frames, ocr_warnings = media.frames_with_text(sampled, assets_out)
        warnings.extend(sample_warnings + ocr_warnings)
        markdown = render_video_markdown(segments, frames, vtt_name)
        tools.extend([_docling_label(), f"rapidocr {documents.package_version('rapidocr')}"])

    return _Converted(markdown=markdown, metadata=probed.metadata, tools=tools, warnings=warnings)


def _was_cut(decoded: float, duration: float | None, limit: float) -> bool:
    """True if ffmpeg stopped at the limit before the end of the audio."""
    near_limit = decoded >= limit - _CUT_MARGIN_SECONDS
    return near_limit and (duration is None or decoded > duration + _CUT_MARGIN_SECONDS)


def _duration_text(seconds: float) -> str:
    if seconds >= _SECONDS_PER_HOUR:
        return f"{seconds / _SECONDS_PER_HOUR:.1f} hours"
    return f"{seconds:g} seconds"


def _safe_stem(stem: str) -> str:
    """A file name stem that is safe in a Markdown link and is not a dot-file."""
    return _UNSAFE_NAME_CHARS.sub("-", stem).strip(".-") or "transcript"


def _normalize(markdown: str) -> str:
    text = markdown.strip()
    return f"{text}\n" if text else ""


def _publish_assets(markdown: str, staged_dir: Path, assets_dir: Path) -> list[Path]:
    """Move the Assets that the markdown references into ``assets_dir``.

    Files that the markdown does not reference (for example images of page
    furniture that Docling saved) are dropped.
    """
    if not staged_dir.is_dir():
        return []
    published: list[Path] = []
    for file in sorted(staged_dir.iterdir()):
        if not file.is_file() or f"({ASSETS_PREFIX}{file.name})" not in markdown:
            continue
        assets_dir.mkdir(parents=True, exist_ok=True)
        target = assets_dir / file.name
        shutil.move(str(file), str(target))
        published.append(target)
    return published
