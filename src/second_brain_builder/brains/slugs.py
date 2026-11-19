"""Slugs: the ASCII names of Sources, Folders, and Wiki Pages in a Brain."""

from __future__ import annotations

import re
import unicodedata

SLUG_MAX_LENGTH = 80
"""``slugify`` cuts longer slugs at a hyphen. ``unique_slug`` can add a suffix after that."""

FALLBACK_SLUG = "untitled"
"""The slug of a text that has no ASCII letters or digits after folding."""

RESERVED_SLUGS = frozenset({"index", "log", "assets"})
"""Slugs that would break the Brain layout.

``index`` and ``log`` would give ``index.md`` and ``log.md``, which OKF reserves at
every level of a bundle. ``assets`` as a Folder or Source slug would match the
``sources/**/assets/**`` rule of ``.gitattributes``, so the Source would skip Git LFS.
``slugify`` adds ``-1`` to these slugs.
"""

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SEPARATORS_RE = re.compile(r"[^a-z0-9]+")
# Letters that NFKD does not split into an ASCII letter and a combining mark.
_TRANSLITERATIONS = str.maketrans(
    {
        "ß": "ss",
        "ẞ": "SS",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "TH",
        "ł": "l",
        "Ł": "L",
        "\u0131": "i",  # dotless i
    }
)
# Apostrophes join the parts of a word: "Don't" gives "dont", not "don-t".
_APOSTROPHES = frozenset("'\u2019\u02bc")


def slugify(text: str) -> str:
    """Make a slug from a title or a filename stem.

    Unicode is folded to ASCII (NFKD, then combining marks are dropped), letters
    are lowercased, and every run of other characters becomes one hyphen. The
    result has only ``a-z``, ``0-9``, and single hyphens, with no hyphen at either
    end, and at most ``SLUG_MAX_LENGTH`` characters. It is never empty
    (``FALLBACK_SLUG``) and never one of ``RESERVED_SLUGS``.

    >>> slugify("Crème Brûlée: A History")
    'creme-brulee-a-history'
    """
    folded = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATIONS))
    kept: list[str] = []
    for char in folded:
        if char in _APOSTROPHES or unicodedata.combining(char):
            continue
        # Other characters outside ASCII (CJK, dashes, symbols) separate words.
        kept.append(char.lower() if char.isascii() else "-")
    slug = _SEPARATORS_RE.sub("-", "".join(kept)).strip("-")
    if len(slug) > SLUG_MAX_LENGTH:
        cut = slug[: SLUG_MAX_LENGTH + 1]
        slug = cut.rsplit("-", 1)[0] if "-" in cut else cut[:SLUG_MAX_LENGTH]
        slug = slug.strip("-")
    if not slug:
        return FALLBACK_SLUG
    if slug in RESERVED_SLUGS:
        return f"{slug}-1"
    return slug


def is_slug(text: str) -> bool:
    """True when ``text`` is a valid slug: ``a-z``, ``0-9``, single inner hyphens, not reserved."""
    return _SLUG_RE.fullmatch(text) is not None and text not in RESERVED_SLUGS


def unique_slug(base: str, taken: set[str]) -> str:
    """Return ``base``, or ``base-2``, ``base-3``, ... : the first one not in ``taken``.

    Raises:
        ValueError: ``base`` is not a slug (see ``is_slug``).
    """
    if not is_slug(base):
        raise ValueError(f"not a slug: {base!r}")
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"
