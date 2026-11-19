"""Read and write OKF pages: frontmatter split, parse, and canonical render (OKF §4)."""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from second_brain_builder.okf import _yaml
from second_brain_builder.okf._markdown import normalize_newlines
from second_brain_builder.okf.models import (
    FRONTMATTER_KEYS,
    Frontmatter,
    OKFError,
    Page,
    SourceRef,
    Stamp,
)

_FENCE = "---"
_BYTE_ORDER_MARK = "\ufeff"
_SOURCE_REF_FIELDS = tuple(name for name in SourceRef.model_fields if name != "extensions")
_STAMP_FIELDS = tuple(name for name in Stamp.model_fields if name != "extensions")


def normalize_text(text: str) -> str:
    """File text without a byte order mark and with ``\\n`` line endings only."""
    return normalize_newlines(text.removeprefix(_BYTE_ORDER_MARK))


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a file into its frontmatter block and the rest.

    The file must start with a line that is exactly ``---``, and the block ends at
    the next line that is exactly ``---`` (OKF §4). Without both lines there is no
    frontmatter, and the whole text is the rest.
    """
    lines = text.split("\n")
    if lines[0] != _FENCE:
        return None, text
    for index in range(1, len(lines)):
        if lines[index] == _FENCE:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, text


def parse_page(path: PurePosixPath, text: str) -> Page:
    """Parse the text of one Markdown file.

    Values are read as OKF §11.2 tells consumers to: null, empty strings, and empty
    lists are absent; one item where a list is expected is a list with one item; a
    date only is the start of that day in UTC. Unknown keys go to ``extensions``.

    Raises:
        OKFError: The frontmatter is not a YAML mapping, or a key that OKF defines
            has a value of the wrong shape (for example a ``sources`` entry without
            ``id`` or ``resource``, or a timestamp that is not RFC 3339).
    """
    text = normalize_text(text)
    block, rest = split_frontmatter(text)
    if block is None:
        return Page(path=path, frontmatter=None, body=text)
    mapping = _yaml.load_mapping(block, first_line=2)
    return Page(path=path, frontmatter=frontmatter_from_mapping(mapping), body=rest.lstrip("\n"))


def frontmatter_from_mapping(data: dict[str, Any]) -> Frontmatter:
    """Build a ``Frontmatter`` from a loaded YAML mapping.

    Raises:
        OKFError: A key that OKF defines has a value of the wrong shape.
    """
    fields: dict[str, Any] = {"type": ""}
    extensions: dict[str, Any] = {}
    for key, value in data.items():
        if key not in FRONTMATTER_KEYS:
            extensions[key] = value
        elif not _is_empty(value):
            fields[key] = value
    for key in ("type", "title", "description", "resource", "status", "superseded_by"):
        if key in fields:
            fields[key] = _text(fields[key], key)
    if "tags" in fields:
        fields["tags"] = [
            _text(tag, "tags") for tag in _as_list(fields["tags"]) if not _is_empty(tag)
        ]
    if "sources" in fields:
        fields["sources"] = [
            _split_known(entry, _SOURCE_REF_FIELDS, f"sources[{index}]")
            for index, entry in enumerate(_as_list(fields["sources"]))
        ]
    if "generated" in fields:
        fields["generated"] = _split_known(fields["generated"], _STAMP_FIELDS, "generated")
    if "verified" in fields:
        fields["verified"] = [
            _split_known(entry, _STAMP_FIELDS, f"verified[{index}]")
            for index, entry in enumerate(_as_list(fields["verified"]))
        ]
    fields["extensions"] = extensions
    try:
        return Frontmatter.model_validate(fields)
    except ValidationError as exc:
        raise OKFError(_describe(exc)) from exc


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _text(value: Any, key: str) -> str:
    """A string value. Numbers and booleans become their YAML text: ``title: 1984``."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise OKFError(f"{key} must be a string, not a {type(value).__name__}")


def _split_known(entry: Any, known: tuple[str, ...], where: str) -> dict[str, Any]:
    """Split a nested mapping into its known keys and ``extensions``."""
    if not isinstance(entry, dict):
        raise OKFError(f"{where} must be a mapping")
    result: dict[str, Any] = {}
    extensions: dict[str, Any] = {}
    for key, value in entry.items():
        if key not in known:
            extensions[key] = value
        elif not _is_empty(value):
            result[key] = value if key == "at" else _text(value, f"{where}.{key}")
    result["extensions"] = extensions
    return result


def _describe(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "frontmatter"
        problems.append(f"{location}: {error['msg']}")
    return "invalid frontmatter: " + "; ".join(problems)


def render_page(page: Page) -> str:
    """Render a page as file text. The output is canonical: the same page gives the same text.

    Keys come in a fixed order (``type``, ``title``, ``description``, ``resource``,
    ``tags``, ``sources``, ``generated``, ``verified``, ``status``, ``superseded_by``,
    ``stale_after``, then ``extensions`` in their own order). Absent and empty values
    are left out. One blank line separates the frontmatter from the body, and the
    text ends with one newline. A body that ends with one newline and does not start
    with one reads back unchanged.

    Raises:
        ValueError: The page has no frontmatter, but its body starts with a block
            that ``parse_page`` would read as frontmatter (a line ``---``, and a
            later line ``---``). No file has that page.
    """
    body = normalize_newlines(page.body)
    if page.frontmatter is None:
        content = body.rstrip("\n")
        if split_frontmatter(normalize_text(content))[0] is not None:
            raise ValueError(
                f"{page.path}: a page without frontmatter cannot have a body that starts "
                "with a frontmatter block (a --- line, and a later --- line)"
            )
        return content + "\n" if content else ""
    head = f"{_FENCE}\n{_yaml.dump_mapping(frontmatter_to_mapping(page.frontmatter))}{_FENCE}\n"
    content = body.strip("\n")
    return f"{head}\n{content}\n" if content else head


def frontmatter_to_mapping(frontmatter: Frontmatter) -> CommentedMap:
    """The canonical YAML node for a frontmatter."""
    mapping = CommentedMap()
    if frontmatter.type:
        mapping["type"] = _yaml.scalar(frontmatter.type)
    if frontmatter.title:
        mapping["title"] = _yaml.scalar(frontmatter.title)
    if frontmatter.description:
        mapping["description"] = _yaml.scalar(frontmatter.description)
    if frontmatter.resource:
        mapping["resource"] = _yaml.scalar(frontmatter.resource)
    tags = [tag for tag in frontmatter.tags if tag]
    if tags:
        mapping["tags"] = _yaml.flow_seq(tags)
    if frontmatter.sources:
        mapping["sources"] = CommentedSeq(_source_node(ref) for ref in frontmatter.sources)
    if frontmatter.generated is not None:
        mapping["generated"] = _stamp_node(frontmatter.generated)
    if frontmatter.verified:
        mapping["verified"] = CommentedSeq(_stamp_node(stamp) for stamp in frontmatter.verified)
    if frontmatter.status:
        mapping["status"] = _yaml.scalar(frontmatter.status)
    if frontmatter.superseded_by:
        mapping["superseded_by"] = _yaml.scalar(frontmatter.superseded_by)
    if frontmatter.stale_after is not None:
        mapping["stale_after"] = _yaml.format_timestamp(frontmatter.stale_after)
    for key, value in frontmatter.extensions.items():
        mapping[_yaml.key_node(key)] = _yaml.to_node(value)
    return mapping


def _source_node(ref: SourceRef) -> CommentedMap:
    node = CommentedMap()
    node["id"] = _yaml.scalar(ref.id)
    node["resource"] = _yaml.scalar(ref.resource)
    if ref.title:
        node["title"] = _yaml.scalar(ref.title)
    if ref.author:
        node["author"] = _yaml.scalar(ref.author)
    for key, value in ref.extensions.items():
        node[_yaml.key_node(key)] = _yaml.to_node(value)
    return node


def _stamp_node(stamp: Stamp) -> CommentedMap:
    return _yaml.flow_map({"by": stamp.by, "at": stamp.at, **stamp.extensions})


def bundle_file(root: Path, path: PurePosixPath) -> Path:
    """The file for a bundle path, checked to stay inside the bundle.

    No part of the path below ``root`` can be a symbolic link. A link inside the
    bundle can point anywhere: at ``.git/config``, at ``index.md``, or out of the
    bundle. A Brain repo from GitHub can carry such links, because git checks them
    out as links. Parts that do not exist yet are allowed: ``write_page`` creates them.

    Raises:
        ValueError: The path is absolute, empty, has a ``..`` or dot segment (dot
            paths are not part of a bundle, OKF §3.2), or goes through a symbolic link.
    """
    if path.is_absolute() or not path.parts or any(part.startswith(".") for part in path.parts):
        raise ValueError(f"not a path inside the bundle: {path}")
    target = root
    for part in path.parts:
        target = target / part
        try:
            is_link = target.is_symlink()
        except OSError as exc:  # for example a name that is too long
            raise ValueError(f"not a path inside the bundle: {path}") from exc
        if is_link:
            raise ValueError(f"path goes through a symbolic link: {path}")
    return target


def read_page(root: Path, path: PurePosixPath) -> Page:
    """Read and parse ``root / path``.

    Raises:
        FileNotFoundError: The file does not exist.
        ValueError: The path is not a path inside the bundle (see ``bundle_file``).
        OKFError: The file is not UTF-8, or its frontmatter cannot be read.
    """
    bundle_file(root, path)
    # Open each component relative to its parent descriptor. O_NOFOLLOW closes
    # the gap between path validation and reading, including directory swaps.
    with contextlib.ExitStack() as stack:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, parent)
        for index, part in enumerate(path.parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if index < len(path.parts) - 1:
                flags |= os.O_DIRECTORY
            try:
                descriptor = os.open(part, flags, dir_fd=parent)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError(
                        f"path goes through a symbolic link or non-directory: {path}"
                    ) from exc
                raise
            stack.callback(os.close, descriptor)
            parent = descriptor
        with os.fdopen(os.dup(descriptor), "rb") as file:
            data = file.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OKFError(f"{path} is not UTF-8 text") from exc
    return parse_page(path, text)


def write_page(root: Path, page: Page) -> None:
    """Write ``render_page(page)`` to ``root / page.path``, with UTF-8 and ``\\n`` line endings.

    Parent directories are created as needed. The text goes to a temporary file in
    the same directory first (``.okf-<hex>.tmp``, a dot-path), which then replaces
    the page in one step. A reader sees the old page or the new page, never a part
    of one. After a crash the page is the old one or the new one, and the temporary
    file can stay behind. An existing page keeps its file mode.

    Raises:
        ValueError: The page path is not a path inside the bundle (see ``bundle_file``),
            or the page cannot be written as a file (see ``render_page``).
    """
    target = bundle_file(root, page.path)
    data = render_page(page).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    # A dot name: OKF and the Brain code skip dot-paths, so no reader takes the
    # temporary file for a page. It is short, so a long page name still fits.
    temporary = target.with_name(f".okf-{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        # A new page keeps the mode that the umask gives.
        with contextlib.suppress(FileNotFoundError):
            temporary.chmod(stat.S_IMODE(target.stat().st_mode))
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
