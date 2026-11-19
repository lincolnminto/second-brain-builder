"""Keep converted Markdown links within the Canonical Document's destinations."""

import re
from typing import cast
from urllib.parse import unquote

from second_brain_builder.okf import _markdown
from second_brain_builder.okf.links import apply_edits, split_href

_REFERENCE = re.compile(r"(!?)\[((?:[^\[\]\\]|\\.)+)\](?:\[([^\[\]]*)\])?")


def _allowed(href: str) -> bool:
    if href.lower().startswith(("http:", "https:", "mailto:")) or href.startswith("#"):
        return True
    path, _ = split_href(href)
    parts = unquote(path).split("/")
    return (
        len(parts) == 2
        and parts[0] == "assets"
        and parts[1] not in ("", ".", "..")
        and "\\" not in parts[1]
    )


def sanitize_links(body: str) -> str:
    """Unwrap unsupported links and images, preserving their text and all code.

    Use OKF's positional scanner so retained links keep their original formatting.
    Delete only the delimiters, allowing nested images to be sanitized as well.
    """
    found = _markdown.scan(body)
    edits: list[tuple[int, int, str]] = []
    definitions: dict[str, bool] = {}
    for link in found.links:
        if link.kind == "definition":
            label = found.text[link.text_start : link.text_end]
            definitions.setdefault(_label(label), _allowed(link.href))
        elif not _allowed(link.href):
            edits.extend(((link.start, link.text_start, ""), (link.text_end, link.end, "")))
    if definitions:
        edits.extend(_reference_edits(found, definitions))
    return apply_edits(found.text, edits)


def _label(text: str) -> str:
    return " ".join(text.split()).upper()


def _reference_edits(
    found: _markdown.Scan, definitions: dict[str, bool]
) -> list[tuple[int, int, str]]:
    """Find reference uses only in inline content, outside code and link metadata."""
    text = found.text
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    masked = ["\n" if char == "\n" else " " for char in text]
    env: dict[str, object] = {}
    for token in _markdown._parser().parse(text, env):
        if token.type == "inline" and token.map is not None:
            start, end = (offsets[line] for line in token.map)
            masked[start:end] = text[start:end]
            _markdown._blank_code_spans(masked, start, end)
    for link in found.links:
        _markdown._blank(masked, link.text_end, link.end)
    content = "".join(masked)
    edits: list[tuple[int, int, str]] = []
    ranges = cast(list[tuple[int, int]], env.get(_markdown._REFERENCE_RANGES, []))
    for first, last in ranges:
        start, end = offsets[first], offsets[last]
        if any(
            link.kind == "definition" and start <= link.start < end and not _allowed(link.href)
            for link in found.links
        ):
            edits.append((start, end, ""))
    for match in _REFERENCE.finditer(content):
        if _markdown._is_escaped(content, match.start()):
            continue
        label = _label(match[3] or match[2])
        if definitions.get(label) is False:
            edits.extend(((match.start(), match.start(2), ""), (match.end(2), match.end(), "")))
    return edits
