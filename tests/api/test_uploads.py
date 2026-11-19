"""Uploads: the multipart body is streamed into the staging directory, with limits and checks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from second_brain_builder.api import create_app
from second_brain_builder.services import list_jobs

from .conftest import WEB, call_asgi, staged_uploads

if TYPE_CHECKING:
    from second_brain_builder.services import AppContext

    from .conftest import BrainFixture, MakeClient, MakeContext

NOTES = b"Field notes from a synthetic salt marsh.\nHerons wade at dawn.\n"
BOUNDARY = "sbb-test-boundary-7MA4YWxkTrZu0gW"


def multipart(filename: str, content: bytes, *, close: bool = True) -> bytes:
    """A ``multipart/form-data`` body with a text field before the ``file`` part."""
    body = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="note"\r\n\r\n'
        "a text field that the API skips\r\n"
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content
    if close:
        body += f"\r\n--{BOUNDARY}--\r\n".encode()
    return body


def upload_headers() -> dict[str, str]:
    return {**WEB, "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


def test_upload_is_staged_and_enqueued(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    response = client.post(
        f"/api/brains/{brain.id}/sources",
        params={"folder": "Field Work"},
        files={"file": ("Marsh Notes.txt", NOTES, "text/plain")},
        headers=WEB,
    )

    assert response.status_code == 202, response.text
    result = response.json()
    payload = result["job"]["payload"]
    assert result["job"]["kind"] == "source.add"
    assert (result["sha256"], result["size_bytes"], result["media_type"]) == (
        hashlib.sha256(NOTES).hexdigest(),
        len(NOTES),
        "text/plain",
    )
    assert (payload["original_filename"], payload["folder"]) == ("Marsh Notes.txt", "field-work")
    assert Path(payload["upload_path"]).read_bytes() == NOTES


def test_utf8_filename_and_skipped_fields_are_read_from_the_raw_body(
    client: TestClient, brain: BrainFixture
) -> None:
    filename = "Notas de maré 鷺.txt"
    response = client.post(
        f"/api/brains/{brain.id}/sources",
        content=multipart(filename, NOTES),
        headers=upload_headers(),
    )

    assert response.status_code == 202, response.text
    assert response.json()["job"]["payload"]["original_filename"] == filename
    assert Path(response.json()["job"]["payload"]["upload_path"]).read_bytes() == NOTES


def test_duplicate_upload_is_409_with_the_existing_source(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    content = (brain.root / "sources" / "nature" / "tide-pools" / "tide-pools.txt").read_bytes()

    response = client.post(
        f"/api/brains/{brain.id}/sources",
        files={"file": ("copy of tide pools.txt", content, "text/plain")},
        headers=WEB,
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "duplicate_source"
    assert body["existing_source"]["slug"] == "tide-pools"
    assert body["existing_source"]["title"] == "Tide Pools of the Example Coast"
    assert "pending_job" not in body
    assert staged_uploads(ctx) == []
    assert list_jobs(ctx, brain.id) == []


def test_second_upload_while_the_first_is_queued_is_409_with_the_pending_job(
    client: TestClient, brain: BrainFixture
) -> None:
    first = client.post(
        f"/api/brains/{brain.id}/sources", files={"file": ("a.txt", NOTES)}, headers=WEB
    )
    second = client.post(
        f"/api/brains/{brain.id}/sources", files={"file": ("b.txt", NOTES)}, headers=WEB
    )

    assert second.status_code == 409
    assert second.json()["pending_job"]["id"] == first.json()["job"]["id"]
    assert "existing_source" not in second.json()


def test_upload_over_the_limit_stops_reading_the_stream(
    make_ctx: MakeContext, brain: BrainFixture
) -> None:
    limited = make_ctx(max_upload_bytes=256 * 1024)
    app = create_app(context=limited)
    chunk = b"x" * (64 * 1024)
    total_chunks = 64  # 4 MiB, sent without a Content-Length
    head, tail = multipart("big.txt", b"", close=False), f"\r\n--{BOUNDARY}--\r\n".encode()

    response = call_asgi(
        app,
        "POST",
        f"/api/brains/{brain.id}/sources",
        upload_headers(),
        [head, *[chunk] * total_chunks, tail],
    )

    assert response.status == 413, response.body
    assert b'"code":"upload_too_large"' in response.body
    assert b'"max_bytes":262144' in response.body
    assert response.chunks_read <= 1 + 256 // 64 + 1  # stopped at the limit, not at the end
    assert staged_uploads(limited) == []
    assert list_jobs(limited, brain.id) == []


def test_content_length_over_the_limit_is_413_before_the_body_is_read(
    make_ctx: MakeContext, brain: BrainFixture
) -> None:
    limited = make_ctx(max_upload_bytes=1024)
    app = create_app(context=limited)
    too_long = str(1024 + 2 * 1024 * 1024)

    response = call_asgi(
        app,
        "POST",
        f"/api/brains/{brain.id}/sources",
        {**upload_headers(), "Content-Length": too_long},
        [multipart("big.txt", NOTES)],
    )

    assert response.status == 413
    assert response.chunks_read == 0


def test_body_that_ends_inside_the_file_is_rejected_and_not_staged(
    ctx: AppContext, brain: BrainFixture
) -> None:
    app = create_app(context=ctx)

    response = call_asgi(
        app,
        "POST",
        f"/api/brains/{brain.id}/sources",
        upload_headers(),
        [multipart("notes.txt", NOTES, close=False)],
    )

    assert response.status == 422
    assert b'"code":"invalid_input"' in response.body
    assert staged_uploads(ctx) == []
    assert list_jobs(ctx, brain.id) == []


def test_body_without_the_final_boundary_is_rejected(ctx: AppContext, brain: BrainFixture) -> None:
    body = multipart("notes.txt", NOTES, close=False)
    body += (
        f'\r\n--{BOUNDARY}\r\nContent-Disposition: form-data; name="note"\r\n\r\n'
        "unfinished trailing field"
    ).encode()

    response = call_asgi(
        create_app(context=ctx),
        "POST",
        f"/api/brains/{brain.id}/sources",
        upload_headers(),
        [body],
    )

    assert response.status == 422
    assert staged_uploads(ctx) == []
    assert list_jobs(ctx, brain.id) == []


def test_multipart_boundaries_and_headers_can_span_stream_chunks(
    ctx: AppContext, brain: BrainFixture
) -> None:
    body = multipart("notes.txt", NOTES)
    response = call_asgi(
        create_app(context=ctx),
        "POST",
        f"/api/brains/{brain.id}/sources",
        upload_headers(),
        (body[offset : offset + 3] for offset in range(0, len(body), 3)),
    )

    assert response.status == 202, response.body
    uploads = staged_uploads(ctx)
    assert len(uploads) == 1
    assert (uploads[0] / "upload.txt").read_bytes() == NOTES


def test_body_that_is_not_multipart_is_415(client: TestClient, brain: BrainFixture) -> None:
    form = client.post(f"/api/brains/{brain.id}/sources", data={"note": "text"}, headers=WEB)
    json = client.post(f"/api/brains/{brain.id}/sources", json={"file": "text"}, headers=WEB)

    for response in (form, json):
        assert (response.status_code, response.json()["code"]) == (415, "unsupported_media_type")


def test_multipart_body_without_a_file_part_is_422(
    ctx: AppContext, client: TestClient, brain: BrainFixture
) -> None:
    only_fields = (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="note"\r\n\r\n'
        f"text\r\n--{BOUNDARY}--\r\n"
    ).encode()

    response = client.post(
        f"/api/brains/{brain.id}/sources", content=only_fields, headers=upload_headers()
    )

    assert (response.status_code, response.json()["code"]) == (422, "invalid_input")
    assert "'file'" in response.json()["message"]
    assert staged_uploads(ctx) == []


def test_upload_to_an_unknown_brain_is_404(ctx: AppContext, client: TestClient) -> None:
    response = client.post(
        "/api/brains/no-such-brain/sources", files={"file": ("a.txt", NOTES)}, headers=WEB
    )

    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert staged_uploads(ctx) == []


def test_upload_with_an_api_token_needs_no_client_header(
    ctx: AppContext, make_client: MakeClient, brain: BrainFixture
) -> None:
    client = make_client(ctx)
    secret = client.post("/api/tokens", json={"name": "agent"}, headers=WEB).json()["secret"]

    without = client.post(f"/api/brains/{brain.id}/sources", files={"file": ("a.txt", NOTES)})
    with_token = client.post(
        f"/api/brains/{brain.id}/sources",
        files={"file": ("a.txt", NOTES)},
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert without.status_code == 403
    assert with_token.status_code == 202
