"""The bundle-root ``log.md`` (OKF §9): entries grouped under date headings, newest first."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePosixPath

from second_brain_builder.domain import StrEnum
from second_brain_builder.okf._format import link_text, markdown_link, one_line
from second_brain_builder.okf._markdown import normalize_newlines

LOG_TITLE = "# Log"
"""The heading of a new ``log.md``."""

DATE_HEADING_RE = re.compile(r"## (\d{4}-\d{2}-\d{2})[ \t]*")
"""A date heading line of ``log.md``."""


class LogKind(StrEnum):
    """The bold word at the start of a log entry."""

    CREATE = "Create"
    ADD = "Add"
    INGEST = "Ingest"
    MOVE = "Move"
    DELETE = "Delete"
    RECONVERT = "Reconvert"
    ANSWER = "Answer"


def log_entry(
    kind: LogKind,
    title: str,
    path: PurePosixPath | None = None,
    detail: str | None = None,
) -> str:
    """Format one entry: ``**Ingest**: [Dune](/sources/books/dune/dune.md) - 12 pages updated``.

    Without ``path`` the title is plain text, for example for a deleted Source.
    """
    label = markdown_link(title, path) if path is not None else link_text(title)
    entry = f"**{kind}**: {label}"
    if detail and one_line(detail):
        entry += f" - {one_line(detail)}"
    return entry


def prepend_log(text: str, day: date, entries: list[str]) -> str:
    """Add entries to ``log.md`` under the ``## YYYY-MM-DD`` heading of ``day``.

    Each entry is one line; a ``* `` bullet is added when it has none. The entries
    go first under the heading of ``day``. If there is no such heading, a new one
    goes before the first older date, so dates stay newest first. Empty text gives a
    new log with the ``# Log`` heading. No entries give the text unchanged.
    """
    lines_to_add = [_bullet(entry) for entry in entries if one_line(entry)]
    if not lines_to_add:
        return text
    if isinstance(day, datetime):
        day = day.date()
    heading = f"## {day.isoformat()}"
    text = normalize_newlines(text)
    if not text.strip():
        return "\n".join([LOG_TITLE, "", heading, *lines_to_add]) + "\n"

    lines = text.rstrip("\n").split("\n")
    for index, line in enumerate(lines):
        match = DATE_HEADING_RE.fullmatch(line)
        if match is None:
            continue
        if match.group(1) == day.isoformat():
            lines[index + 1 : index + 1] = lines_to_add
            break
        if match.group(1) < day.isoformat():
            block = [heading, *lines_to_add, ""]
            if index > 0 and lines[index - 1].strip():
                block.insert(0, "")
            lines[index:index] = block
            break
    else:
        lines += ["", heading, *lines_to_add]
    return "\n".join(lines) + "\n"


def _bullet(entry: str) -> str:
    line = one_line(entry)
    return line if line.startswith("* ") else f"* {line}"
