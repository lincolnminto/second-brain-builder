"""``answer.save``."""

from __future__ import annotations

from second_brain_builder.ingest import save_answer as write_answer
from second_brain_builder.okf import LogKind, log_entry, read_page
from second_brain_builder.services import AnswerSavePayload
from second_brain_builder.worker.canonical import page_event, page_ref
from second_brain_builder.worker.context import JobRun


def save_answer(run: JobRun) -> None:
    """Write a Query answer as a synthesis Wiki Page, then commit and push it.

    The agent that wrote the answer is not known, so ``generated.by`` is
    ``ingest.UNKNOWN_AGENT``. An answer that breaks the OKF rules fails the job
    with the problems (``ingest.InvalidAnswerError``).
    """
    payload = run.payload(AnswerSavePayload)
    brain = run.brain(payload.brain_id)
    clone = run.open_clone(brain)
    path = write_answer(
        clone.root,
        payload.title,
        payload.body,
        payload.source_ids,
        payload.description,
        payload.tags,
        None,
    )
    page = read_page(clone.root, path)
    title = str(page_ref(page)["title"])
    clone.finish(f"Save answer: {title}", [log_entry(LogKind.ANSWER, title, path)])
    run.emit("page_written", page_event(page))
