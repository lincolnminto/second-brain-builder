"""Staged uploads in ``<data_dir>/uploads/<uuid>/``.

An upload is streamed to disk with its SHA-256 computed on the way, so the file is
read once and never held in memory. The ``source.add`` job converts the staged
file. Services remove a staged upload when its job is cancelled or its Brain is
disconnected.
"""

from __future__ import annotations

import binascii
import hashlib
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from pydantic import ValidationError

from second_brain_builder.domain import JobKind, JobStatus
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import InvalidInputError, UploadTooLargeError
from second_brain_builder.services.payloads import SourceAddPayload

_log = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
STAGED_STEM = "upload"
MAX_FILENAME_BYTES = 255
_EXTENSION_RE = re.compile(r"[a-z0-9]{1,16}")

BASE64_PIECE_CHARS = 256 * 1024
"""Characters of base64 text that ``Base64Stream`` decodes at a time."""
BASE64_WHITESPACE = " \t\n\r\v\f"
"""Characters that base64 text can have between its groups, for example MIME line breaks."""
_BASE64_WHITESPACE_BYTES = BASE64_WHITESPACE.encode("ascii")
_BASE64_ALPHABET_RE = re.compile(rb"[A-Za-z0-9+/]*")


class ByteStream(Protocol):
    """A binary file object: ``io.BytesIO``, an open file, or a FastAPI ``UploadFile.file``."""

    def read(self, size: int = -1, /) -> bytes: ...


class Base64Stream:
    """A ``ByteStream`` that decodes base64 text one piece at a time.

    ``stage_upload`` reads it in chunks, so the decoded file is never in memory as
    a whole, and the text is never copied as a whole. ``BASE64_WHITESPACE`` in the
    text is ignored.

    ``read`` raises ``InvalidInputError`` when it gets to text that is not base64:
    a character outside the base64 alphabet, padding before the end, or an
    incomplete last group.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._offset = 0
        self._carry = b""
        """Base64 characters of a group that the next piece completes."""
        self._padded = False
        self._buffer = bytearray()

    def read(self, size: int = -1, /) -> bytes:
        while size < 0 or len(self._buffer) < size:
            piece = self._decode_next()
            if piece is None:
                break
            self._buffer += piece
        if size < 0 or size >= len(self._buffer):
            data = bytes(self._buffer)
            self._buffer.clear()
            return data
        with memoryview(self._buffer) as view:
            data = bytes(view[:size])
        del self._buffer[:size]
        return data

    def _decode_next(self) -> bytes | None:
        """Decode the next piece of the text, or return ``None`` at its end."""
        while self._offset < len(self._text):
            piece = self._text[self._offset : self._offset + BASE64_PIECE_CHARS]
            self._offset += len(piece)
            try:
                raw = piece.encode("ascii")
            except UnicodeEncodeError:
                raise _not_base64() from None
            chars = self._carry + raw.translate(None, _BASE64_WHITESPACE_BYTES)
            whole = len(chars) - len(chars) % 4
            groups, self._carry = chars[:whole], chars[whole:]
            if not groups:
                continue
            body = groups.rstrip(b"=")
            if (
                self._padded
                or len(groups) - len(body) > 2
                or _BASE64_ALPHABET_RE.fullmatch(body) is None
            ):
                raise _not_base64()
            self._padded = len(body) < len(groups)
            return binascii.a2b_base64(groups)
        if self._carry:
            raise _not_base64()
        return None


def base64_min_decoded_size(text: str) -> int:
    """The smallest size that ``text`` can decode to, without a copy of the text.

    Only ``BASE64_WHITESPACE`` is left out of the count, so text with other
    characters (which is not base64) can count as larger than it is.
    """
    chars = len(text) - sum(text.count(char) for char in BASE64_WHITESPACE)
    return max(0, chars // 4 * 3 - 2)


def _not_base64() -> InvalidInputError:
    return InvalidInputError("content_base64 is not valid base64.")


@dataclass(frozen=True)
class StagedUpload:
    directory: Path
    """``<data_dir>/uploads/<uuid>``. Remove it to discard the upload."""
    path: Path
    sha256: str
    size_bytes: int


def clean_filename(filename: str) -> str:
    """The base name of an uploaded file, without directories or control characters.

    Raises:
        InvalidInputError: Nothing is left, or the name is longer than 255 bytes.
    """
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = " ".join("".join(char for char in base if char.isprintable()).split())
    if base in {"", ".", ".."}:
        raise InvalidInputError("The file has no name.")
    if len(base.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise InvalidInputError(f"The file name is longer than {MAX_FILENAME_BYTES} bytes.")
    return base


def stage_upload(ctx: AppContext, filename: str, stream: ByteStream) -> StagedUpload:
    """Copy ``stream`` to a new upload directory and compute its SHA-256 on the way.

    The staged file is ``upload.<ext>``, with the extension of ``filename`` when it
    is a plain one, because Conversion reads the extension when the content has no
    known signature.

    Raises:
        UploadTooLargeError: The stream is longer than ``SBB_MAX_UPLOAD_BYTES``.
        InvalidInputError: The stream is empty.
        OSError: The stream or the disk failed.

    On every error, the upload directory is removed.
    """
    limit = ctx.settings.max_upload_bytes
    extension = PurePosixPath(filename).suffix.lower().removeprefix(".")
    name = f"{STAGED_STEM}.{extension}" if _EXTENSION_RE.fullmatch(extension) else STAGED_STEM
    directory = ctx.settings.uploads_dir / uuid.uuid4().hex
    directory.mkdir(parents=True)
    path = directory / name
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as file:
            while chunk := stream.read(CHUNK_BYTES):
                size += len(chunk)
                if size > limit:
                    raise UploadTooLargeError(
                        f"The file is larger than the upload limit of {limit} bytes.",
                        max_bytes=limit,
                    )
                digest.update(chunk)
                file.write(chunk)
        if size == 0:
            raise InvalidInputError("The file is empty.")
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return StagedUpload(directory=directory, path=path, sha256=digest.hexdigest(), size_bytes=size)


def staged_upload_path(ctx: AppContext, payload: dict[str, Any]) -> Path | None:
    """The staged file of a ``source.add`` payload, if it is a file in the uploads directory."""
    try:
        parsed = SourceAddPayload.model_validate(payload)
    except ValidationError:
        return None
    path = Path(parsed.upload_path)
    uploads = ctx.settings.uploads_dir.resolve()
    if not path.is_absolute() or path.resolve().parent.parent != uploads:
        return None
    return path


def remove_staged_upload(ctx: AppContext, payload: dict[str, Any]) -> None:
    """Remove the upload directory of a ``source.add`` payload. Missing files are fine."""
    path = staged_upload_path(ctx, payload)
    if path is not None:
        shutil.rmtree(path.parent, ignore_errors=True)


def pending_upload_bytes(ctx: AppContext, brain_ids: set[str]) -> int:
    """Bytes of the staged uploads of queued and running ``source.add`` jobs of these Brains.

    These Sources are not committed yet, so the clone's LFS usage does not count them.
    """
    jobs = ctx.jobs.list_jobs(statuses=[JobStatus.QUEUED, JobStatus.RUNNING], limit=10_000)
    total = 0
    for job in jobs:
        if job.kind is not JobKind.SOURCE_ADD or job.brain_id not in brain_ids:
            continue
        path = staged_upload_path(ctx, job.payload)
        try:
            total += path.stat().st_size if path is not None else 0
        except OSError:
            continue  # the worker removed it after the commit
    return total
