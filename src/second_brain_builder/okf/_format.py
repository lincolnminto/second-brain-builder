"""Small Markdown formatting helpers for the files that code writes."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from second_brain_builder.okf.links import bundle_href

_LINK_TEXT_SPECIAL_RE = re.compile(r"([\\\[\]`*_<>])")


def one_line(text: str) -> str:
    """Collapse all whitespace, line endings included, to single spaces."""
    return " ".join(text.split())


def link_text(text: str) -> str:
    """Text for a link label, on one line, with Markdown syntax characters escaped."""
    return _LINK_TEXT_SPECIAL_RE.sub(r"\\\1", one_line(text))


def markdown_link(title: str, path: PurePosixPath) -> str:
    """``[title](/path)`` with a ``/``-rooted href."""
    return f"[{link_text(title)}]({bundle_href(path)})"
