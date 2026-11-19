"""Resolve all targets before writers run, including slug and existing-page collisions."""

from pathlib import Path, PurePosixPath

from second_brain_builder.brains import BrainLayout, read_pages, slugify
from second_brain_builder.ingest._map_reduce_notes import _normalized
from second_brain_builder.ingest.map_reduce_models import MapReduceState, NoteGroup
from second_brain_builder.okf import PageType


def resolve_targets(root: Path, state: MapReduceState) -> None:
    if state.reduce_targets or not state.groups:
        return
    existing: dict[tuple[str, str], PurePosixPath] = {}
    for page in read_pages(root):
        if (
            page.frontmatter is not None
            and page.frontmatter.title is not None
            and page.frontmatter.type in {PageType.ENTITY, PageType.CONCEPT}
        ):
            existing.setdefault(
                (page.frontmatter.type, _normalized(page.frontmatter.title)), page.path
            )
    buckets: dict[PurePosixPath, tuple[NoteGroup, list[int]]] = {}
    for number, group in enumerate(state.groups):
        candidates = [group.name, *group.aliases]
        target = next(
            (
                existing[(group.kind, _normalized(name))]
                for name in candidates
                if (group.kind, _normalized(name)) in existing
            ),
            BrainLayout().wiki_path(PageType(group.kind), slugify(group.name)),
        )
        if target not in buckets:
            buckets[target] = (group.model_copy(deep=True), [number])
        else:
            merged, numbers = buckets[target]
            merged.facts.extend(group.facts)
            merged.aliases = list(dict.fromkeys([*merged.aliases, group.name, *group.aliases]))
            numbers.append(number)
    # Legacy checkpoints can already contain a completed prefix. Keep fully
    # completed targets first; a collision with unprocessed facts must run again.
    ordered = sorted(
        buckets.items(), key=lambda item: any(n >= state.reduce_groups_done for n in item[1][1])
    )
    state.reduce_groups_done = sum(
        all(n < state.reduce_groups_done for n in numbers) for _, (_, numbers) in ordered
    )
    state.groups = [group for _, (group, _) in ordered]
    state.reduce_targets = [path for path, _ in ordered]
