"""Public data types and errors of the conversion module."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from second_brain_builder.domain import Transcriber

# Audio and video longer than this are refused: the decoded WAV of one day is 2.8 GB.
DEFAULT_MAX_MEDIA_SECONDS = 24 * 3600


class UnsupportedFormatError(Exception):
    """The Source has a media type that Conversion cannot read."""


class DrmProtectedError(Exception):
    """The Source is protected by DRM or needs a password. Conversion never removes DRM."""


class ConversionError(Exception):
    """Conversion failed for a reason other than the format or DRM."""


class SourceMetadata(BaseModel):
    """Facts about a Source that Conversion found. ``None`` means unknown."""

    title: str | None = None
    author: str | None = None
    pages: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)


class ConversionOptions(BaseModel):
    """Settings for one call of ``convert_source``."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    models_dir: Path = Field(
        description="Model cache. Hugging Face models (HF_HOME) and Whisper models go here."
    )
    asr_model: str = Field(
        default="turbo",
        min_length=1,
        description="Whisper model for local speech recognition, for example turbo or tiny.",
    )
    transcriber: Transcriber | None = Field(
        default=None,
        description="Provider transcriber. If set, it replaces local Whisper. Conversion "
        "calls it with WAV files (16 kHz, mono) of at most ten minutes each.",
    )
    max_media_seconds: float = Field(
        default=DEFAULT_MAX_MEDIA_SECONDS,
        gt=0,
        allow_inf_nan=False,
        description="Longest audio or video Source that Conversion accepts, in seconds. "
        "A longer duration in the file header raises ConversionError, and audio after "
        "this point is never decoded.",
    )


class ConversionResult(BaseModel):
    """The Canonical Document body and the Assets of one Source."""

    markdown: str = Field(description="Canonical Document body. Images link as assets/<name>.")
    assets: list[Path] = Field(
        description="Files written to the assets directory, sorted by name. "
        "The markdown references each one."
    )
    metadata: SourceMetadata
    converter: str = Field(description="Tools and versions, for example 'docling 2.150.1'.")
    warnings: list[str] = Field(default_factory=list)
