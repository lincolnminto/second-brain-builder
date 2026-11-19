"""The Ingest loop: the model reads a Canonical Document part by part and writes Wiki Pages."""

from __future__ import annotations

import secrets
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import BrainLayout
from second_brain_builder.ingest._prompts import (
    CUT_OFF_MESSAGE,
    NUDGE_MESSAGE,
    part_message,
    system_prompt,
)
from second_brain_builder.ingest._tools import TOOL_SPECS, IngestTools, PageSearch
from second_brain_builder.ingest._wiki import CanonicalSource, is_canonical_path, source_catalog
from second_brain_builder.ingest.chunks import split_into_chunks
from second_brain_builder.ingest.models import IngestEvent, IngestResult, IngestStop
from second_brain_builder.okf import PageType, read_page
from second_brain_builder.providers import ChatModel, Message, StopReason, Usage
from second_brain_builder.search import SearchIndex

MAX_PAGES_PER_SOURCE = 40
"""The most distinct pages that one Ingest writes. The loop stops when it reaches this."""

MAX_OUTPUT_TOKENS = 8_192
"""``max_tokens`` of each model request."""

MAX_TURNS_PER_PART = 60
"""The most model requests for one part. Then the loop goes on with the next part."""

MAX_IDLE_TURNS = 2
"""Answers without tool calls in a row, after which the part counts as finished."""

MAX_NOTES = 10
"""The finish notes of the latest parts that the next part gets."""

_TEMPORARY_BRAIN_ID = "ingest"


def ingest_source(
    root: Path,
    canonical_path: PurePosixPath,
    model: ChatModel,
    model_name: str,
    language: str,
    max_tokens_budget: int,
    on_event: Callable[[IngestEvent], None],
    *,
    index: SearchIndex | None = None,
    brain_id: str | None = None,
) -> IngestResult:
    """Let ``model`` write the Wiki Pages for one Source of the Brain at ``root``.

    The body of the Canonical Document at ``canonical_path`` is split on headings
    into chunks of about 8k tokens (see ``split_into_chunks``), and each chunk goes
    to the model in order, in a new conversation, with the finish notes of the
    earlier chunks. The model works with the tools search, list_pages, read_page,
    write_page, and finish. The first chunk also writes the summary page
    ``wiki/summaries/<source-id>.md``. Wiki Pages are written in ``language`` (a BCP
    47 tag), and ``generated.by`` is ``sbb/<model_name>``.

    The loop stops after the last chunk, when the tokens used (input plus output)
    reach ``max_tokens_budget``, when ``MAX_PAGES_PER_SOURCE`` pages are written,
    when the model refuses to answer, or when the first chunk ends without the
    summary page. ``IngestResult.stopped`` says which (see ``IngestStop``). It
    emits a ``progress`` event at the start of each chunk and a ``page_written``
    event for each page written. It does not change ``index.md`` or ``log.md`` and
    does not commit: the caller does.

    Args:
        index: The search index of the Brain, to search and to keep current while
            pages are written; give ``brain_id`` with it. Without it, a temporary
            index is built from the clone for this call.
        brain_id: The id of the Brain in ``index``.

    Raises:
        ValueError: ``canonical_path`` is not the path of a Canonical Document
            (``sources/.../<slug>/<slug>.md`` with ``type: source``), an argument is
            empty or out of range, or only one of ``index`` and ``brain_id`` is given.
        FileNotFoundError: The Canonical Document does not exist.
        OKFError: The Canonical Document cannot be read.
        ProviderError: A model request failed. Pages written before stay written.
    """
    if not model_name.strip():
        raise ValueError("model_name is empty")
    if not language.strip():
        raise ValueError("language is empty")
    if max_tokens_budget < 1:
        raise ValueError(f"max_tokens_budget must be 1 or more, not {max_tokens_budget}")
    if (index is None) != (brain_id is None):
        raise ValueError("give both index and brain_id, or neither")
    source, body = _load_source(root, canonical_path)
    catalog = source_catalog(root)
    catalog[source.id] = source
    chunks = split_into_chunks(body)
    summary_path = BrainLayout().wiki_path(PageType.SUMMARY, source.id)

    with _page_search(root, index, brain_id) as search:
        tools = IngestTools(
            root=root,
            source=source,
            summary_path=summary_path,
            catalog=catalog,
            search=search,
            model_name=model_name.strip(),
            page_limit=MAX_PAGES_PER_SOURCE,
            on_event=on_event,
        )
        run = _Run(
            model=model,
            system=system_prompt(language.strip(), MAX_PAGES_PER_SOURCE),
            tools=tools,
            budget=max_tokens_budget,
        )
        marker = secrets.token_hex(8)
        notes: list[str] = []
        stopped = IngestStop.COMPLETED
        for number, chunk in enumerate(chunks, start=1):
            limit = run.limit_reached()
            if limit is not None:
                stopped = limit
                break
            on_event(
                IngestEvent(
                    kind="progress",
                    data={"source": source.id, "chunk": number, "chunks": len(chunks)},
                )
            )
            tools.start_part(number)
            message = part_message(
                source=source,
                summary_path=summary_path,
                summary_exists=tools.summary_exists(),
                number=number,
                total=len(chunks),
                chunk=chunk,
                written=list(tools.written.items()),
                notes=notes[-MAX_NOTES:],
                marker=marker,
            )
            stop = run.part(message)
            if tools.note:
                notes.append(f"Part {number}: {tools.note}")
            if stop is not None:
                stopped = stop
                break
            if number == 1 and summary_path not in tools.written:
                stopped = IngestStop.NO_SUMMARY
                break
    return IngestResult(written=list(tools.written), deleted=[], usage=run.usage, stopped=stopped)


class _Run:
    """The model requests of one Ingest, and the tokens that they used."""

    def __init__(self, *, model: ChatModel, system: str, tools: IngestTools, budget: int) -> None:
        self.model = model
        self.system = system
        self.tools = tools
        self.budget = budget
        self.usage = Usage()

    def limit_reached(self) -> IngestStop | None:
        """The limit that the Ingest reached, if any: the page limit or the token budget."""
        if self.tools.page_limit_reached:
            return IngestStop.PAGE_LIMIT
        if self.usage.input_tokens + self.usage.output_tokens >= self.budget:
            return IngestStop.TOKEN_BUDGET
        return None

    def part(self, message: str) -> IngestStop | None:
        """Work on one part until the model finishes it or a limit stops it.

        Returns why the Ingest must stop, when a limit or a refusal (an answer
        without tool calls and with ``stop_reason`` ``refusal``) ended the part.
        The tool calls of an answer always run, also in the answer that reaches the
        budget: its tokens are spent already. A part also ends, and the Ingest goes
        on, after ``MAX_TURNS_PER_PART`` requests and after ``MAX_IDLE_TURNS``
        answers in a row without tool calls.
        """
        messages = [Message(role="user", content=message)]
        idle = 0
        for _ in range(MAX_TURNS_PER_PART):
            self.tools.start_request()
            response = self.model.chat(self.system, messages, TOOL_SPECS, MAX_OUTPUT_TOKENS)
            self.usage = self.usage + response.usage
            messages.append(response.to_message())
            if response.tool_calls:
                idle = 0
                for call in response.tool_calls:
                    result = self.tools.call(call)
                    messages.append(Message(role="tool", content=result, tool_call_id=call.id))
                if self.tools.finished:
                    return None
            elif response.stop_reason == StopReason.REFUSAL.value:
                return IngestStop.REFUSED
            else:
                idle += 1
                if idle >= MAX_IDLE_TURNS:
                    return None
                cut_off = response.stop_reason == StopReason.MAX_TOKENS.value
                messages.append(
                    Message(role="user", content=CUT_OFF_MESSAGE if cut_off else NUDGE_MESSAGE)
                )
            limit = self.limit_reached()
            if limit is not None:
                return limit
        return None


def _load_source(root: Path, canonical_path: PurePosixPath) -> tuple[CanonicalSource, str]:
    """The Source of a Canonical Document, and the body of the document."""
    if not is_canonical_path(canonical_path):
        raise ValueError(f"not the path of a Canonical Document: {canonical_path}")
    page = read_page(root, canonical_path)
    frontmatter = page.frontmatter
    if frontmatter is None or frontmatter.type != PageType.SOURCE.value:
        raise ValueError(f"{canonical_path} is not a Canonical Document (type: source)")
    title = " ".join((frontmatter.title or "").split()) or canonical_path.stem
    source = CanonicalSource(id=canonical_path.stem, path=canonical_path, title=title)
    return source, page.body


@contextmanager
def _page_search(
    root: Path, index: SearchIndex | None, brain_id: str | None
) -> Iterator[PageSearch]:
    if index is not None and brain_id is not None:
        yield PageSearch(index, brain_id, root)
        return
    with tempfile.TemporaryDirectory(prefix="sbb-ingest-") as directory:
        temporary = SearchIndex(Path(directory) / "search.sqlite")
        temporary.rebuild(_TEMPORARY_BRAIN_ID, root)
        yield PageSearch(temporary, _TEMPORARY_BRAIN_ID, root)
