"""Folders: the tree (with empty Folders), create, rename, and delete.

A Folder is a slug path under ``sources/``. A Folder with Sources exists through
their directories; an empty Folder through its ``.gitkeep`` file in the Brain repo
(``brains.FOLDER_MARKER``). Every change is a job: ``folder.create``,
``folder.rename``, and ``folder.delete``. Folder names from requests go through
``normalize_folder``, so traversal and dot segments never reach a job. A request
that names an existing Folder exactly (one pushed with a name that is not a
slug) keeps that name, so the filter, rename, and delete reach it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import select

from second_brain_builder.brains import kept_folders
from second_brain_builder.domain import JobKind
from second_brain_builder.services._common import (
    MAX_FOLDER_DEPTH,
    MAX_FOLDER_LENGTH,
    brain_row,
    folder_text,
    job_view,
    normalize_folder,
)
from second_brain_builder.services.context import AppContext
from second_brain_builder.services.errors import ConflictError, InvalidInputError, NotFoundError
from second_brain_builder.services.models import FolderNode, JobView
from second_brain_builder.services.payloads import FolderPayload, FolderRenamePayload
from second_brain_builder.services.sources import ACTIVE_STATUSES, _check_folder_placement
from second_brain_builder.store import SourceRow

FOLDER_JOBS = frozenset({JobKind.FOLDER_CREATE, JobKind.FOLDER_RENAME, JobKind.FOLDER_DELETE})
"""The job kinds that change Folders."""

_PLACEMENT_JOBS = frozenset({JobKind.SOURCE_ADD, JobKind.SOURCE_MOVE})
"""The job kinds that put a Source into the Folder of their payload."""


# --- the tree -------------------------------------------------------------------------


@dataclass
class _Folder:
    name: str
    path: str
    count: int = 0
    children: dict[str, _Folder] = field(default_factory=dict)

    def add(self, folder: str) -> _Folder:
        """The node of ``folder`` (``a/b``), with the nodes on its way made as needed."""
        node = self
        parts = PurePosixPath(folder).parts if folder else ()
        for index, part in enumerate(parts):
            path = "/".join(parts[: index + 1])
            node = node.children.setdefault(part, _Folder(name=part, path=path))
        return node

    def node(self) -> FolderNode:
        children = [self.children[name].node() for name in sorted(self.children)]
        total = self.count + sum(child.total_source_count for child in children)
        return FolderNode(
            name=self.name,
            path=self.path,
            source_count=self.count,
            total_source_count=total,
            children=children,
        )


@dataclass(frozen=True)
class _Folders:
    """The Folders of one Brain: Source counts from the rows, empty Folders from the clone."""

    counts: Counter[str]
    kept: list[str]

    def existing(self) -> set[str]:
        """Every Folder (``a/b``) that holds Sources or ``.gitkeep``, and every Folder above one."""
        found: set[str] = set()
        for folder in [*self.counts, *self.kept]:
            parts = PurePosixPath(folder).parts if folder else ()
            found.update("/".join(parts[: index + 1]) for index in range(len(parts)))
        return found

    def sources_in(self, folder: str) -> int:
        """The number of Sources in ``folder`` and its subfolders."""
        return sum(count for name, count in self.counts.items() if _within(name, folder))


def get_folder_tree(ctx: AppContext, brain_id: str) -> FolderNode:
    """The Folder tree of a Brain. The root is the ``sources/`` directory.

    Folders come from the Sources and from the ``.gitkeep`` files of empty Folders
    in the clone, so a new Folder shows up before it holds a Source.

    Raises:
        NotFoundError: No such Brain.
    """
    folders = _read_folders(ctx, brain_id)
    root = _Folder(name="", path="")
    for folder, count in folders.counts.items():
        root.add(folder).count += count
    for folder in folders.kept:
        root.add(folder)
    return root.node()


# --- changes --------------------------------------------------------------------------


def create_folder(ctx: AppContext, brain_id: str, folder: str) -> JobView:
    """Enqueue ``folder.create``: an empty Folder, kept by ``sources/<folder>/.gitkeep``.

    Each part of ``folder`` becomes a slug (``"Work/Client Notes"`` gives
    ``work/client-notes``). Folders above it are made too.

    Raises:
        NotFoundError: No such Brain.
        InvalidInputError: The Folder is not valid, is the root, or is inside the
            directory of a Source.
        ConflictError: The Folder exists already, or a job on it is pending.
    """
    with ctx.add_source_lock:
        target = _named_folder(folder)
        if target in _read_folders(ctx, brain_id).existing():
            raise ConflictError(f"The Folder {target!r} exists already.")
        _check_folder_placement(ctx, brain_id, target)
        _check_no_pending(ctx, brain_id, [target])
        payload = FolderPayload(brain_id=brain_id, folder=target)
        return job_view(ctx.jobs.enqueue(JobKind.FOLDER_CREATE, brain_id, payload.to_json()))


def rename_folder(ctx: AppContext, brain_id: str, folder: str, new_folder: str) -> JobView:
    """Enqueue ``folder.rename``: move a Folder, its Sources, and its subfolders.

    The new path can be in another Folder (``work/old`` to ``archive/old``). Every
    link to the moved Sources is rewritten; Source ids do not change. ``folder``
    can name a Folder that was pushed with a name that is not a slug (see
    ``_existing_folder``), so a rename can give it a slug path.

    Raises:
        NotFoundError: No such Brain or Folder.
        InvalidInputError: A Folder is not valid or is the root, the new path is
            the same or inside the Folder, a moved subfolder (also one that a
            pending upload or move makes) would get too deep or too long, or the
            new path is inside the directory of a Source.
        ConflictError: The new path exists already, a job on either Folder is
            pending, or a Source is being added to or moved into the new path.
    """
    with ctx.add_source_lock:
        existing = _read_folders(ctx, brain_id).existing()
        old = _existing_folder(folder, existing)
        new = _named_folder(new_folder)
        if new == old:
            raise InvalidInputError("The Folder has this name already.")
        if _within(new, old):
            raise InvalidInputError("A Folder cannot move into itself or one of its subfolders.")
        if old not in existing:
            raise NotFoundError(f"The Folder {old!r} does not exist.")
        if new in existing:
            raise ConflictError(f"The Folder {new!r} exists already. Choose another name.")
        # A pending upload or move into a subfolder runs first, so it moves too.
        for name in existing | _pending_placements(ctx, brain_id):
            if _within(name, old):
                _check_size(new + name[len(old) :])
        _check_folder_placement(ctx, brain_id, new)
        _check_no_pending(ctx, brain_id, [old, new], sources_into=[new])
        payload = FolderRenamePayload(brain_id=brain_id, folder=old, new_folder=new)
        return job_view(ctx.jobs.enqueue(JobKind.FOLDER_RENAME, brain_id, payload.to_json()))


def delete_folder(ctx: AppContext, brain_id: str, folder: str) -> JobView:
    """Enqueue ``folder.delete`` for a Folder without Sources, with its empty subfolders.

    ``folder`` can name a Folder that was pushed with a name that is not a slug.

    Raises:
        NotFoundError: No such Brain or Folder.
        InvalidInputError: The Folder is not valid or is the root.
        ConflictError: The Folder or one of its subfolders holds a Source, a
            Source is being added to or moved into it, or a job on it is pending.
    """
    with ctx.add_source_lock:
        folders = _read_folders(ctx, brain_id)
        existing = folders.existing()
        target = _existing_folder(folder, existing)
        if target not in existing:
            raise NotFoundError(f"The Folder {target!r} does not exist.")
        count = folders.sources_in(target)
        if count:
            sources = "1 Source" if count == 1 else f"{count} Sources"
            raise ConflictError(
                f"The Folder {target!r} holds {sources}. Move or delete them first."
            )
        _check_no_pending(ctx, brain_id, [target], sources_into=[target])
        payload = FolderPayload(brain_id=brain_id, folder=target)
        return job_view(ctx.jobs.enqueue(JobKind.FOLDER_DELETE, brain_id, payload.to_json()))


# --- helpers ----------------------------------------------------------------------------


def _read_folders(ctx: AppContext, brain_id: str) -> _Folders:
    with ctx.read_sessions() as session:
        brain_row(session, brain_id)
        counts = Counter(
            session.scalars(select(SourceRow.folder).where(SourceRow.brain_id == brain_id))
        )
    root = ctx.clone_path(brain_id)
    kept = [folder.as_posix() for folder in kept_folders(root)] if root.is_dir() else []
    return _Folders(counts=counts, kept=kept)


def _named_folder(value: str) -> str:
    """A Folder from a request, as a slug path; never the root of ``sources/``."""
    folder = folder_text(normalize_folder(value))
    if not folder:
        raise InvalidInputError("Name a Folder; the root of sources/ cannot change.")
    return folder


def _existing_folder(value: str, existing: Collection[str]) -> str:
    """A Folder from a request that names a Folder of the Brain; never the root.

    A Folder pushed from GitHub can have a name that is not a slug, such as
    ``Field Notes``, or more levels than a new Folder can have. A value that names
    such a Folder exactly is used as it is. Any other value becomes a slug path,
    as in ``_named_folder``.
    """
    plain = _plain_folder(value)
    if plain and plain in existing:
        return plain
    return _named_folder(value)


def filter_folder(ctx: AppContext, brain_id: str, value: str | None) -> PurePosixPath | None:
    """The Folder of the page and graph filter; ``None`` (no filter) for no value and the root.

    The value becomes a slug path, except when it names a Folder of the Brain
    exactly (see ``_existing_folder``): the Folder tree lists such Folders, so
    their filter must find their Sources.

    Raises:
        NotFoundError: No such Brain (only checked for a value that is not a slug path).
        InvalidInputError: The Folder is not valid.
    """
    if value is None:
        return None
    plain = _plain_folder(value)
    # Only a value that is not a slug path needs the Folders of the Brain.
    if plain and plain != _slug_form(value) and plain in _read_folders(ctx, brain_id).existing():
        return PurePosixPath(plain)
    folder = normalize_folder(value)
    return folder if folder_text(folder) else None


def _plain_folder(value: str) -> str | None:
    """``value`` as a Folder path with its names as they are; ``None`` when it is not safe.

    Unsafe values (traversal, dot parts, a backslash, or a NUL character) are left
    to ``normalize_folder``, which rejects them.
    """
    if "\x00" in value or "\\" in value:
        return None
    parts = [part.strip() for part in value.split("/") if part.strip()]
    if any(part.startswith(".") for part in parts):
        return None
    return "/".join(parts)


def _slug_form(value: str) -> str | None:
    try:
        return folder_text(normalize_folder(value))
    except InvalidInputError:
        return None


def _pending_placements(ctx: AppContext, brain_id: str) -> set[str]:
    """The Folders that pending uploads and moves put Sources into."""
    return {
        folder
        for job in ctx.jobs.list_jobs(brain_id, statuses=ACTIVE_STATUSES, limit=10_000)
        if job.kind in _PLACEMENT_JOBS and isinstance(folder := job.payload.get("folder"), str)
    }


def _check_size(folder: str) -> None:
    if len(PurePosixPath(folder).parts) > MAX_FOLDER_DEPTH:
        raise InvalidInputError(
            f"A Folder can have at most {MAX_FOLDER_DEPTH} levels, also after the rename."
        )
    if len(folder) > MAX_FOLDER_LENGTH:
        raise InvalidInputError(
            f"A Folder path can have at most {MAX_FOLDER_LENGTH} characters, also after the rename."
        )


def _check_no_pending(
    ctx: AppContext, brain_id: str, folders: Collection[str], *, sources_into: Collection[str] = ()
) -> None:
    """Refuse a change while another Folder job, or a Source job into the Folder, is pending.

    A queued job keeps the Folder paths of its payload. Jobs of one Brain run in
    order, so a pending job would act on a Folder that moved or went away.
    """
    for job in ctx.jobs.list_jobs(brain_id, statuses=ACTIVE_STATUSES, limit=10_000):
        if job.kind in FOLDER_JOBS:
            paths = [job.payload.get("folder"), job.payload.get("new_folder")]
            for path in paths:
                if isinstance(path, str) and any(_overlap(path, name) for name in folders):
                    raise ConflictError(
                        f"A Folder job for {path!r} is pending already (job {job.id})."
                    )
        elif job.kind in _PLACEMENT_JOBS:
            target = job.payload.get("folder")
            if not isinstance(target, str):
                continue
            for name in sources_into:
                if _within(target, name):
                    raise ConflictError(
                        f"A Source is being added to or moved into {name!r} (job {job.id}). "
                        "Wait until the job ends."
                    )


def _within(folder: str, other: str) -> bool:
    """True when ``folder`` is ``other`` or one of its subfolders."""
    return folder == other or folder.startswith(f"{other}/")


def _overlap(first: str, second: str) -> bool:
    return _within(first, second) or _within(second, first)
