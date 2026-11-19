"""Checks shared by the github tests."""

import traceback
from collections.abc import Callable

import pytest


def _exposed_text(exc: BaseException) -> str:
    """Return all the text that a log line or an error reporter can get from ``exc``.

    That is the formatted traceback, plus ``str()`` and ``repr()`` of ``exc`` and of
    every exception linked to it as ``__cause__`` or ``__context__``. A context
    that ``raise ... from None`` hides from the traceback is still included,
    because code that walks the chain can still read it.
    """
    parts = traceback.format_exception(type(exc), exc, exc.__traceback__)
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        parts += [str(current), repr(current)]
        pending += [
            linked for linked in (current.__cause__, current.__context__) if linked is not None
        ]
    return "\n".join(parts)


@pytest.fixture
def exposed_text() -> Callable[[BaseException], str]:
    """``exposed_text(exc)``: all the text that a log or an error reporter can show for ``exc``."""
    return _exposed_text
