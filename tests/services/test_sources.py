"""Source use cases: uploads (hash, size, format, duplicates, quota), Folders, and job payloads."""

from __future__ import annotations

import base64
import hashlib
import io
import tracemalloc
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import pytest

from second_brain_builder.domain import JobKind
from second_brain_builder.ingest.progress import document_progress, progress_path, write_progress
from second_brain_builder.okf import read_page, write_page
from second_brain_builder.providers import Usage
from second_brain_builder.services import (
    AppContext,
    ConflictError,
    DuplicateSourceError,
    InvalidInputError,
    NotConfiguredError,
    NotFoundError,
    ProviderInput,
    ProviderKind,
    QuotaExceededError,
    TaskMode,
    TaskSettingInput,
    TaskSettingsInput,
    UploadTooLargeError,
    add_source,
    add_source_base64,
    create_provider,
    delete_source,
    get_folder_tree,
    get_lfs_usage,
    get_source,
    ingest_estimate,
    ingest_source,
    list_jobs,
    list_sources,
    move_source,
    original_file,
    put_task_settings,
    reconvert_source,
)
from second_brain_builder.services.uploads import BASE64_PIECE_CHARS, CHUNK_BYTES

if TYPE_CHECKING:
    from tests.services.conftest import AddSourceRow, BrainFixture, InsertBrain

NOTES = b"Field notes from a synthetic salt marsh.\nHerons wade at dawn.\n"


def staged_dirs(ctx: AppContext) -> list[Path]:
    uploads = ctx.settings.uploads_dir
    return sorted(uploads.iterdir()) if uploads.is_dir() else []


def field_notes(size: int) -> bytes:
    """Synthetic plain text of exactly ``size`` bytes, different in every line."""
    lines: list[bytes] = []
    total = 0
    while total < size:
        line = f"Note {len(lines)}: herons wade in the salt marsh.\n".encode()
        lines.append(line)
        total += len(line)
    return b"".join(lines)[:size]


def mime_base64(content: bytes) -> str:
    """Base64 with a CRLF after every 76 characters, as in MIME."""
    encoded = base64.b64encode(content).decode()
    return "\r\n".join(encoded[start : start + 76] for start in range(0, len(encoded), 76))


class CountingStream(io.BytesIO):
    """Counts how many bytes the reader took."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.taken = 0

    def read(self, size: int | None = -1, /) -> bytes:
        chunk = super().read(size)
        self.taken += len(chunk)
        return chunk


# --- add_source -----------------------------------------------------------------------


def test_upload_is_staged_hashed_and_enqueued_as_source_add(
    ctx: AppContext, brain: BrainFixture
) -> None:
    result = add_source(ctx, brain.id, "Marsh Notes.txt", io.BytesIO(NOTES), folder="Field Work")

    payload = result.job.payload
    staged = Path(payload["upload_path"])
    assert result.job.kind is JobKind.SOURCE_ADD
    assert payload == {
        "brain_id": brain.id,
        "upload_path": str(staged),
        "original_filename": "Marsh Notes.txt",
        "folder": "field-work",
        "sha256": hashlib.sha256(NOTES).hexdigest(),
    }
    assert staged.read_bytes() == NOTES
    assert staged.parent.parent == ctx.settings.uploads_dir.resolve()
    assert staged.name == "upload.txt"
    assert (result.sha256, result.size_bytes, result.media_type) == (
        payload["sha256"],
        len(NOTES),
        "text/plain",
    )


def test_filename_keeps_only_its_base_name(ctx: AppContext, brain: BrainFixture) -> None:
    result = add_source(ctx, brain.id, "../../outside/notes.txt", io.BytesIO(NOTES))

    assert result.job.payload["original_filename"] == "notes.txt"
    assert Path(result.job.payload["upload_path"]).parent.parent == (
        ctx.settings.uploads_dir.resolve()
    )


@pytest.mark.parametrize("folder", ["../escape", "nature/../../escape", ".git", "a\\b"])
def test_folder_that_leaves_sources_is_rejected(
    ctx: AppContext, brain: BrainFixture, folder: str
) -> None:
    with pytest.raises(InvalidInputError):
        add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES), folder=folder)

    assert staged_dirs(ctx) == []
    assert list_jobs(ctx, brain.id) == []


def test_same_content_as_an_existing_source_is_a_duplicate(
    ctx: AppContext, brain: BrainFixture
) -> None:
    content = (brain.root / "sources/nature/tide-pools/tide-pools.txt").read_bytes()

    with pytest.raises(DuplicateSourceError) as caught:
        add_source(ctx, brain.id, "copy of tide pools.txt", io.BytesIO(content))

    assert caught.value.existing is not None
    assert caught.value.existing.slug == "tide-pools"
    assert staged_dirs(ctx) == []
    assert list_jobs(ctx, brain.id) == []


def test_same_content_as_a_queued_upload_is_a_duplicate(
    ctx: AppContext, brain: BrainFixture
) -> None:
    first = add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES))

    with pytest.raises(DuplicateSourceError) as caught:
        add_source(ctx, brain.id, "notes again.txt", io.BytesIO(NOTES))

    assert caught.value.pending_job is not None
    assert caught.value.pending_job.id == first.job.id
    assert len(staged_dirs(ctx)) == 1


def test_same_content_in_another_brain_is_not_a_duplicate(
    ctx: AppContext, brain: BrainFixture, add_source_row: AddSourceRow, insert_brain: InsertBrain
) -> None:
    other = insert_brain("test-user/other-notes", name="Other")
    add_source_row(other, "notes", "", sha256=hashlib.sha256(NOTES).hexdigest(), title="Notes")

    assert add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES)).job.brain_id == brain.id


def test_upload_over_the_size_limit_stops_reading_and_keeps_nothing(
    make_ctx: Any, brain: BrainFixture
) -> None:
    ctx: AppContext = make_ctx(max_upload_bytes=1000)
    stream = CountingStream(b"tide " * (1024 * 1024))  # 5 MiB

    with pytest.raises(UploadTooLargeError) as caught:
        add_source(ctx, brain.id, "notes.txt", stream)

    assert caught.value.max_bytes == 1000
    assert stream.taken == CHUNK_BYTES  # the first chunk already went over the limit
    assert staged_dirs(ctx) == []
    assert list_jobs(ctx, brain.id) == []


@pytest.mark.parametrize(
    ("filename", "content"),
    [("empty.txt", b""), ("program.bin", bytes(range(256)) * 4)],
    ids=["empty", "unsupported"],
)
def test_empty_or_unsupported_upload_is_invalid_input(
    ctx: AppContext, brain: BrainFixture, filename: str, content: bytes
) -> None:
    with pytest.raises(InvalidInputError):
        add_source(ctx, brain.id, filename, io.BytesIO(content))

    assert staged_dirs(ctx) == []


def test_upload_over_the_lfs_quota_needs_confirmation(
    ctx: AppContext, brain: BrainFixture, require_lfs: None
) -> None:
    used = get_lfs_usage(ctx, brain.id).owner_bytes
    tight = replace(ctx, settings=ctx.settings.model_copy(update={"lfs_quota_bytes": used + 80}))

    first = add_source(tight, brain.id, "a.txt", io.BytesIO(NOTES))  # fits: 63 bytes
    with pytest.raises(QuotaExceededError) as caught:
        add_source(tight, brain.id, "b.txt", io.BytesIO(NOTES.upper()))

    error = caught.value
    assert (error.used_bytes, error.upload_bytes, error.quota_bytes) == (
        used + first.size_bytes,
        len(NOTES),
        used + 80,
    )
    assert len(staged_dirs(ctx)) == 1

    confirmed = add_source(
        tight, brain.id, "b.txt", io.BytesIO(NOTES.upper()), confirm_over_quota=True
    )
    assert confirmed.job.kind is JobKind.SOURCE_ADD


def test_base64_upload_for_the_mcp_tool_is_decoded_and_hashed(
    ctx: AppContext, brain: BrainFixture
) -> None:
    encoded = base64.b64encode(NOTES).decode()
    wrapped = "\n".join(encoded[i : i + 20] for i in range(0, len(encoded), 20))

    result = add_source_base64(ctx, brain.id, "notes.txt", wrapped, folder="inbox")

    assert result.sha256 == hashlib.sha256(NOTES).hexdigest()
    assert result.job.payload["folder"] == "inbox"


def test_base64_upload_that_is_not_base64_is_invalid_input(
    ctx: AppContext, brain: BrainFixture
) -> None:
    with pytest.raises(InvalidInputError, match="base64"):
        add_source_base64(ctx, brain.id, "notes.txt", "not base64 at all!")
    assert staged_dirs(ctx) == []


def test_base64_upload_over_the_limit_is_refused_before_decoding(
    make_ctx: Any, brain: BrainFixture
) -> None:
    ctx: AppContext = make_ctx(max_upload_bytes=30)
    # Not valid base64 either: only the size check can reject it.
    with pytest.raises(UploadTooLargeError):
        add_source_base64(ctx, brain.id, "notes.txt", "*" * 80)


def test_base64_upload_over_the_limit_is_refused_without_a_copy_of_the_text(
    make_ctx: Any, brain: BrainFixture
) -> None:
    ctx: AppContext = make_ctx(max_upload_bytes=1024 * 1024)
    text = mime_base64(field_notes(4 * 1024 * 1024))

    tracemalloc.start()
    try:
        with pytest.raises(UploadTooLargeError):
            add_source_base64(ctx, brain.id, "notes.txt", text)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < len(text) // 20
    assert staged_dirs(ctx) == []


def test_base64_upload_is_decoded_piece_by_piece_while_it_is_staged(
    ctx: AppContext, brain: BrainFixture
) -> None:
    content = field_notes(16 * 1024 * 1024)
    text = mime_base64(content)

    tracemalloc.start()
    try:
        result = add_source_base64(ctx, brain.id, "notes.txt", text)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert Path(result.job.payload["upload_path"]).read_bytes() == content
    # A few 1 MiB chunks, but neither the decoded file nor a copy of the text.
    assert peak < len(content) // 2


@pytest.mark.parametrize(
    "defect", ["character", "padding before the end", "incomplete last group", "non-ascii"]
)
def test_base64_text_with_a_defect_after_the_first_piece_is_invalid_input(
    ctx: AppContext, brain: BrainFixture, defect: str
) -> None:
    content = field_notes(3 * BASE64_PIECE_CHARS)
    text = mime_base64(content)
    middle = 2 * BASE64_PIECE_CHARS  # in the third piece of the text
    # These bytes encode to exactly one piece of text that ends with "==" padding.
    padded_piece = base64.b64encode(content[: BASE64_PIECE_CHARS // 4 * 3 - 2]).decode()
    assert (len(padded_piece), padded_piece[-2:]) == (BASE64_PIECE_CHARS, "==")
    broken = {
        "character": text[:middle] + "*" + text[middle:],
        "padding before the end": padded_piece + text,
        "incomplete last group": text.rstrip()[:-1],
        "non-ascii": text[:middle] + "é" + text[middle:],
    }[defect]

    with pytest.raises(InvalidInputError, match="base64"):
        add_source_base64(ctx, brain.id, "notes.txt", broken)

    assert staged_dirs(ctx) == []
    assert list_jobs(ctx, brain.id) == []


# --- reading Sources ------------------------------------------------------------------


def test_source_detail_lists_the_wiki_pages_that_cite_it(
    ctx: AppContext, brain: BrainFixture
) -> None:
    detail = get_source(ctx, brain.id, "tide-pools")

    assert detail.source.canonical_path == "sources/nature/tide-pools/tide-pools.md"
    assert [page.path for page in detail.citing_pages] == [
        "wiki/entities/grey-heron.md",
        "wiki/entities/sea-star.md",
        "wiki/summaries/coastal-birds.md",
    ]


def test_original_file_has_the_original_filename(ctx: AppContext, brain: BrainFixture) -> None:
    # The worker records the name of the uploaded file in the Canonical Document.
    canonical = brain.root / "sources/nature/tide-pools/tide-pools.md"
    text = canonical.read_text(encoding="utf-8")
    renamed = text.replace(
        "original_filename: tide-pools.txt", 'original_filename: "Tide Pools - Field Guide.txt"'
    )
    assert renamed != text
    canonical.write_text(renamed, encoding="utf-8")

    original = original_file(ctx, brain.id, "tide-pools")

    assert original.path == (brain.root / "sources/nature/tide-pools/tide-pools.txt").resolve()
    assert (original.filename, original.media_type, original.inline) == (
        "Tide Pools - Field Guide.txt",
        "text/plain",
        True,
    )


def test_original_file_that_is_an_lfs_pointer_is_not_found(
    ctx: AppContext, brain: BrainFixture
) -> None:
    # A clone made with GIT_LFS_SKIP_SMUDGE=1 holds pointer files like this one.
    pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{'0' * 64}\nsize 812\n"
    (brain.root / "sources/nature/tide-pools/tide-pools.txt").write_text(pointer)

    with pytest.raises(NotFoundError, match="not downloaded"):
        original_file(ctx, brain.id, "tide-pools")


def test_folder_tree_counts_sources_per_folder(
    ctx: AppContext, brain: BrainFixture, add_source_row: AddSourceRow
) -> None:
    add_source_row(brain.id, "dune", "books/sci-fi", sha256="1" * 64, title="Dune")
    add_source_row(brain.id, "atlas", "", sha256="2" * 64, title="Atlas")

    tree = get_folder_tree(ctx, brain.id)

    assert (tree.path, tree.source_count, tree.total_source_count) == ("", 1, 4)
    assert [child.path for child in tree.children] == ["books", "nature"]
    books = tree.children[0]
    assert (books.source_count, books.total_source_count) == (0, 1)
    assert [(child.name, child.path) for child in books.children] == [("sci-fi", "books/sci-fi")]
    assert [s.slug for s in list_sources(ctx, brain.id, folder="books")] == ["dune"]
    assert list_sources(ctx, brain.id, folder="books", recursive=False) == []


# --- jobs on one Source ---------------------------------------------------------------


def test_source_jobs_enqueue_their_payloads(ctx: AppContext, brain: BrainFixture) -> None:
    move = move_source(ctx, brain.id, "tide-pools", "Archive/Shore")
    reconvert = reconvert_source(ctx, brain.id, "coastal-birds")

    assert (move.kind, move.payload) == (
        JobKind.SOURCE_MOVE,
        {"brain_id": brain.id, "source_slug": "tide-pools", "folder": "archive/shore"},
    )
    assert (reconvert.kind, reconvert.payload) == (
        JobKind.SOURCE_RECONVERT,
        {"brain_id": brain.id, "source_slug": "coastal-birds"},
    )


def test_a_source_that_is_being_deleted_takes_no_other_job(
    ctx: AppContext, brain: BrainFixture
) -> None:
    deletion = delete_source(ctx, brain.id, "tide-pools")

    assert deletion.payload == {"brain_id": brain.id, "source_slug": "tide-pools"}
    for action in (
        lambda: move_source(ctx, brain.id, "tide-pools", "archive"),
        lambda: reconvert_source(ctx, brain.id, "tide-pools"),
    ):
        with pytest.raises(ConflictError, match="being deleted"):
            action()


def test_move_to_the_same_folder_is_invalid(ctx: AppContext, brain: BrainFixture) -> None:
    with pytest.raises(InvalidInputError, match="already"):
        move_source(ctx, brain.id, "tide-pools", "/nature/")


def test_a_folder_inside_the_directory_of_a_source_is_refused(
    ctx: AppContext, brain: BrainFixture
) -> None:
    # sources/nature/tide-pools/ is the directory of the Source tide-pools.
    with pytest.raises(InvalidInputError, match="'tide-pools'"):
        add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES), folder="Nature/Tide Pools/Rocks")
    with pytest.raises(InvalidInputError, match="'tide-pools'"):
        move_source(ctx, brain.id, "coastal-birds", "Nature/Tide Pools")
    assert staged_dirs(ctx) == []
    assert list_jobs(ctx, brain.id) == []

    # A Folder whose name only starts with the slug is another directory.
    added = add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES), folder="nature/tide-pools-2")
    assert added.job.payload["folder"] == "nature/tide-pools-2"


def test_a_source_cannot_move_to_where_its_directory_would_hold_other_sources(
    ctx: AppContext, brain: BrainFixture, add_source_row: AddSourceRow
) -> None:
    add_source_row(brain.id, "nature", "archive", sha256="3" * 64, title="Nature")

    # Its directory would be sources/nature/, the Folder of tide-pools and coastal-birds.
    with pytest.raises(InvalidInputError, match="holds other Sources"):
        move_source(ctx, brain.id, "nature", "")
    assert list_jobs(ctx, brain.id) == []


def test_a_pending_move_counts_for_the_folder_checks(ctx: AppContext, brain: BrainFixture) -> None:
    move_source(ctx, brain.id, "tide-pools", "")  # its directory becomes sources/tide-pools/

    with pytest.raises(InvalidInputError, match="'tide-pools'"):
        add_source(ctx, brain.id, "notes.txt", io.BytesIO(NOTES), folder="tide-pools")
    with pytest.raises(InvalidInputError, match="'tide-pools'"):
        move_source(ctx, brain.id, "coastal-birds", "tide-pools/birds")


def test_ingest_needs_an_ingest_provider(ctx: AppContext, brain: BrainFixture) -> None:
    with pytest.raises(NotConfiguredError):
        ingest_source(ctx, brain.id, "tide-pools")

    provider = create_provider(
        ctx,
        ProviderInput(name="Local", kind=ProviderKind.OPENAI, base_url="http://127.0.0.1:1/v1"),
    )
    put_task_settings(
        ctx,
        TaskSettingsInput(
            ingest=TaskSettingInput(mode=TaskMode.PROVIDER, provider_id=provider.id, model="m")
        ),
    )

    job = ingest_source(ctx, brain.id, "tide-pools")
    assert (job.kind, job.payload) == (
        JobKind.SOURCE_INGEST,
        {
            "brain_id": brain.id,
            "source_slug": "tide-pools",
            "resume": True,
            "max_tokens": ctx.settings.ingest_max_tokens,
        },
    )
    custom = ingest_source(ctx, brain.id, "coastal-birds", resume=False, max_tokens=2000)
    assert custom.payload["resume"] is False
    assert custom.payload["max_tokens"] == 2000
    with pytest.raises(InvalidInputError, match="positive"):
        ingest_source(ctx, brain.id, "coastal-birds", max_tokens=0)


def test_unknown_source_is_not_found(ctx: AppContext, brain: BrainFixture) -> None:
    with pytest.raises(NotFoundError):
        delete_source(ctx, brain.id, "missing")


def test_ingest_estimate_uses_observed_usage_for_remaining_parts(
    ctx: AppContext, brain: BrainFixture
) -> None:
    canonical = PurePosixPath("sources/nature/tide-pools/tide-pools.md")
    page = read_page(brain.root, canonical)
    page.body = (
        "# Tide Pools\n\n" + "Alpha pools. " * 3000 + "\n\n## Beta\n\n" + "Beta pools. " * 3000
    )
    write_page(brain.root, page)
    estimate = ingest_estimate(ctx, brain.id, "tide-pools")
    assert (estimate.parts_total, estimate.parts_done) == (2, 0)
    assert estimate.estimated_tokens == 2 * ctx.settings.ingest_part_tokens * 6

    progress = document_progress(
        brain.root, canonical, ctx.settings.ingest_part_tokens * 4, ctx.clock()
    )
    progress = progress.model_copy(
        update={
            "parts_done": 1,
            "usage": Usage(input_tokens=9000, output_tokens=2000),
            "completed_usage": Usage(input_tokens=800, output_tokens=200, cache_read_tokens=300),
        }
    )
    write_progress(brain.root, "tide-pools", progress)
    progress_path(brain.root, "tide-pools").write_text(
        progress.model_dump_json(exclude={"recent_part_tokens"}), encoding="utf-8"
    )
    observed = ingest_estimate(ctx, brain.id, "tide-pools")
    assert (observed.parts_total, observed.parts_done, observed.estimated_tokens) == (2, 1, 1000)

    progress = progress.model_copy(update={"parts_done": 2, "status": "complete"})
    write_progress(brain.root, "tide-pools", progress)
    assert ingest_estimate(ctx, brain.id, "tide-pools").estimated_tokens == 0


@pytest.mark.parametrize(
    ("costs", "per_part"),
    [([100, 300, 500], 400), ([500, 300, 100], 300)],
)
def test_ingest_estimate_uses_maximum_of_overall_and_recent_average(
    ctx: AppContext, brain: BrainFixture, costs: list[int], per_part: int
) -> None:
    canonical = PurePosixPath("sources/nature/tide-pools/tide-pools.md")
    page = read_page(brain.root, canonical)
    page.body = "\n\n".join(f"## Part {part}\n\n" + "Pools. " * 8000 for part in range(6))
    write_page(brain.root, page)
    initial = document_progress(
        brain.root, canonical, ctx.settings.ingest_part_tokens * 4, ctx.clock()
    )
    assert initial.parts_total > len(costs)
    total = 0
    for number, cost in enumerate(costs, start=1):
        total += cost
        progress = initial.model_copy(
            update={
                "parts_done": number,
                "completed_usage": Usage(
                    input_tokens=total - number * 10, output_tokens=number * 10
                ),
            }
        )
        write_progress(brain.root, "tide-pools", progress)
    estimate = ingest_estimate(ctx, brain.id, "tide-pools")
    assert estimate.parts_done == 3
    assert estimate.estimated_tokens == per_part * (estimate.parts_total - 3)
