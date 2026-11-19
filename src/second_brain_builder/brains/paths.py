"""Resolve paths from requests inside a Brain clone."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class BrainPathError(ValueError):
    """A path from a request does not name a file or directory inside the Brain."""


def resolve_in_brain(root: Path, path: str) -> Path:
    """Resolve ``path`` inside the Brain clone at ``root``.

    ``path`` is a path from the Brain root, with or without one leading ``/``:
    ``wiki/entities/ishmael.md`` and ``/wiki/entities/ishmael.md`` (the form of
    OKF links and of ``index.md`` entries) name the same file. A leading ``/``
    never means the root of the host file system. The result is the resolved
    absolute path; the file does not have to exist.

    Raises:
        BrainPathError: ``path`` is empty or names the root itself; starts with
            ``//``; has a ``..`` segment; has a dot-path segment (``.git``,
            ``.brain``, ``.hidden.md``), which is not part of an OKF bundle; has a
            backslash or a NUL character; leads outside ``root`` or to a dot-path
            through a symbolic link; or goes through a loop of symbolic links.
    """
    if not path or "\x00" in path or "\\" in path:
        raise BrainPathError(f"not a path in the Brain: {path!r}")
    relative = PurePosixPath(path.removeprefix("/"))
    if relative.is_absolute():
        raise BrainPathError(f"not a path in the Brain: {path!r}")
    _check_parts(relative.parts, path)
    resolved_root = root.resolve()
    try:
        resolved = resolved_root.joinpath(*relative.parts).resolve()
    except (OSError, RuntimeError) as exc:  # Python < 3.13 raises RuntimeError on a loop
        raise BrainPathError(f"path cannot be resolved: {path!r}") from exc
    # Python 3.13+ stops at a loop without an error and returns the link.
    if resolved.is_symlink():
        raise BrainPathError(f"path goes through a loop of symbolic links: {path!r}")
    if not resolved.is_relative_to(resolved_root):
        raise BrainPathError(f"path leads outside the Brain: {path!r}")
    # A symbolic link inside the Brain can point at .git/ or at the root itself.
    _check_parts(resolved.relative_to(resolved_root).parts, path)
    return resolved


def _check_parts(parts: tuple[str, ...], path: str) -> None:
    if not parts:
        raise BrainPathError(f"path names the Brain root, not a file in it: {path!r}")
    for part in parts:
        if part == "..":
            raise BrainPathError(f"path traversal is not allowed: {path!r}")
        if part.startswith("."):
            raise BrainPathError(f"dot-paths are not part of the Brain: {path!r}")
