"""Streaming multipart uploads: the request body goes to ``services.add_source`` in chunks.

``services.add_source`` reads a blocking ``ByteStream`` and writes it to the staging
directory while it computes the SHA-256, so it runs in a worker thread. The worker
thread parses the multipart body with ``python_multipart.MultipartParser`` and gets
each body chunk from the event loop (``anyio.from_thread.run``), when it needs it.
The file is never held in memory, and reading stops at ``SBB_MAX_UPLOAD_BYTES``:
``add_source`` raises ``UploadTooLargeError``, and the rest of the body is not read.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field

import anyio
import anyio.from_thread
import anyio.to_thread
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.requests import Request

from second_brain_builder import services
from second_brain_builder.api.errors import ApiError
from second_brain_builder.services import (
    AddSourceResult,
    AppContext,
    InvalidInputError,
    UploadTooLargeError,
)

FILE_FIELD = "file"
"""The name of the multipart part that holds the file."""

MULTIPART_OVERHEAD_BYTES = 1024 * 1024
"""Body bytes allowed on top of ``SBB_MAX_UPLOAD_BYTES``: part headers and boundaries."""

UPLOAD_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {
                        FILE_FIELD: {
                            "type": "string",
                            "format": "binary",
                            "description": "The Source file. Its filename is the original "
                            "filename of the Source.",
                        }
                    },
                }
            }
        },
    }
}
"""OpenAPI ``requestBody`` of the upload route, which reads the body itself."""


async def receive_upload(
    request: Request,
    ctx: AppContext,
    brain_id: str,
    folder: str,
    *,
    confirm_over_quota: bool,
) -> AddSourceResult:
    """Stream the ``file`` part of a multipart request into ``services.add_source``.

    Raises:
        ApiError: The body is not ``multipart/form-data`` (415).
        UploadTooLargeError: The body is longer than the upload limit.
        InvalidInputError: The body is not valid multipart, has no ``file`` part with a
            filename, or ends early.
        ServiceError: Every error of ``services.add_source``.
    """
    boundary = _boundary(request.headers.get("content-type"))
    max_body_bytes = ctx.settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > max_body_bytes:
        raise _too_large(ctx)

    body = request.stream()

    async def next_chunk() -> bytes:
        try:
            return await body.__anext__()
        except StopAsyncIteration:
            return b""

    def run() -> AddSourceResult:
        reader = MultipartFileReader(
            boundary,
            lambda: anyio.from_thread.run(next_chunk),
            max_body_bytes=max_body_bytes,
            too_large=lambda: _too_large(ctx),
        )
        filename = reader.open_file(FILE_FIELD)
        return services.add_source(
            ctx, brain_id, filename, reader, folder, confirm_over_quota=confirm_over_quota
        )

    try:
        return await anyio.to_thread.run_sync(run)
    finally:
        await _close(body)


# --- the multipart reader ------------------------------------------------------------------


@dataclass(frozen=True)
class _PartStart:
    headers: dict[str, str]


@dataclass(frozen=True)
class _PartData:
    data: bytes


@dataclass(frozen=True)
class _PartEnd:
    pass


@dataclass(frozen=True)
class _BodyEnd:
    pass


_Event = _PartStart | _PartData | _PartEnd | _BodyEnd


@dataclass
class _HeaderState:
    name: bytearray = field(default_factory=bytearray)
    value: bytearray = field(default_factory=bytearray)
    headers: dict[str, str] = field(default_factory=dict)


class MultipartFileReader:
    """A blocking reader of one file part of a ``multipart/form-data`` body.

    ``next_chunk`` returns the next body chunk, or ``b""`` at the end of the body.
    ``open_file`` skips to the file part and returns its filename; then ``read``
    returns the part's bytes, so the reader is a ``services.ByteStream``. Only one
    body chunk is held at a time.
    """

    def __init__(
        self,
        boundary: bytes,
        next_chunk: Callable[[], bytes],
        *,
        max_body_bytes: int,
        too_large: Callable[[], Exception],
    ) -> None:
        self._next_chunk = next_chunk
        self._max_body_bytes = max_body_bytes
        self._too_large = too_large
        self._received = 0
        self._body_done = False
        self._events: deque[_Event] = deque()
        self._header = _HeaderState()
        self._in_file = False
        self._buffer = b""
        self._parser = MultipartParser(
            boundary,
            callbacks={
                "on_part_begin": self._on_part_begin,
                "on_header_field": self._on_header_field,
                "on_header_value": self._on_header_value,
                "on_header_end": self._on_header_end,
                "on_headers_finished": self._on_headers_finished,
                "on_part_data": self._on_part_data,
                "on_part_end": self._on_part_end,
                "on_end": self._on_end,
            },
        )

    def open_file(self, name: str) -> str:
        """Skip to the part ``name`` and return its filename.

        Raises:
            InvalidInputError: There is no such part, or it has no filename.
        """
        while True:
            event = self._pull()
            if isinstance(event, _BodyEnd):
                raise InvalidInputError(f"The upload has no part named {name!r}.")
            if not isinstance(event, _PartStart):
                continue  # data or end of a part that is skipped
            part_name, filename = _disposition(event.headers)
            if part_name != name:
                continue
            if not filename:
                raise InvalidInputError(f"The part {name!r} has no filename.")
            self._in_file = True
            return filename

    def read(self, size: int = -1, /) -> bytes:
        """Up to ``size`` bytes of the file part (all of it when ``size`` < 0); b"" at its end."""
        if size < 0:
            chunks = []
            while chunk := self.read(1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        while not self._buffer and self._in_file:
            event = self._pull()
            if isinstance(event, _PartData):
                self._buffer = event.data
            elif isinstance(event, _PartEnd):
                self._in_file = False
                # Validate the remaining multipart envelope before services enqueue
                # the Source. A part boundary alone does not close the request body.
                while not isinstance(self._pull(), _BodyEnd):
                    pass
            else:
                raise InvalidInputError("The multipart body is not valid.")
        chunk, self._buffer = self._buffer[:size], self._buffer[size:]
        return chunk

    def _pull(self) -> _Event:
        while not self._events:
            if self._body_done:
                raise InvalidInputError("The upload ended before the end of the multipart body.")
            chunk = self._next_chunk()
            if not chunk:
                self._body_done = True
                continue
            self._received += len(chunk)
            if self._received > self._max_body_bytes:
                raise self._too_large()
            try:
                self._parser.write(chunk)
            except MultipartParseError:
                raise InvalidInputError("The multipart body is not valid.") from None
        return self._events.popleft()

    # Parser callbacks. ``data[start:end]`` copies the bytes: the parser reuses its input.

    def _on_part_begin(self) -> None:
        self._header = _HeaderState()

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header.name += data[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header.value += data[start:end]

    def _on_header_end(self) -> None:
        name = self._header.name.decode("latin-1").strip().lower()
        self._header.headers[name] = self._header.value.decode("latin-1")
        self._header.name = bytearray()
        self._header.value = bytearray()

    def _on_headers_finished(self) -> None:
        self._events.append(_PartStart(headers=self._header.headers))

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        if end > start:
            self._events.append(_PartData(data=data[start:end]))

    def _on_part_end(self) -> None:
        self._events.append(_PartEnd())

    def _on_end(self) -> None:
        self._events.append(_BodyEnd())


# --- helpers ----------------------------------------------------------------------------


def _boundary(content_type: str | None) -> bytes:
    media_type, options = parse_options_header(content_type)
    if media_type != b"multipart/form-data":
        raise ApiError(
            415,
            "unsupported_media_type",
            f"Send the file as multipart/form-data, in a part named {FILE_FIELD!r}.",
        )
    boundary = options.get(b"boundary")
    if not boundary:
        raise InvalidInputError("The multipart body has no boundary.")
    return boundary


def _disposition(headers: dict[str, str]) -> tuple[str | None, str | None]:
    """The ``name`` and ``filename`` of a part's ``Content-Disposition`` header."""
    _, options = parse_options_header(headers.get("content-disposition"))
    # Header bytes arrive as latin-1; browsers send names in UTF-8.
    name = options.get(b"name")
    filename = options.get(b"filename")
    return (
        None if name is None else name.decode("utf-8", errors="replace"),
        None if filename is None else filename.decode("utf-8", errors="replace"),
    )


def _too_large(ctx: AppContext) -> UploadTooLargeError:
    limit = ctx.settings.max_upload_bytes
    return UploadTooLargeError(
        f"The file is larger than the upload limit of {limit} bytes.", max_bytes=limit
    )


async def _close(body: AsyncGenerator[bytes, None]) -> None:
    with anyio.CancelScope(shield=True):
        await body.aclose()
