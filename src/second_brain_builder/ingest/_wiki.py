"""Helpers that Ingest, removal, and saved answers share: Sources, citations, and links."""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import SOURCES_DIR, is_slug
from second_brain_builder.brains.pages import _markdown_files
from second_brain_builder.okf import (
    IssueCode,
    OKFError,
    Page,
    PageType,
    SourceRef,
    bundle_href,
    cited_labels,
    extract_links,
    footnote_labels,
    parse_page,
    remove_citations,
    resolve_link,
    validate_page,
)
from second_brain_builder.okf.pages import bundle_file

_log = logging.getLogger(__name__)

_FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\s\[\]|\\]+\]:")
"""``[^label]:``, which starts a footnote definition when it starts a block line.

okf reads any label character but a space and ``]``. This pattern also leaves out
the other whitespace, ``|`` and ``\\`` (they change how a table row splits into
cells, so a new label without them keeps the block structure), and ``[`` (so a
search takes linear time).
"""

_LINK_TEXT_SPECIAL_RE = re.compile(r"([\\\[\]`*_<>])")

UNCLOSED_BLOCK_PROBLEM = (
    "The body ends inside a code block or an HTML block (for example a ``` fence "
    "without its closing line), so the footnote definitions that are added after the "
    "body are not read as definitions. Close the block."
)
"""The problem of a body whose generated footnote definitions okf does not find."""


@dataclass(frozen=True)
class CanonicalSource:
    """A Source of the Brain, as a Wiki Page cites it.

    ``id`` is the Source slug and the footnote label. ``path`` is its Canonical
    Document, for example ``sources/books/dune/dune.md``.
    """

    id: str
    path: PurePosixPath
    title: str

    @property
    def resource(self) -> str:
        """The ``/``-rooted href of the Canonical Document, for ``sources[].resource``."""
        return bundle_href(self.path)


def is_canonical_path(path: PurePosixPath) -> bool:
    """True for ``sources/<folder>/.../<slug>/<slug>.md``, the path of a Canonical Document."""
    parts = path.parts
    return (
        len(parts) >= 3
        and parts[0] == SOURCES_DIR.name
        and path.suffix == ".md"
        and path.stem == path.parent.name
        and is_slug(path.stem)
    )


def source_catalog(root: Path) -> dict[str, CanonicalSource]:
    """Every Source of the Brain at ``root`` that has a Canonical Document, by id."""
    catalog: dict[str, CanonicalSource] = {}
    for path in _markdown_files(root):
        if not is_canonical_path(path):
            continue
        try:
            page = _read_source_header(root, path)
        except (OKFError, OSError, ValueError) as exc:
            _log.warning("Skipped %s in %s: %s", path, root, exc)
            continue
        if page.frontmatter is not None and page.frontmatter.type == PageType.SOURCE.value:
            title = " ".join((page.frontmatter.title or "").split()) or path.stem
            catalog.setdefault(
                path.stem,
                CanonicalSource(id=path.stem, path=path, title=title),
            )
    return catalog


def _read_source_header(root: Path, path: PurePosixPath) -> Page:
    """Read only frontmatter, without decoding or parsing the document body."""
    bundle_file(root, path)
    with contextlib.ExitStack() as stack:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, descriptor)
        for index, part in enumerate(path.parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if index < len(path.parts) - 1:
                flags |= os.O_DIRECTORY
            descriptor = os.open(part, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        with os.fdopen(os.dup(descriptor), "rb") as file:

            def header_line() -> bytes:
                line = bytearray()
                while byte := file.read(1):
                    if byte == b"\r":
                        if file.peek(1).startswith(b"\n"):
                            file.read(1)
                        return bytes(line) + b"\n"
                    line.extend(byte)
                    if byte == b"\n":
                        break
                return bytes(line)

            first = header_line().removeprefix(b"\xef\xbb\xbf")
            header = [first]
            if first.rstrip(b"\r\n") == b"---":
                while line := header_line():
                    header.append(line)
                    if line.rstrip(b"\r\n") == b"---":
                        break
            try:
                return parse_page(path, b"".join(header).decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise OKFError(f"{path} frontmatter is not UTF-8 text") from exc


def bundle_paths(root: Path) -> set[PurePosixPath]:
    """The bundle paths of the regular files under ``root``, without dot-paths and symbolic links.

    ``okf.validate_page`` checks links against this set.
    """
    found: set[PurePosixPath] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                found.add(PurePosixPath(entry.relative_to(root).as_posix()))
    return found


def one_line(text: str) -> str:
    """Collapse all whitespace, line endings included, to single spaces."""
    return " ".join(text.split())


def clean_tags(tags: Iterable[str]) -> list[str]:
    """Tags on one line each, without empty tags and repeats, in their first order."""
    return list(dict.fromkeys(tag for tag in (one_line(tag) for tag in tags) if tag))


def starts_with_frontmatter(body: str) -> bool:
    """True when the first line of ``body`` is ``---``: the writer put frontmatter in the body."""
    return body.split("\n", 1)[0].rstrip() == "---"


def fresh_name(text: str, base: str) -> str:
    """``base``, or ``base-2``, ``base-3``, ...: the first one that does not occur in ``text``."""
    name = base
    number = 1
    while name in text:
        number += 1
        name = f"{base}-{number}"
    return name


def strip_footnote_definitions(body: str) -> str:
    """Remove every footnote definition block (``[^label]: ...``) that okf finds in ``body``.

    Definitions in block quotes and list items go too, each with the lines that okf
    counts as part of it. Code does not change. The writer of a body cites with
    footnote references only; ``render_citations`` adds the definitions.

    A definition whose label has whitespace, ``[``, ``|``, or ``\\`` can stay, but
    such a label is never a Source id, so OKF validation refuses a page that keeps
    one.
    """
    markers = list(_FOOTNOTE_MARKER_RE.finditer(body))
    if not markers:
        return body
    # okf has no public function for the definitions alone: footnote_labels also
    # gives the labels of references. So each marker first gets a label of its
    # own, and okf reads it as a definition (a label that no reference has) or as
    # a reference that a colon follows.
    prefix = fresh_name(body, "sbb-footnote")
    probe = _replace_spans(body, markers, [f"[^{prefix}-{n}]:" for n in range(len(markers))])
    defined = footnote_labels(probe) - cited_labels(probe)
    if not any(f"{prefix}-{n}" in defined for n in range(len(markers))):
        return body
    marked = _replace_spans(
        body,
        markers,
        [
            f"[^{prefix}]:" if f"{prefix}-{n}" in defined else marker.group()
            for n, marker in enumerate(markers)
        ],
    )
    return remove_citations(marked, prefix)


def _replace_spans(text: str, matches: list[re.Match[str]], replacements: list[str]) -> str:
    """``text`` with each match, in order and without overlaps, replaced."""
    pieces: list[str] = []
    position = 0
    for match, replacement in zip(matches, replacements, strict=True):
        pieces += [text[position : match.start()], replacement]
        position = match.end()
    pieces.append(text[position:])
    return "".join(pieces)


def cited_in_order(body: str) -> list[str]:
    """The footnote labels that the body cites, in the order of their first reference."""
    labels = cited_labels(body)
    return sorted(labels, key=lambda label: (_first_position(body, label), label))


def _first_position(body: str, label: str) -> int:
    position = body.find(f"[^{label}]")
    return position if position >= 0 else len(body)


def link_text(text: str) -> str:
    """Text for a Markdown link label, on one line, with syntax characters escaped."""
    return _LINK_TEXT_SPECIAL_RE.sub(r"\\\1", one_line(text))


def render_citations(
    body: str, labels: list[str], catalog: Mapping[str, CanonicalSource | SourceRef]
) -> str:
    """``body`` followed by one footnote definition per label: ``[^id]: [Title](/sources/...)``.

    Every label must be in ``catalog``.
    """
    definitions = [
        f"[^{label}]: [{link_text(catalog[label].title or label)}]({catalog[label].resource})"
        for label in labels
    ]
    text = body.strip("\n")
    if not definitions:
        return text + "\n"
    return f"{text}\n\n" + "\n".join(definitions) + "\n"


def check_page(page: Page, existing_paths: set[PurePosixPath]) -> tuple[list[str], list[str]]:
    """The errors and the warnings of OKF validation for a page with generated citations.

    The body of ``page`` comes from ``render_citations``, so every cited label has a
    generated definition. When okf does not find one, the body left a block open
    and the definitions became part of it: that is an error here, not a warning.
    """
    errors: list[str] = []
    warnings: list[str] = []
    unclosed = False
    for issue in validate_page(page, existing_paths):
        if issue.code == IssueCode.UNDEFINED_FOOTNOTE:
            unclosed = True
        elif issue.severity == "error":
            errors.append(issue.message)
        else:
            warnings.append(issue.message)
    if unclosed:
        errors.append(UNCLOSED_BLOCK_PROBLEM)
    return errors, warnings


def merge_sources(
    labels: list[str],
    catalog: dict[str, CanonicalSource],
    existing: list[SourceRef],
) -> list[SourceRef]:
    """The ``sources`` list for a page that cites ``labels``.

    Entries of ``existing`` that are still cited stay first, in their order, with
    their other keys (``author``, extension keys); their ``resource`` and ``title``
    come from the Canonical Document again. New labels follow in citation order.
    A Source missing from ``catalog`` keeps its existing entry.
    """
    wanted = set(labels)
    merged: list[SourceRef] = []
    seen: set[str] = set()
    for ref in existing:
        if ref.id in wanted and ref.id not in seen:
            source = catalog.get(ref.id)
            merged.append(
                ref.model_copy(update={"resource": source.resource, "title": source.title})
                if source is not None
                else ref
            )
            seen.add(ref.id)
    for label in labels:
        if label not in seen:
            source = catalog[label]
            merged.append(SourceRef(id=label, resource=source.resource, title=source.title))
            seen.add(label)
    return merged


def page_links(path: PurePosixPath, body: str) -> list[PurePosixPath]:
    """The bundle paths that ``body`` links to, citations included, without ``path`` itself."""
    targets: dict[PurePosixPath, None] = {}
    for href in extract_links(body):
        target = resolve_link(path, href)
        if target is not None and target.parts and target != path:
            targets.setdefault(target, None)
    return list(targets)
