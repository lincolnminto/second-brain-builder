"""Files of a Brain clone that the API sends to a client."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from second_brain_builder.services.errors import NotFoundError
from second_brain_builder.services.models import LocalFile

OCTET_STREAM = "application/octet-stream"

INLINE_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "text/plain",
        "text/vtt",
        "text/markdown",
        "text/csv",
        "audio/mpeg",
        "audio/wav",
        "audio/ogg",
        "audio/mp4",
        "video/mp4",
        "video/webm",
    }
)
"""Media types that a browser shows without running content from the file.

HTML, SVG, and XML can run scripts in the app's origin, so they are attachments.
"""

_LFS_POINTER_START = b"version https://git-lfs.github.com/spec/v1\n"
_LFS_POINTER_MAX_BYTES = 1024


def is_lfs_pointer(path: Path) -> bool:
    """True when the file is a Git LFS pointer, not the content it points to."""
    try:
        if path.stat().st_size > _LFS_POINTER_MAX_BYTES:
            return False
        with path.open("rb") as file:
            return file.read(len(_LFS_POINTER_START)) == _LFS_POINTER_START
    except OSError:
        return False


def guess_media_type(path: Path) -> str:
    if path.suffix.lower() == ".vtt":
        return "text/vtt"
    media_type, _ = mimetypes.guess_type(path.name, strict=False)
    return media_type or OCTET_STREAM


def local_file(path: Path, filename: str, media_type: str) -> LocalFile:
    """Describe a regular file of a clone for the API.

    Raises:
        NotFoundError: The file is missing, not a regular file, or a Git LFS
            pointer whose content is not downloaded.
    """
    if not path.is_file():
        raise NotFoundError(f"The file {filename!r} does not exist.")
    if is_lfs_pointer(path):
        raise NotFoundError(
            f"The content of {filename!r} is in Git LFS and is not downloaded on this server."
        )
    return LocalFile(
        path=path,
        filename=filename,
        media_type=media_type,
        size_bytes=path.stat().st_size,
        inline=media_type in INLINE_MEDIA_TYPES,
    )
