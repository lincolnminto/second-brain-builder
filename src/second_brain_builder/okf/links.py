"""Links between files of a bundle: extract, resolve, rewrite, and remove (OKF §6)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

from second_brain_builder.okf import _markdown

_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")


def split_href(href: str) -> tuple[str, str]:
    """Split an href into its path and its ``?query#fragment`` suffix."""
    cut = len(href)
    for marker in "?#":
        position = href.find(marker)
        if position >= 0:
            cut = min(cut, position)
    return href[:cut], href[cut:]


def is_external(href: str) -> bool:
    """True for an absolute URL (``https:``, ``mailto:``) or a network-path reference (``//``)."""
    path, _ = split_href(href.strip())
    return _SCHEME_RE.match(path) is not None or path.startswith("//")


def extract_links(body: str) -> list[str]:
    """The link targets of a body, in document order, each one time.

    Inline links, autolinks, and link reference definitions count; images do not,
    because OKF §6.1 counts only ``[text](target)`` and reference-style links. Code
    spans, code blocks, and empty targets (``[text]()``) are skipped. Each href keeps
    its ``#fragment`` and has backslash escapes and character references resolved.
    """
    seen: dict[str, None] = {}
    for link in _markdown.scan(body).links:
        if link.kind != "image" and link.href:
            seen.setdefault(link.href, None)
    return list(seen)


def resolve_link(from_path: PurePosixPath, href: str) -> PurePosixPath | None:
    """Resolve an href in the file at ``from_path`` to a bundle path (OKF §6).

    A ``/``-rooted href resolves from the bundle root, any other href from the
    directory of ``from_path``. The query and fragment are dropped and percent-escapes
    are decoded. An href with only a fragment (``#notes``) resolves to ``from_path``.

    Returns:
        The path relative to the bundle root, or ``None`` for an external URL or a
        path that leads outside the bundle root.
    """
    path, _ = split_href(href.strip())
    if is_external(path):
        return None
    if not path:
        return from_path
    decoded = unquote(path)
    parts: list[str] = [] if decoded.startswith("/") else list(from_path.parent.parts)
    for segment in decoded.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(segment)
    return PurePosixPath(*parts)


def bundle_href(path: PurePosixPath) -> str:
    """The ``/``-rooted, percent-encoded href of a bundle path: ``/wiki/entities/ishmael.md``."""
    return "/" + quote(path.as_posix())


def apply_moves(path: PurePosixPath, moves: Mapping[PurePosixPath, PurePosixPath]) -> PurePosixPath:
    """The new path of ``path`` after ``moves``.

    A key can be a file or a directory. An exact match wins; else the deepest moved
    directory that contains ``path`` decides.
    """
    if path in moves:
        return moves[path]
    for parent in path.parents:
        if parent in moves and parent.parts:
            return moves[parent].joinpath(path.relative_to(parent))
    return path


def _relative_href(target: PurePosixPath, directory: PurePosixPath) -> str:
    target_parts, base_parts = target.parts, directory.parts
    common = 0
    while (
        common < len(target_parts)
        and common < len(base_parts)
        and target_parts[common] == base_parts[common]
    ):
        common += 1
    parts = [".."] * (len(base_parts) - common) + list(target_parts[common:])
    return quote("/".join(parts)) if parts else "."


def _link_target(href: str, from_path: PurePosixPath | None) -> PurePosixPath | None:
    """The bundle path of an href, or ``None`` when it is external, only a fragment, or
    relative while ``from_path`` is unknown."""
    path, _ = split_href(href)
    if not path or is_external(path):
        return None
    if path.startswith("/"):
        return resolve_link(PurePosixPath(), path)
    return None if from_path is None else resolve_link(from_path, path)


def _raw_suffix(raw: str, href: str, path: str) -> str:
    """The ``?query#fragment`` of a destination as written: ``href`` is ``raw`` unescaped.

    The suffix keeps its escapes and character references (``#a\\)b``), so it means
    the same in the new destination. The suffix is escaped again only if its ``?`` or
    ``#`` is inside the text of one character reference, which HTML5 references
    do not produce.
    """
    start = _markdown.raw_offset(raw, len(path))
    if start is None:
        return _markdown.escape_destination(href[len(path) :])
    return raw[start:]


def _new_href(
    href: str,
    raw: str,
    moves: Mapping[PurePosixPath, PurePosixPath],
    from_path: PurePosixPath | None,
) -> str | None:
    """The rewritten destination, or ``None`` when the href still points at its target.

    ``raw`` is the destination as written in the body, and ``href`` is ``raw`` unescaped.
    """
    target = _link_target(href, from_path)
    if target is None:
        return None
    new_target = apply_moves(target, moves)
    path, _ = split_href(href)
    suffix = _raw_suffix(raw, href, path)
    trailing = "/" if path.endswith("/") and new_target.parts else ""
    if path.startswith("/"):
        if new_target == target:
            return None
        return bundle_href(new_target) + trailing + suffix
    if from_path is None:
        return None
    new_from = apply_moves(from_path, moves)
    if resolve_link(new_from, path) == new_target:
        return None
    new_path = _relative_href(new_target, new_from.parent)
    if path.startswith("./") and not new_path.startswith(".."):
        new_path = "./" + new_path
    return new_path + trailing + suffix


def rewrite_links(
    body: str,
    moves: dict[PurePosixPath, PurePosixPath],
    from_path: PurePosixPath | None = None,
) -> str:
    """Point links and images at the new paths after files or directories moved.

    ``moves`` maps old bundle paths to new ones; see ``apply_moves``. ``from_path`` is
    the path of the page before the move. With it, relative hrefs are rewritten too,
    also when the page itself moved; without it, only ``/``-rooted hrefs change.
    Fragments, queries, external URLs, and code stay as they are. Only the changed
    destinations are replaced, so the rest of the body keeps its bytes.
    """
    found = _markdown.scan(body)
    edits: list[tuple[int, int, str]] = []
    for link in found.links:
        if link.kind == "autolink":
            continue
        raw = found.text[link.dest_start : link.dest_end]
        new = _new_href(link.href, raw, moves, from_path)
        if new is not None:
            edits.append((link.dest_start, link.dest_end, new))
    return apply_edits(found.text, edits)


def remove_links(
    body: str,
    targets: Iterable[PurePosixPath],
    from_path: PurePosixPath | None = None,
) -> str:
    """Turn inline links to ``targets`` into their plain text, and images into their alt text.

    Use it when pages are deleted. ``from_path`` works as in ``rewrite_links``.
    Reference-style links and code stay as they are.
    """
    target_set = set(targets)
    found = _markdown.scan(body)
    edits: list[tuple[int, int, str]] = []
    for link in found.links:
        if link.kind not in ("inline", "image"):
            continue
        target = _link_target(link.href, from_path)
        if target is not None and target in target_set:
            edits.append((link.start, link.end, found.text[link.text_start : link.text_end]))
    return apply_edits(found.text, edits)


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Replace ``text[start:end]`` for each edit. Edits that overlap an earlier edit are skipped."""
    pieces: list[str] = []
    position = 0
    for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], -edit[1])):
        if start < position:
            continue
        pieces.append(text[position:start])
        pieces.append(replacement)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)
