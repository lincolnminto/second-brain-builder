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
from second_brain_builder.ingest.chunks import CHUNK_CHARS, split_into_chunks
from second_brain_builder.ingest.models import IngestEvent, IngestResult, IngestStop
from second_brain_builder.okf import PageType, read_page
from second_brain_builder.providers import ChatModel, Message, StopReason, Usage
from second_brain_builder.search import SearchIndex

MAX_PAGES_PER_PART = 16
"""The most writes allowed in one Part; further writes ask the model to finish."""

MAX_OUTPUT_TOKENS = 16_000
"""``max_tokens`` of each model request."""

MAX_TURNS_PER_PART = 60
"""The most model requests for one part. Then the loop goes on with the next part."""

MIN_PART_SAFETY_TOKENS = 600_000
"""The lowest hard token cap of one Part. On a large Brain a normal Part costs about
300k tokens (mostly cache reads), so the cap stops runaway loops, not real work."""

MAX_IDLE_TURNS = 2
"""Answers without tool calls in a row, after which the part counts as finished."""

MAX_NOTES = 20
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
    start_part: int = 0,
    chunk_chars: int = CHUNK_CHARS,
    on_part_done: Callable[[IngestResult], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_usage: Callable[[Usage], None] | None = None,
    previous_notes: list[str] | None = None,
    previous_written: list[PurePosixPath] | None = None,
    estimated_part_tokens: float | None = None,
) -> IngestResult:
    """Let ``model`` write the Wiki Pages for one Source of the Brain at ``root``.

    The body of the Canonical Document at ``canonical_path`` is split on headings
    into Parts of about 16k tokens (see ``split_into_chunks``), and each Part goes
    to the model in order, in a new conversation, with the finish notes of the
    earlier chunks. The model works with the tools search, list_pages, read_page,
    write_page, and finish. The first chunk also writes the summary page
    ``wiki/summaries/<source-id>.md``. Wiki Pages are written in ``language`` (a BCP
    47 tag), and ``generated.by`` is ``sbb/<model_name>``.

    The loop stops after the last chunk, before the next Part would exceed the
    estimated run budget, when the model refuses, or when the first Part ends
    without its summary. Every run starts at least one remaining Part. Writes
    beyond the page allowance are refused without interrupting the Part.
    ``IngestResult.stopped`` says which (see ``IngestStop``). It
    emits a ``progress`` event at the start of each chunk and a ``page_written``
    event for each page written. It does not change ``index.md`` or ``log.md`` and
    does not commit: the caller does.

    Args:
        index: The search index of the Brain, to search and to keep current while
            pages are written; give ``brain_id`` with it. Without it, a temporary
            index is built from the clone for this call.
        brain_id: The id of the Brain in ``index``.
        start_part: The number of already completed Parts (zero starts at the beginning).
        chunk_chars: The maximum characters per Part; must match the saved progress.

    Raises:
        ValueError: ``canonical_path`` is not the path of a Canonical Document
            (``sources/.../<slug>/<slug>.md`` with ``type: source``), an argument is
            empty or out of range, or only one of ``index`` and ``brain_id`` is given.
        FileNotFoundError: The Canonical Document does not exist.
        OKFError: The Canonical Document cannot be read.
        ProviderError: A model request failed; only the current Part is rolled back.
        RuntimeError: The hard token safety cap aborted the current Part.
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
    chunks = split_into_chunks(body, max_chars=chunk_chars)
    if not 0 <= start_part <= len(chunks):
        raise ValueError("start_part must be between 0 and the number of Parts")
    summary_path = BrainLayout().wiki_path(PageType.SUMMARY, source.id)

    with _page_search(root, index, brain_id) as search:
        tools = IngestTools(
            root=root,
            source=source,
            summary_path=summary_path,
            catalog=catalog,
            search=search,
            model_name=model_name.strip(),
            page_limit=MAX_PAGES_PER_PART,
            on_event=on_event,
        )
        run = _Run(
            model=model,
            system=system_prompt(language.strip(), MAX_PAGES_PER_PART),
            tools=tools,
            on_usage=on_usage,
        )
        marker = secrets.token_hex(8)
        notes = list(previous_notes or [])
        history = list(previous_written or [])
        completed_usage = Usage()
        stopped = IngestStop.COMPLETED
        parts_done = start_part

        def result() -> IngestResult:
            return IngestResult(
                written=list(tools.written),
                usage=run.usage,
                stopped=stopped,
                parts_done=parts_done,
                parts_total=len(chunks),
                completed_usage=completed_usage,
                notes=notes,
                context_written=list(
                    reversed(list(dict.fromkeys(reversed(history + tools.recent_written)))[:300])
                ),
            )

        for number, chunk in enumerate(chunks[start_part:], start=start_part + 1):
            if should_stop is not None and should_stop():
                stopped = IngestStop.USER_REQUESTED
                break
            done_this_run = parts_done - start_part
            estimate = (
                _tokens(completed_usage) / done_this_run if done_this_run else estimated_part_tokens
            )
            if (
                done_this_run
                and estimate is not None
                and _tokens(run.usage) + estimate > max_tokens_budget
            ):
                stopped = IngestStop.TOKEN_BUDGET
                break
            tools.start_part(number)
            on_event(
                IngestEvent(
                    kind="progress",
                    data={"source": source.id, "chunk": number, "chunks": len(chunks)},
                )
            )
            context = dict.fromkeys(history, "")
            context.update(tools.written)
            message = part_message(
                source=source,
                summary_path=summary_path,
                summary_exists=tools.summary_exists(),
                number=number,
                total=len(chunks),
                chunk=chunk,
                written=list(context.items())[-300:],
                notes=notes,
                marker=marker,
            )
            before = run.usage
            written_before = dict(tools.written)
            recent_before = list(tools.recent_written)
            try:
                stop = run.part(message, max(4 * (estimate or 0), MIN_PART_SAFETY_TOKENS))
                if stop is None and number == 1 and summary_path not in tools.written:
                    stop = IngestStop.NO_SUMMARY
                if stop is not None:
                    tools.rollback_part()
                    tools.written = written_before
                    tools.recent_written = recent_before
                    stopped = stop
                    break
            except Exception:
                tools.rollback_part()
                raise
            completed_usage = completed_usage + Usage(
                **{
                    key: value - getattr(before, key)
                    for key, value in run.usage.model_dump().items()
                }
            )
            if tools.note:
                notes.append(f"Part {number}: {tools.note}")
            notes = [note[-400:] for note in notes[-MAX_NOTES:]]
            parts_done = number
            if on_part_done is not None:
                on_part_done(result())
            if should_stop is not None and should_stop():
                stopped = IngestStop.USER_REQUESTED
                break
        return result()


def _tokens(usage: Usage) -> int:
    return usage.input_tokens + usage.output_tokens


class _Run:
    """The model requests of one Ingest, and the tokens that they used."""

    def __init__(
        self,
        *,
        model: ChatModel,
        system: str,
        tools: IngestTools,
        on_usage: Callable[[Usage], None] | None,
    ) -> None:
        self.model = model
        self.system = system
        self.tools = tools
        self.on_usage = on_usage
        self.usage = Usage()

    def part(self, message: str, safety_cap: float) -> IngestStop | None:
        """Finish one Part; only its hard safety cap can interrupt token spending."""
        before = _tokens(self.usage)
        messages = [Message(role="user", content=message)]
        idle = 0
        for _ in range(MAX_TURNS_PER_PART):
            self.tools.start_request()
            response = self.model.chat(self.system, messages, TOOL_SPECS, MAX_OUTPUT_TOKENS)
            self.usage = self.usage + response.usage
            if self.on_usage is not None:
                self.on_usage(self.usage)
            if _tokens(self.usage) - before >= safety_cap:
                raise RuntimeError(
                    f"Ingest Part reached its hard safety cap ({int(safety_cap)} tokens); "
                    "its writes were discarded."
                )
            messages.append(response.to_message())
            if response.cut_off_tool_call_ids:
                idle = 0
                for call_id in response.cut_off_tool_call_ids:
                    messages.append(
                        Message(
                            role="tool",
                            tool_call_id=call_id,
                            content=(
                                "Error: your answer was cut off before this call was complete, "
                                "so it did not run. Call it again with a shorter body, "
                                "one page per call."
                            ),
                        )
                    )
                continue
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
