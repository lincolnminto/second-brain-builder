"""File responses for files of a Brain clone: Sources and Assets.

Brain content is untrusted. ``services`` marks a file ``inline`` only for media
types that a browser shows without running content from the file; every other file
is sent as an attachment, and ``nosniff`` (added by the security middleware) stops
the browser from guessing another type.
"""

from __future__ import annotations

from starlette.responses import FileResponse

from second_brain_builder.services import LocalFile

FILE_CACHE_CONTROL = "private, no-cache"


def file_response(file: LocalFile) -> FileResponse:
    return FileResponse(
        file.path,
        media_type=file.media_type,
        filename=file.filename,
        content_disposition_type="inline" if file.inline else "attachment",
        headers={"Cache-Control": FILE_CACHE_CONTROL},
    )


FILE_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "The file. Types that a browser can show safely are inline; "
        "others are attachments.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}
"""OpenAPI responses of a route that sends a file."""
