"""Resumable Ingest state stored in the Brain repository, outside its OKF bundle."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from ruamel.yaml import YAML

from second_brain_builder.brains import is_slug
from second_brain_builder.ingest.chunks import split_into_chunks
from second_brain_builder.ingest.models import IngestStop
from second_brain_builder.okf import read_page
from second_brain_builder.providers import Usage


class IngestProgress(BaseModel):
    """A completed prefix of Parts and cumulative usage for this document and size."""

    canonical_sha256: str
    chunk_chars: int = Field(gt=0)
    parts_total: int = Field(ge=0)
    parts_done: int = Field(default=0, ge=0)
    status: Literal["partial", "complete"] = "partial"
    last_stop: IngestStop = IngestStop.COMPLETED
    usage: Usage = Field(default_factory=Usage)
    updated_at: datetime

    @model_validator(mode="after")
    def _check_parts(self) -> IngestProgress:
        if self.parts_done > self.parts_total:
            raise ValueError("parts_done exceeds parts_total")
        if (self.status == "complete") != (self.parts_done == self.parts_total):
            raise ValueError("status does not match Parts remaining")
        return self


def progress_path(root: Path, slug: str) -> Path:
    """The app-owned progress path, without following symlinks in a remote clone."""
    if not is_slug(slug):
        raise ValueError("invalid Source slug")
    path = root
    for part in (".brain", "ingest", f"{slug}.yaml"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Ingest progress cannot use symbolic links")
    return path


def read_progress(root: Path, slug: str) -> IngestProgress | None:
    """Read and validate saved progress; a missing file has no completed Parts."""
    path = progress_path(root, slug)
    if not path.exists():
        return None
    return IngestProgress.model_validate(YAML(typ="safe").load(path.read_text(encoding="utf-8")))


def write_progress(root: Path, slug: str, progress: IngestProgress) -> None:
    """Write a validated progress model as YAML."""
    path = progress_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        YAML(typ="safe").dump(progress.model_dump(mode="json"), stream)


def document_progress(
    root: Path, canonical: PurePosixPath, chunk_chars: int, now: datetime, *, resume: bool = True
) -> IngestProgress:
    """Keep progress only when the document bytes and Part boundaries still match."""
    digest = hashlib.sha256((root / canonical).read_bytes()).hexdigest()
    total = len(split_into_chunks(read_page(root, canonical).body, max_chars=chunk_chars))
    previous = read_progress(root, canonical.stem) if resume else None
    if (
        previous is not None
        and previous.canonical_sha256 == digest
        and previous.chunk_chars == chunk_chars
        and previous.parts_total == total
    ):
        return previous
    return IngestProgress(
        canonical_sha256=digest,
        chunk_chars=chunk_chars,
        parts_total=total,
        status="complete" if total == 0 else "partial",
        updated_at=now,
    )
