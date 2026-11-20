"""Move directories of ``sources/`` and keep every link to them working.

``source.move`` moves one Source directory and ``folder.rename`` moves a Folder
with all its Sources; both use ``relocate``. It renames the directories, then
rewrites page links, ``sources[].resource``, ``resource``, and ``log.md`` to the
new paths, and keeps the Ingest progress and the stored map notes valid for each
Source whose Canonical Document moved or got new links.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import (
    LOG_PATH,
    SOURCES_DIR,
    is_slug,
    read_pages,
)
from second_brain_builder.ingest.chunks import split_into_chunks
from second_brain_builder.ingest.progress import read_progress, write_progress
from second_brain_builder.okf import (
    Page,
    apply_moves,
    bundle_href,
    is_external,
    resolve_link,
    rewrite_links,
    write_page,
)
from second_brain_builder.worker.canonical import (
    canonical_documents,
    folder_path,
    folder_text,
    page_event,
    page_ref,
)
from second_brain_builder.worker.clone import BrainClone, remove_empty_dirs
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.errors import JobError
from second_brain_builder.worker.handlers.source_map_reduce import map_directory, rekey_map_notes

Moves = Mapping[PurePosixPath, PurePosixPath]
"""Directories from the Brain root, each to its new path."""


@dataclass
class Relocation:
    """What ``relocate`` changed, for the events after the commit."""

    moved: list[Page] = field(default_factory=list)
    """Pages at their old paths, before the move."""
    written: list[Page] = field(default_factory=list)
    """Pages at their new paths whose path, body, or frontmatter changed."""
    sources: dict[str, PurePosixPath] = field(default_factory=dict)
    """The new Canonical Document path of each moved Source, by Source id."""

    def emit(self, run: JobRun) -> None:
        """``page_deleted`` for each old path, then ``page_written`` for each changed page."""
        for page in self.moved:
            run.emit("page_deleted", page_ref(page))
        for page in self.written:
            run.emit("page_written", page_event(page))


def relocate(run: JobRun, clone: BrainClone, moves: Moves) -> Relocation:
    """Rename directories of the clone, and rewrite every reference to the moved files.

    The caller checked that each target is free and that no directory moves into
    itself. Folders left empty above an old directory are removed. The Source ids
    (slugs) do not change. The progress digest of each Source whose Canonical
    Document moves or gets new links follows its new bytes (when it matched the
    old ones and the rewritten Parts still match), so completed Parts and map
    notes stay valid. That includes a Source outside the moved directories that
    links into them.
    """
    root = clone.root
    pages = read_pages(root)
    plan = [(page, moved_page(page, apply_moves(page.path, moves), moves)) for page in pages]
    planned_pages = {page.path: (page, moved) for page, moved in plan}
    rewritten = {
        page.path for page, moved in plan if moved != page.model_copy(update={"path": moved.path})
    }
    changed_sources = {
        slug: path
        for slug, path in canonical_documents(root).items()
        if path in rewritten or apply_moves(path, moves) != path
    }
    old_digests = {slug: _digest(root, path) for slug, path in changed_sources.items()}
    for old_dir, new_dir in moves.items():
        target = absolute(root, new_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        absolute(root, old_dir).rename(target)
        remove_empty_dirs(root, old_dir.parent, SOURCES_DIR)

    result = Relocation()
    for page, moved in plan:
        if page.path in rewritten:
            write_page(root, moved)
        if moved.path != page.path:
            result.moved.append(page)
        if moved.path != page.path or page.path in rewritten:
            result.written.append(moved)
    log_text = clone.read_log()
    if log_text:
        clone.write_log(rewrite_links(log_text, dict(moves), from_path=LOG_PATH))

    brain_id = clone.brain_id
    for slug, old_path in changed_sources.items():
        new_path = apply_moves(old_path, moves)
        if new_path != old_path:
            result.sources[slug] = new_path
        old_digest, digest = old_digests[slug], _digest(root, new_path)
        saved = read_progress(root, slug)
        if saved is None:
            continue
        planned = planned_pages.get(old_path)
        same_parts = planned is not None and _same_parts(
            planned[0], planned[1], moves, saved.chunk_chars
        )
        checkpoint_digest = digest if same_parts else old_digest
        if same_parts:
            rekey_map_notes(map_directory(run, brain_id, slug), old_digest, digest)
        update: dict[str, object] = {
            "canonical_sha256": _rekeyed(saved.canonical_sha256, old_digest, checkpoint_digest),
            "written": [apply_moves(path, moves) for path in saved.written],
        }
        state = saved.map_reduce_state
        if state is not None:
            update["map_reduce_state"] = state.model_copy(
                update={
                    "canonical_sha256": _rekeyed(
                        state.canonical_sha256, old_digest, checkpoint_digest
                    ),
                    "written": [apply_moves(path, moves) for path in state.written],
                    "reduce_targets": [apply_moves(path, moves) for path in state.reduce_targets],
                }
            )
        write_progress(root, slug, saved.model_copy(update=update))
    return result


def _same_parts(page: Page, moved: Page, moves: Moves, chunk_chars: int) -> bool:
    """Keep checkpoints only when each new Part is exactly its old Part with links rewritten."""
    old_parts = split_into_chunks(page.body, max_chars=chunk_chars)
    new_parts = split_into_chunks(moved.body, max_chars=chunk_chars)
    return len(old_parts) == len(new_parts) and all(
        rewrite_links(old.text, dict(moves), from_path=page.path) == new.text
        for old, new in zip(old_parts, new_parts, strict=True)
    )


def checked_folder(text: str, root: Path, *, slugs: bool = True) -> PurePosixPath:
    """The Folder of a payload. No part of it can be a Source directory.

    With ``slugs``, every part must be a slug, as in every Folder that the app
    makes. Without it, a part can also be another plain name, as in a Folder
    pushed from GitHub (``Field Notes``), but never a dot part, a backslash, or a
    NUL character.

    Raises:
        JobError: The Folder is not valid, or it is inside a Source directory.
    """
    folder = folder_path(text)
    valid = is_slug if slugs else _plain_name
    if folder.is_absolute() or not all(valid(part) for part in folder.parts):
        raise JobError(f"Not a valid Folder: {text!r}.")
    directory = SOURCES_DIR
    for part in folder.parts:
        directory = directory / part
        if absolute(root, directory / f"{part}.md").is_file():
            raise JobError(f"{folder_text(folder)} is in the directory of a Source, not a Folder.")
    return folder


def moved_page(page: Page, new_path: PurePosixPath, moves: Moves) -> Page:
    """The page at its new path, with its links and resources pointing at the moved files."""
    body = rewrite_links(page.body, dict(moves), from_path=page.path)
    frontmatter = page.frontmatter
    if frontmatter is not None:
        sources = [
            ref.model_copy(
                update={"resource": _moved_reference(ref.resource, page.path, new_path, moves)}
            )
            for ref in frontmatter.sources
        ]
        resource = frontmatter.resource
        if resource:
            resource = _moved_reference(resource, page.path, new_path, moves)
        frontmatter = frontmatter.model_copy(update={"sources": sources, "resource": resource})
    return Page(path=new_path, frontmatter=frontmatter, body=body)


def absolute(root: Path, path: PurePosixPath) -> Path:
    """The file of the clone at ``path`` (from the Brain root)."""
    return root.joinpath(*path.parts)


def _moved_reference(
    value: str, old_path: PurePosixPath, new_path: PurePosixPath, moves: Moves
) -> str:
    """A path in frontmatter, changed to point at the moved file; else unchanged."""
    if is_external(value) or any(char.isspace() for char in value):
        return value
    suffix_start = min((value.find(char) for char in "#?" if char in value), default=len(value))
    path, suffix = value[:suffix_start], value[suffix_start:]
    if not path:
        return value
    target = resolve_link(old_path, path)
    if target is None:
        return value
    new_target = apply_moves(target, moves)
    if path.startswith("/"):
        return value if new_target == target else bundle_href(new_target) + suffix
    if resolve_link(new_path, path) == new_target:
        return value
    return bundle_href(new_target) + suffix


def _plain_name(part: str) -> bool:
    return (
        bool(part.strip()) and not part.startswith(".") and "\\" not in part and "\x00" not in part
    )


def _rekeyed(saved: str, old_digest: str, digest: str) -> str:
    """The new progress digest: it follows the bytes only when it named the old bytes."""
    return digest if saved == old_digest else saved


def _digest(root: Path, path: PurePosixPath) -> str:
    return hashlib.sha256(absolute(root, path).read_bytes()).hexdigest()
