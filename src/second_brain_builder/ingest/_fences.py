"""Fenced code blocks (CommonMark §4.5), for the line scanners of this package."""

from __future__ import annotations

import re

FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")
"""A line that opens a fenced code block. Group 1 is the fence."""


def closes_fence(line: str, fence: str) -> bool:
    """True when ``line`` closes the code block that ``fence`` opened."""
    stripped = line.strip()
    indent = len(line) - len(line.lstrip(" "))
    return indent <= 3 and len(stripped) >= len(fence) and stripped == fence[0] * len(stripped)
