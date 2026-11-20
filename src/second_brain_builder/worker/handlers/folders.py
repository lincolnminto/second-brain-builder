"""The Folder jobs: ``folder.create``, ``folder.rename``, and ``folder.delete``.

An empty Folder is kept by ``sources/<folder>/.gitkeep`` (``FOLDER_MARKER``). A
rename moves the whole Folder directory with ``relocate``, like ``source.move``,
so links, resources, ``log.md``, and Ingest progress follow the moved Sources.
When a job leaves the Folder above it empty, that Folder gets a marker, so it
stays in the tree.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import FOLDER_MARKER, SOURCES_DIR, kept_folders
from second_brain_builder.okf import LogKind, log_entry
from second_brain_builder.services._common import MAX_FOLDER_DEPTH, MAX_FOLDER_LENGTH
from second_brain_builder.services.payloads import FolderPayload, FolderRenamePayload
from second_brain_builder.worker.canonical import canonical_documents, folder_of, folder_text
from second_brain_builder.worker.context import JobRun
from second_brain_builder.worker.errors import JobError
from second_brain_builder.worker.handlers._relocate import absolute, checked_folder, relocate

MAX_LISTED = 5
"""How many blocking Sources or files an error message names."""


def create_folder(run: JobRun) -> None:
    """Commit ``sources/<folder>/.gitkeep``, so the empty Folder shows in the tree."""
    payload = run.payload(FolderPayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root
    folder = _named(payload.folder, root)
    directory = SOURCES_DIR / folder
    _check_real_directories(root, directory)
    marker = absolute(root, directory / FOLDER_MARKER)
    if marker.is_symlink() or (marker.exists() and not marker.is_file()):
        raise JobError(f"{(directory / FOLDER_MARKER).as_posix()} is not a regular file.")
    if not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"")
    clone.finish(f"Create folder: {folder.as_posix()}", [])


def rename_folder(run: JobRun) -> None:
    """Move a Folder, with its Sources and subfolders, to a new path.

    Every link to the moved files is rewritten (see ``relocate``). Source ids do
    not change, so the ``sources`` rows follow on the refresh after the push. The
    old Folder can have a name that is not a slug (a Folder pushed from GitHub);
    the new one is a slug path. The size limits of Folders are checked again here,
    because the tree can have changed since the job was queued.
    """
    payload = run.payload(FolderRenamePayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root
    old = _named(payload.folder, root, slugs=False)
    new = _named(payload.new_folder, root)
    if new == old:
        clone.finish("Rename folder", [])
        return
    if old in new.parents:
        raise JobError("A Folder cannot move into itself or one of its subfolders.")
    old_dir, new_dir = SOURCES_DIR / old, SOURCES_DIR / new
    _check_real_directories(root, old_dir)
    _check_real_directories(root, new_dir.parent)
    if not absolute(root, old_dir).is_dir():
        raise JobError(f"The Folder {old.as_posix()} does not exist. Sync the Brain.")
    if absolute(root, new_dir).exists() or absolute(root, new_dir).is_symlink():
        raise JobError(f"The Folder {new.as_posix()} exists already. Choose another name.")
    _check_sizes(root, old, new)

    relocation = relocate(run, clone, {old_dir: new_dir})
    _keep_folder(root, old_dir.parent)
    old_text, new_text = old.as_posix(), new.as_posix()
    clone.finish(
        f"Rename folder: {old_text} to {new_text}",
        [log_entry(LogKind.MOVE, f"Folder {old_text}", None, f"renamed to {new_text}")],
    )
    relocation.emit(run)


def delete_folder(run: JobRun) -> None:
    """Remove a Folder that holds no Source: its ``.gitkeep`` files and empty subfolders.

    A Folder that holds a Source or any other file fails the job and changes
    nothing. A Folder that is gone already succeeds without a commit.
    """
    payload = run.payload(FolderPayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    root = clone.root
    folder = _named(payload.folder, root)
    directory = SOURCES_DIR / folder
    _check_real_directories(root, directory)
    target = absolute(root, directory)
    if not target.is_dir():
        clone.finish("Delete folder", [])
        return
    sources = sorted(
        slug for slug, path in canonical_documents(root).items() if directory in path.parents
    )
    if sources:
        raise JobError(
            f"The Folder {folder.as_posix()} holds Sources ({_names(sources)}). "
            "Move or delete them first."
        )
    others = _other_files(root, target)
    if others:
        raise JobError(
            f"The Folder {folder.as_posix()} holds other files ({_names(others)}). "
            "Remove them first."
        )
    shutil.rmtree(target)
    _keep_folder(root, directory.parent)
    clone.finish(f"Delete folder: {folder.as_posix()}", [])


# --- helpers ----------------------------------------------------------------------------


def _named(text: str, root: Path, *, slugs: bool = True) -> PurePosixPath:
    """A Folder of the payload other than the root of ``sources/`` (see ``checked_folder``)."""
    folder = checked_folder(text, root, slugs=slugs)
    if not folder_text(folder):
        raise JobError("The root of sources/ is not a Folder that can change.")
    return folder


def _check_sizes(root: Path, old: PurePosixPath, new: PurePosixPath) -> None:
    """Refuse a rename that gives a Folder below ``old`` too many levels or characters.

    The Folders are those of the Sources and of the ``.gitkeep`` files in the
    clone now, as the service counts them.
    """
    folders = [folder_of(path) for path in canonical_documents(root).values()]
    folders += kept_folders(root)
    for folder in folders:
        if folder != old and old not in folder.parents:
            continue
        moved = (new / folder.relative_to(old)).as_posix()
        if len(PurePosixPath(moved).parts) > MAX_FOLDER_DEPTH:
            raise JobError(
                f"A Folder can have at most {MAX_FOLDER_DEPTH} levels, also after the rename "
                f"({moved})."
            )
        if len(moved) > MAX_FOLDER_LENGTH:
            raise JobError(
                f"A Folder path can have at most {MAX_FOLDER_LENGTH} characters, also after "
                "the rename."
            )


def _check_real_directories(root: Path, directory: PurePosixPath) -> None:
    """Refuse a path whose existing parts are symbolic links or files, from ``sources/`` down."""
    current = PurePosixPath()
    for part in directory.parts:
        current = current / part
        path = absolute(root, current)
        if path.is_symlink():
            raise JobError(f"{current.as_posix()} is a symbolic link, not a Folder.")
        if not path.exists():
            return
        if not path.is_dir():
            raise JobError(f"{current.as_posix()} is a file, not a Folder.")


def _other_files(root: Path, directory: Path) -> list[str]:
    """Entries below ``directory`` other than ``.gitkeep`` files and directories, as paths."""
    found: list[str] = []
    for current, directories, files in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in directories:
            if (base / name).is_symlink():
                found.append((base / name).relative_to(root).as_posix())
        for name in files:
            path = base / name
            if name != FOLDER_MARKER or path.is_symlink():
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _keep_folder(root: Path, directory: PurePosixPath) -> None:
    """Give the Folder at ``directory`` a marker when it is gone or empty now."""
    if directory == SOURCES_DIR or SOURCES_DIR not in directory.parents:
        return
    path = absolute(root, directory)
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        return
    if path.is_dir() and any(path.iterdir()):
        return
    path.mkdir(parents=True, exist_ok=True)
    (path / FOLDER_MARKER).write_bytes(b"")


def _names(values: list[str]) -> str:
    listed = ", ".join(values[:MAX_LISTED])
    more = len(values) - MAX_LISTED
    return f"{listed}, and {more} more" if more > 0 else listed
