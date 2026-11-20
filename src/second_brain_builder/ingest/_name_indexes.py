"""Deterministic, code-built Wiki pages retaining every name without a page."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from second_brain_builder.brains import resolve_in_brain
from second_brain_builder.ingest._map_reduce_notes import _normalized
from second_brain_builder.ingest._wiki import (
    CanonicalSource,
    link_text,
    merge_sources,
    render_citations,
)
from second_brain_builder.ingest.map_reduce_models import NoteGroup
from second_brain_builder.okf import (
    Frontmatter,
    Page,
    PageType,
    Stamp,
    read_page,
    render_page,
    validate_page,
    write_page,
)

MAX_NAME_INDEX_CHARS = 20_000
_SUBTYPES = ("person", "organization", "place", "event", "work", "idea", "method", "term")
_LABELS = {
    "en": (
        "names",
        ("People", "Organizations", "Places", "Events", "Works", "Ideas", "Methods", "Terms"),
    ),
    "pt": (
        "nomes",
        ("Pessoas", "Organizações", "Lugares", "Eventos", "Obras", "Ideias", "Métodos", "Termos"),
    ),
    "es": (
        "nombres",
        (
            "Personas",
            "Organizaciones",
            "Lugares",
            "Eventos",
            "Obras",
            "Ideas",
            "Métodos",
            "Términos",
        ),
    ),
}


def _letter(group: NoteGroup) -> str:
    return _normalized(group.name)[0]


def _range(first: str, last: str) -> str:
    def safe(letter: str) -> str:
        return letter if letter.isascii() and letter.isalnum() else f"u{ord(letter):x}"

    return safe(first) if first == last else f"{safe(first)}-{safe(last)}"


def _entries(group: NoteGroup, source_id: str) -> list[str]:
    aliases = sorted(set(group.aliases) - {group.name}, key=lambda name: (_normalized(name), name))
    prefix = f"- **{link_text(group.name)}**"
    if aliases:
        prefix += " (" + ", ".join(link_text(alias) for alias in aliases) + ")"
    prefix += ": "
    suffix = f".[^{source_id}]"
    # Split unusually large groups/facts across entries without discarding text.
    claims: list[str] = []
    for fact in group.facts:
        locator = f" ({link_text(fact.locator)})" if fact.locator else ""
        # Escape after splitting so no chunk cuts a Markdown escape in half.
        claims.extend(
            link_text(fact.text[start : start + 2000]) + locator
            for start in range(0, len(fact.text), 2000)
        )
    entries = []
    current = prefix
    for claim in claims:
        separator = "; " if current != prefix else ""
        if len(current + separator + claim + suffix) > 12_000 and current != prefix:
            entries.append(current + suffix)
            current, separator = prefix, ""
        current += separator + claim
    entries.append(current + suffix)
    return entries


def write_name_indexes(
    *,
    root: Path,
    source: CanonicalSource,
    groups: Sequence[NoteGroup],
    language: str,
    existing_paths: set[PurePosixPath],
) -> tuple[list[PurePosixPath], list[PurePosixPath]]:
    """Regenerate only this Source's ranges; return current and removed paths.

    Whole initial-letter buckets stay together where possible; an oversized
    bucket spans numbered ranges. The limit includes rendered frontmatter and
    citations. The Canonical Document's timestamp keeps identical runs identical.
    """
    names, labels = _LABELS.get(language.lower().split("-")[0], _LABELS["en"])
    source_page = read_page(root, source.path)
    generated = source_page.frontmatter.generated if source_page.frontmatter else None
    stamp = Stamp(
        by="second-brain-builder:name-index",
        at=(generated.at if generated else datetime(1970, 1, 1, tzinfo=timezone.utc)),
    )
    sources = merge_sources([source.id], {source.id: source}, [])

    def page(records: list[tuple[str, str, str]], label: str) -> Page:
        title = f"{source.title}: {names} {label}"
        sections = [f"# {link_text(title)}"]
        for subtype, heading in zip(_SUBTYPES, labels, strict=True):
            entries = [entry for _, kind, entry in records if kind == subtype]
            if entries:
                sections.append(f"## {heading}\n\n" + "\n".join(entries))
        return Page(
            path=PurePosixPath(f"wiki/name-indexes/{source.id}-{label}.md"),
            frontmatter=Frontmatter(
                type=PageType.NAME_INDEX, title=title, sources=sources, generated=stamp
            ),
            body=render_citations("\n\n".join(sections), [source.id], {source.id: source}),
        )

    buckets: dict[str, list[tuple[str, str, str]]] = {}
    for group in sorted(groups, key=lambda group: (_normalized(group.name), group.name, group.key)):
        letter = _letter(group)
        buckets.setdefault(letter, []).extend(
            (letter, group.subtype, entry) for entry in _entries(group, source.id)
        )
    chunks: list[list[tuple[str, str, str]]] = []
    current: list[tuple[str, str, str]] = []

    def fits(records: list[tuple[str, str, str]]) -> bool:
        label = _range(records[0][0], records[-1][0]) + "-999999"
        return len(render_page(page(records, label))) < MAX_NAME_INDEX_CHARS

    for records in buckets.values():
        if current and not fits(current + records):
            chunks.append(current)
            current = []
        for record in records:
            if current and not fits([*current, record]):
                chunks.append(current)
                current = []
            if not fits([record]):
                raise ValueError("one Name Index entry exceeds the page size limit")
            current.append(record)
    if current:
        chunks.append(current)
    ranges = [_range(chunk[0][0], chunk[-1][0]) for chunk in chunks]
    totals = Counter(ranges)
    numbers: Counter[str] = Counter()
    pages = []
    for chunk, label in zip(chunks, ranges, strict=True):
        numbers[label] += 1
        final_label = f"{label}-{numbers[label]}" if totals[label] > 1 else label
        built = page(chunk, final_label)
        if validate_page(built, existing_paths):
            raise ValueError("generated Name Index failed OKF validation")
        pages.append(built)
    directory = resolve_in_brain(root, "wiki/name-indexes")
    wanted = {built.path for built in pages}
    stale = []
    for file in sorted(directory.glob(f"{source.id}-*.md")):
        path = PurePosixPath(file.relative_to(root).as_posix())
        old = read_page(root, path)
        if (
            path not in wanted
            and old.frontmatter is not None
            and old.frontmatter.type == PageType.NAME_INDEX
            and [ref.id for ref in old.frontmatter.sources] == [source.id]
        ):
            stale.append(path)
    for built in pages:
        write_page(root, built)
    for path in stale:
        resolve_in_brain(root, str(path)).unlink()
    return [built.path for built in pages], stale
