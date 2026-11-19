"""The tools of the Ingest loop: search, list_pages, read_page, write_page, and finish.

The tools are the only way the model changes the Brain, and they enforce every
limit in code: the model writes only Wiki Pages under ``wiki/``, cites only Sources
of the Brain, reads a page before it overwrites it, and writes at most a fixed
number of pages. What the Source text or the model asks for cannot change that, and
a tool call that the tools refuse goes back to the model as an error: it does not
stop the Ingest.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from second_brain_builder.brains import (
    WIKI_DIR,
    WIKI_DIRS,
    BrainPathError,
    is_slug,
    list_pages,
    resolve_in_brain,
)
from second_brain_builder.ingest._wiki import (
    CanonicalSource,
    bundle_paths,
    check_page,
    cited_in_order,
    clean_tags,
    merge_sources,
    one_line,
    page_links,
    render_citations,
    starts_with_frontmatter,
    strip_footnote_definitions,
)
from second_brain_builder.ingest.models import IngestEvent
from second_brain_builder.okf import (
    Frontmatter,
    OKFError,
    Page,
    PageType,
    Stamp,
    read_page,
    write_page,
)
from second_brain_builder.providers import ToolCall, ToolSpec
from second_brain_builder.search import SearchHit, SearchIndex

WRITABLE_TYPES = (PageType.SUMMARY, PageType.ENTITY, PageType.CONCEPT, PageType.COMPARISON)
"""The Wiki Page types that the model can write. Syntheses are saved answers."""

SEARCH_LIMIT = 10
"""The most hits that one search returns to the model."""

LIST_LIMIT = 200
"""The most pages that one list_pages call returns to the model."""

READ_LIMIT = 40_000
"""The most characters of a file that read_page returns to the model."""

NOTE_LIMIT = 1_000
"""The most characters of a finish note that the next part gets."""

SLUG_LIMIT = 100
"""The most characters in the slug of a page that the model writes.

``brains.slugify`` makes slugs of at most 80 characters, and a suffix such as ``-2``
can follow. Every page name then fits in the 255-byte name limit of file systems.
"""

NAME_LIMIT = 255
"""The most bytes in one segment of a path that a tool gets (``NAME_MAX`` on Linux)."""

PATH_LIMIT = 1_024
"""The most bytes in a path that a tool gets."""

_PAGE_TYPE_VALUES = [page_type.value for page_type in PageType]
_WRITABLE_VALUES = [page_type.value for page_type in WRITABLE_TYPES]

TOOL_SPECS = [
    ToolSpec(
        name="search",
        description=(
            "Full-text search over the pages of this Brain: Canonical Documents (type source) "
            "and Wiki Pages. Returns the best matches with path, type, title, and a snippet. "
            "Search before you create a page, to find an existing page for the same thing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Plain words to search for."},
                "types": {
                    "type": "array",
                    "items": {"type": "string", "enum": _PAGE_TYPE_VALUES},
                    "description": "Only return pages of these types.",
                },
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="list_pages",
        description=(
            "List the pages of this Brain with path, type, title, and description. "
            "For a Source, the line also shows its id, the footnote label to cite it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": _PAGE_TYPE_VALUES,
                    "description": "Only list pages of this type.",
                },
            },
        },
    ),
    ToolSpec(
        name="read_page",
        description=(
            "Read a Markdown file of this Brain, frontmatter included. To overwrite a page "
            "with write_page, read it first in the current part, and call write_page in a "
            "later answer, when you have its text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path from the Brain root, for example "
                    "wiki/entities/grey-heron.md.",
                },
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="write_page",
        description=(
            "Create or overwrite one Wiki Page. The path must be wiki/<directory>/<slug>.md "
            "with the directory of the type: summaries, entities, concepts, or comparisons, "
            f"and a slug of at most {SLUG_LIMIT} characters. The body is Markdown without "
            "frontmatter and without footnote definitions: cite each claim with "
            "[^source-id], and the tool adds the sources list, the footnote definitions, and "
            "the generated stamp. To overwrite an existing page, read it with read_page in an "
            "earlier answer of the current part. Returns errors to correct, or the written "
            "path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "For example wiki/concepts/tidal-cycle.md.",
                },
                "type": {"type": "string", "enum": _WRITABLE_VALUES},
                "title": {
                    "type": "string",
                    "description": "Required for a new page. Leave it out to keep the title "
                    "of an existing page.",
                },
                "description": {
                    "type": "string",
                    "description": "One sentence. Required for a new page. Leave it out to "
                    "keep the description of an existing page.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Leave it out to keep the tags of an existing page.",
                },
                "body": {"type": "string", "description": "The complete Markdown body."},
            },
            "required": ["path", "type", "body"],
        },
    ),
    ToolSpec(
        name="finish",
        description=(
            "Finish the work on the current part of the Source. The note goes to you with the "
            "next part."
        ),
        parameters={
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "What is still open, and which pages the next part should "
                    "update.",
                },
            },
        },
    ),
]
"""The tools that the model gets in every request of an Ingest."""


class ToolError(Exception):
    """A tool call that the tool refuses. The message goes back to the model."""


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _SearchArguments(_Arguments):
    query: str
    types: list[str] | None = None


class _ListPagesArguments(_Arguments):
    type: str | None = None


class _ReadPageArguments(_Arguments):
    path: str


class _WritePageArguments(_Arguments):
    path: str
    type: str
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    body: str


class _FinishArguments(_Arguments):
    note: str | None = None


class PageSearch:
    """The search index of one Brain, as the tools use it."""

    def __init__(self, index: SearchIndex, brain_id: str, root: Path) -> None:
        self._index = index
        self._brain_id = brain_id
        self._root = root

    def query(self, text: str, types: list[str] | None) -> list[SearchHit]:
        return self._index.query(self._brain_id, text, limit=SEARCH_LIMIT, types=types)

    def update(self, paths: list[PurePosixPath]) -> None:
        self._index.update(self._brain_id, self._root, paths)


class IngestTools:
    """The tool calls of one Ingest, and the state that their rules need.

    Call ``start_part`` before the first model request of each part, and
    ``start_request`` before each model request. A page counts as read only in the
    part where the model read or wrote it, because the model gets a new conversation
    for each part and no longer has the page text. A page that the model read counts
    as read from the next request on, when the model has the text: the tool calls of
    one answer run before the model sees their results.
    """

    def __init__(
        self,
        *,
        root: Path,
        source: CanonicalSource,
        summary_path: PurePosixPath,
        catalog: dict[str, CanonicalSource],
        search: PageSearch,
        model_name: str,
        page_limit: int,
        on_event: Callable[[IngestEvent], None],
    ) -> None:
        self._root = root
        self._source = source
        self._summary_path = summary_path
        self._catalog = catalog
        self._search = search
        self._generated_by = f"sbb/{model_name}"
        self._page_limit = page_limit
        self._on_event = on_event
        self._paths = bundle_paths(root)
        self._known: set[PurePosixPath] = set()
        """The pages whose current text the model has in this part."""
        self._reads_in_answer: set[PurePosixPath] = set()
        """The pages read in the current answer: the model gets their text in the next request."""
        self._partly_read: set[PurePosixPath] = set()
        """The pages too long for read_page in this part: the model cannot overwrite them."""
        self._originals: dict[PurePosixPath, bytes | None] = {}
        self._part = 0
        self._part_writes = 0
        self._part_written: set[PurePosixPath] = set()
        self.recent_written: list[PurePosixPath] = []
        self.written: dict[PurePosixPath, str] = {}
        """Each page written in this Ingest, with its title, in the order of the first write."""
        self.finished = False
        """True when the model called finish in the current part, and finish accepted it."""
        self.note: str | None = None
        """The note of the last accepted finish call in the current part."""
        self._handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "search": self._call_search,
            "list_pages": self._call_list_pages,
            "read_page": self._call_read_page,
            "write_page": self._call_write_page,
            "finish": self._call_finish,
        }

    def start_part(self, number: int) -> None:
        """Start part ``number`` (1-based): no page counts as read, and finish is open again."""
        self._originals = {}
        self._part = number
        self._part_writes = 0
        self._part_written = set()
        self._known = set()
        self._reads_in_answer = set()
        self._partly_read = set()
        self.finished = False
        self.note = None

    def rollback_part(self) -> None:
        """Discard only the writes of the unfinished Part, including their search entries."""
        for path, content in self._originals.items():
            if content is None:
                (self._root / path).unlink(missing_ok=True)
            else:
                (self._root / path).write_bytes(content)
        self._search.update(list(self._originals))

    def start_request(self) -> None:
        """Start a model request: the pages read since the last request now count as read."""
        self._known |= self._reads_in_answer
        self._reads_in_answer = set()

    @property
    def page_limit_reached(self) -> bool:
        """True when the Ingest wrote as many distinct pages as it may."""
        return self._part_writes >= self._page_limit

    def summary_exists(self) -> bool:
        """True when the summary page of the Source exists."""
        return _occupied(self._root, self._summary_path)

    def call(self, call: ToolCall) -> str:
        """Run one tool call and return the text for the model. A refusal starts with ``Error:``."""
        handler = self._handlers.get(call.name)
        if handler is None:
            return (
                f"Error: there is no tool {call.name!r}. Use one of: {', '.join(self._handlers)}."
            )
        try:
            return handler(call.arguments)
        except ValidationError as exc:
            return f"Error: invalid arguments for {call.name}: {_describe(exc)}"
        except ToolError as exc:
            return f"Error: {exc}"

    # --- search, list_pages, read_page -----------------------------------------

    def _call_search(self, arguments: dict[str, Any]) -> str:
        args = _SearchArguments.model_validate(arguments)
        types = _check_types(args.types) if args.types else None
        if not args.query.strip():
            raise ToolError("the query is empty.")
        hits = self._search.query(args.query, types)
        if not hits:
            return "No pages match."
        lines: list[str] = []
        for hit in hits:
            lines.append(f"- {_page_line(hit.path, hit.type, hit.title, None)}")
            if hit.snippet:
                lines.append(f"  {one_line(hit.snippet)}")
        return "\n".join(lines)

    def _call_list_pages(self, arguments: dict[str, Any]) -> str:
        args = _ListPagesArguments.model_validate(arguments)
        if args.type is not None:
            _check_types([args.type])
        pages = [
            page for page in list_pages(self._root) if args.type is None or page.type == args.type
        ]
        if not pages:
            return "No pages."
        lines = [
            f"- {_page_line(page.path, page.type, page.title, page.description)}"
            for page in pages[:LIST_LIMIT]
        ]
        if len(pages) > LIST_LIMIT:
            lines.append(
                f"({len(pages) - LIST_LIMIT} more pages. Use search, or list_pages with a type.)"
            )
        return "\n".join(lines)

    def _call_read_page(self, arguments: dict[str, Any]) -> str:
        args = _ReadPageArguments.model_validate(arguments)
        path = _bundle_path(args.path)
        if path.suffix != ".md":
            raise ToolError("read_page reads Markdown files (.md) only.")
        try:
            file = resolve_in_brain(self._root, str(path))
        except BrainPathError as exc:
            raise ToolError(f"{args.path!r} is not a file of this Brain: {exc}") from None
        except OSError as exc:
            raise _path_error(path, exc) from None
        try:
            is_file = file.is_file()
        except OSError as exc:
            raise _path_error(path, exc) from None
        if not is_file:
            raise ToolError(f"/{path} does not exist.")
        try:
            text = file.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            raise ToolError(f"/{path} cannot be read as UTF-8 text.") from None
        if len(text) > READ_LIMIT:
            self._partly_read.add(path)
            return (
                f"{text[:READ_LIMIT]}\n\n[read_page: the file has {len(text)} characters, and "
                f"only the first {READ_LIMIT} are shown. You cannot overwrite it.]"
            )
        self._reads_in_answer.add(path)
        return text

    # --- write_page ------------------------------------------------------------

    def _call_write_page(self, arguments: dict[str, Any]) -> str:
        args = _WritePageArguments.model_validate(arguments)
        page_type = _writable_type(args.type)
        path = _wiki_path(args.path, page_type)
        if page_type is PageType.SUMMARY:
            if path != self._summary_path:
                raise ToolError(
                    f"the summary of this Source is /{self._summary_path}. You cannot write "
                    "the summaries of other Sources."
                )
        elif len(path.stem) > SLUG_LIMIT:
            raise ToolError(
                f"the slug has {len(path.stem)} characters, and the limit is {SLUG_LIMIT}. "
                "Make a shorter slug from the main words of the title."
            )
        if self.page_limit_reached:
            raise ToolError(
                f"this Part wrote {self._page_limit} pages, the limit for one Part. "
                "Further writes are refused. Call finish to complete this Part."
            )
        try:
            exists = _occupied(self._root, path)
        except OSError as exc:
            raise _path_error(path, exc) from None
        if exists and path not in self._known:
            raise ToolError(self._unread_problem(path))
        existing = self._existing_frontmatter(path) if exists else None
        page = self._build_page(path, page_type, args, existing)
        errors, warnings = check_page(page, self._paths | {path})
        if errors:
            raise ToolError(
                "the page was not written. Correct these problems:\n"
                + "\n".join(f"- {error}" for error in errors)
            )
        if path not in self._originals:
            self._originals[path] = (self._root / path).read_bytes() if exists else None
        try:
            write_page(self._root, page)
        except ValueError as exc:
            raise ToolError(f"/{path} cannot be written: {exc}") from None
        return self._after_write(page, exists, warnings)

    def _unread_problem(self, path: PurePosixPath) -> str:
        """Why the model cannot overwrite the existing page ``path`` now."""
        if path in self._partly_read:
            return (
                f"/{path} is longer than read_page shows ({READ_LIMIT} characters), so you "
                "cannot overwrite it. Put the new facts on another page."
            )
        if path in self._reads_in_answer:
            return (
                f"you read /{path} in this same answer, so you did not see its text yet. "
                "Call write_page in a later answer, and keep its content and citations."
            )
        return (
            f"/{path} exists. Read it with read_page in this part before you overwrite it, "
            "and keep its content and citations."
        )

    def _existing_frontmatter(self, path: PurePosixPath) -> Frontmatter | None:
        """The frontmatter of the page at ``path``, or None when it cannot be read."""
        try:
            return read_page(self._root, path).frontmatter
        except (OKFError, ValueError):
            return None
        except OSError as exc:
            raise ToolError(f"/{path} cannot be read: {exc}") from None

    def _build_page(
        self,
        path: PurePosixPath,
        page_type: PageType,
        args: _WritePageArguments,
        existing: Frontmatter | None,
    ) -> Page:
        title = one_line(args.title) if args.title is not None else None
        description = one_line(args.description) if args.description is not None else None
        if not title and existing is not None:
            title = existing.title
        if not description and existing is not None:
            description = existing.description
        if not title:
            raise ToolError("a new page needs a title.")
        if not description:
            raise ToolError("a new page needs a description: one sentence.")
        tags = clean_tags(args.tags) if args.tags is not None else None
        if tags is None:
            tags = list(existing.tags) if existing is not None else []

        body = args.body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not body.strip():
            raise ToolError("the body is empty.")
        if starts_with_frontmatter(body):
            raise ToolError(
                "the body starts with a --- line. Write only the Markdown body: the tool makes "
                "the frontmatter."
            )
        body = strip_footnote_definitions(body).rstrip()
        labels = cited_in_order(body)
        self._check_citations(page_type, labels, existing)
        sources = merge_sources(labels, self._catalog, existing.sources if existing else [])
        stamp = Stamp(by=self._generated_by, at=datetime.now(timezone.utc).replace(microsecond=0))
        update: dict[str, Any] = {
            "type": page_type.value,
            "title": title,
            "description": description,
            "tags": tags,
            "sources": sources,
            "generated": stamp,
        }
        frontmatter = (
            existing.model_copy(update=update)
            if existing is not None
            else Frontmatter.model_validate(update)
        )
        return Page(
            path=path,
            frontmatter=frontmatter,
            body=render_citations(body, labels, {ref.id: ref for ref in sources}),
        )

    def _check_citations(
        self, page_type: PageType, labels: list[str], existing: Frontmatter | None
    ) -> None:
        source_id = self._source.id
        retained = {ref.id for ref in existing.sources} if existing is not None else set()
        unknown = [
            label for label in labels if label not in self._catalog and label not in retained
        ]
        if unknown:
            listed = ", ".join(f"[^{label}]" for label in unknown)
            raise ToolError(
                f"the page was not written. These footnotes are not ids of Sources in this "
                f"Brain: {listed}. Cite this Source with [^{source_id}]; find the ids of other "
                "Sources with list_pages (type source)."
            )
        if not labels:
            raise ToolError(
                f"the page cites no Source. Cite each claim with a footnote, for example "
                f"[^{source_id}]."
            )
        if page_type is PageType.SUMMARY and source_id not in labels:
            raise ToolError(f"the summary must cite this Source with [^{source_id}].")
        if existing is not None:
            dropped = [
                ref.id for ref in existing.sources if ref.id != source_id and ref.id not in labels
            ]
            if dropped:
                listed = ", ".join(f"[^{label}]" for label in dict.fromkeys(dropped))
                raise ToolError(
                    f"the page was not written. The existing page cites {listed}: other Sources "
                    "that this Ingest cannot remove. Keep those claims and their citations."
                )

    def _after_write(self, page: Page, existed: bool, warnings: list[str]) -> str:
        frontmatter = page.frontmatter
        if frontmatter is None:  # _build_page always sets it
            raise ValueError(f"{page.path} has no frontmatter")
        title = frontmatter.title or page.path.stem
        self._paths.add(page.path)
        self._known.add(page.path)
        self.recent_written.append(page.path)
        self.recent_written = self.recent_written[-300:]
        self.written[page.path] = title  # a key that exists keeps its place
        self._part_written.add(page.path)
        self._part_writes += 1
        self._search.update([page.path])
        self._on_event(
            IngestEvent(
                kind="page_written",
                data={
                    "path": str(page.path),
                    "title": title,
                    "type": frontmatter.type,
                    "links": [str(link) for link in page_links(page.path, page.body)],
                },
            )
        )
        result = f"Wrote /{page.path} ({'updated' if existed else 'new'} page)."
        if warnings:
            result += " Warnings:\n" + "\n".join(f"- {warning}" for warning in warnings)
        return result

    # --- finish ----------------------------------------------------------------

    def _call_finish(self, arguments: dict[str, Any]) -> str:
        args = _FinishArguments.model_validate(arguments)
        if self._part == 1 and self._summary_path not in self.written:
            raise ToolError(
                f"write the summary page /{self._summary_path} (type summary) before you "
                "finish the first part."
            )
        self.finished = True
        note = one_line(args.note or "")
        self.note = note[:NOTE_LIMIT] if note else None
        return "The part is finished."


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in exc.errors()
    )


def _check_types(types: list[str]) -> list[str]:
    unknown = [value for value in types if value not in _PAGE_TYPE_VALUES]
    if unknown:
        raise ToolError(
            f"unknown page types: {', '.join(unknown)}. Use: {', '.join(_PAGE_TYPE_VALUES)}."
        )
    return types


def _writable_type(value: str) -> PageType:
    if value not in _WRITABLE_VALUES:
        raise ToolError(
            f"write_page cannot write pages of type {value!r}. Use one of: "
            f"{', '.join(_WRITABLE_VALUES)}."
        )
    return PageType(value)


def _bundle_path(text: str) -> PurePosixPath:
    """A path from a tool argument: from the Brain root, with or without one leading ``/``.

    The path and each of its segments must fit the limits of file systems
    (``PATH_LIMIT``, ``NAME_LIMIT``), so a long path is refused here and not by an
    ``OSError``.
    """
    value = text.strip()
    if value.startswith("/"):
        value = value[1:]
    path = PurePosixPath(value)
    if not value or "\\" in value or "\x00" in value or path.is_absolute():
        raise ToolError(f"{_shorten(text)!r} is not a path from the Brain root.")
    if len(value.encode("utf-8")) > PATH_LIMIT or any(
        len(part.encode("utf-8")) > NAME_LIMIT for part in path.parts
    ):
        raise ToolError(
            f"the path {_shorten(value)!r} is too long: a path has at most {PATH_LIMIT} bytes, "
            f"and each name in it at most {NAME_LIMIT}."
        )
    return path


def _shorten(text: str, limit: int = 120) -> str:
    """``text`` cut to ``limit`` characters, with ``...`` when it was cut."""
    return text if len(text) <= limit else f"{text[:limit]}..."


def _path_error(path: PurePosixPath, exc: OSError) -> ToolError:
    """A tool error for an ``OSError`` that the file system gave for ``path``.

    The message names the bundle path only, never the location of the Brain clone.
    """
    reason = exc.strerror or type(exc).__name__
    return ToolError(f"/{_shorten(str(path))} cannot be used: {reason}.")


def _wiki_path(text: str, page_type: PageType) -> PurePosixPath:
    """The path of a Wiki Page that the model can write: ``wiki/<type-dir>/<slug>.md``."""
    path = _bundle_path(text)
    directory = WIKI_DIRS[page_type]
    parts = path.parts
    if not parts or parts[0] != WIKI_DIR.name:
        raise ToolError(
            "you can write only Wiki Pages under wiki/. sources/, index.md, log.md, AGENTS.md, "
            "CLAUDE.md, and dot-paths are read-only."
        )
    slug = parts[-1].removesuffix(".md")
    if (
        len(parts) != 3
        or parts[1] != directory
        or not parts[-1].endswith(".md")
        or not is_slug(slug)
    ):
        raise ToolError(
            f"a {page_type.value} page must be at wiki/{directory}/<slug>.md, where the slug "
            "has only lowercase ASCII letters, digits, and single hyphens."
        )
    return path


def _occupied(root: Path, path: PurePosixPath) -> bool:
    file = root.joinpath(*path.parts)
    return file.is_symlink() or file.exists()


def _page_line(path: PurePosixPath, page_type: str, title: str, description: str | None) -> str:
    line = f"/{path} | {page_type} | {one_line(title)}"
    if page_type == PageType.SOURCE.value:
        line += f" | id: {path.stem}"
    if description:
        line += f" | {one_line(description)}"
    return line
