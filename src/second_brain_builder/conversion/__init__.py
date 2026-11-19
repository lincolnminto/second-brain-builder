"""Conversion: turn a Source into a Canonical Document body and Assets.

Importing this package needs no worker dependencies. Docling, RapidOCR, and
PDFium are imported only inside the functions that convert. A Provider
transcriber for ``ConversionOptions.transcriber`` implements
``second_brain_builder.domain.Transcriber``.
"""

from second_brain_builder.conversion.converter import convert_source
from second_brain_builder.conversion.formats import detect_media_type, sha256_file
from second_brain_builder.conversion.models import (
    ConversionError,
    ConversionOptions,
    ConversionResult,
    DrmProtectedError,
    SourceMetadata,
    UnsupportedFormatError,
)

__all__ = [
    "ConversionError",
    "ConversionOptions",
    "ConversionResult",
    "DrmProtectedError",
    "SourceMetadata",
    "UnsupportedFormatError",
    "convert_source",
    "detect_media_type",
    "sha256_file",
]
