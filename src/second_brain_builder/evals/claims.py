"""Claims of Wiki Pages: the sentences that hold a footnote reference, and their sampling.

A claim is one footnote reference of a Wiki Page with the sentence that holds it.
References count as ``services.resolve_citation`` counts them (``search.cited_sentence``):
the references of the body first, from 0, then those in footnote definitions. Only
the references of the body are claims.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from second_brain_builder.search import cited_sentence
from second_brain_builder.search.text import markdown_tokens


class Claim(BaseModel):
    """One sentence of a Wiki Page and the Source that one of its footnotes cites."""

    model_config = ConfigDict(frozen=True)

    page_path: str
    """The Wiki Page, from the Brain root, such as ``wiki/entities/grey-heron.md``."""
    page_type: str
    ref: int
    """The number of the footnote reference in the page, from 0."""
    source_id: str
    """The footnote label: the id of the cited Source."""
    sentence: str
    """The sentence that holds the reference, as plain text without footnote references."""
    page_title: str = ""
    """The title of the Wiki Page: the subject that a sentence can leave out."""


@dataclass(frozen=True)
class WikiPageText:
    """The parts of a Wiki Page that sampling reads."""

    path: str
    type: str
    body: str
    title: str = ""


PageReader = Callable[[str], WikiPageText | None]
"""Reads a Wiki Page by its path, or gives None when the page cannot be read."""


def body_reference_count(body: str) -> int:
    """The number of footnote references in a Markdown body, outside footnote definitions.

    These references have the numbers 0 to count - 1, because a renderer shows the
    footnote definitions (and the references in them) after the body.
    """
    count = 0
    in_definition = False
    for token in markdown_tokens(body):
        if token.type == "footnote_open":
            in_definition = True
        elif token.type == "footnote_close":
            in_definition = False
        elif token.type == "inline" and not in_definition:
            count += sum(1 for child in token.children or [] if child.type == "footnote_ref")
    return count


def page_claims(page: WikiPageText, refs: Sequence[int]) -> list[Claim]:
    """The claims of ``page`` for the reference numbers ``refs``, in order.

    A reference whose sentence is empty is left out, and so is a second reference
    with the same sentence and the same Source.
    """
    claims: list[Claim] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        cited = cited_sentence(page.body, ref)
        if cited is None or not cited.sentence or (cited.sentence, cited.label) in seen:
            continue
        seen.add((cited.sentence, cited.label))
        claims.append(
            Claim(
                page_path=page.path,
                page_type=page.type,
                ref=ref,
                source_id=cited.label,
                sentence=cited.sentence,
                page_title=page.title,
            )
        )
    return claims


def cites_sentence(page: WikiPageText, claim: Claim) -> bool:
    """Whether reference ``claim.ref`` of ``page`` still holds the claim's sentence and Source.

    A job can change a page after sampling. Then the same reference number can hold
    another sentence, and its passage is not the one that the claim cites.
    """
    cited = cited_sentence(page.body, claim.ref)
    return cited is not None and (cited.sentence, cited.label) == (claim.sentence, claim.source_id)


def sample_claims(
    paths: Sequence[str],
    read_page: PageReader,
    *,
    page_count: int,
    claims_per_page: int,
    rng: random.Random,
) -> list[Claim]:
    """Claims from up to ``page_count`` random pages, up to ``claims_per_page`` each.

    Pages are read in a random order until ``page_count`` pages with at least one
    claim are found, so pages without footnotes do not count. The claims of a page
    are a random sample of its body references, in page order. The same ``rng``
    state and the same pages give the same claims.
    """
    if page_count < 1 or claims_per_page < 1:
        raise ValueError("page_count and claims_per_page must be 1 or more")
    order = sorted(paths)
    rng.shuffle(order)
    claims: list[Claim] = []
    pages = 0
    for path in order:
        if pages >= page_count:
            break
        page = read_page(path)
        if page is None:
            continue
        total = body_reference_count(page.body)
        if total == 0:
            continue
        refs = sorted(rng.sample(range(total), min(claims_per_page, total)))
        found = page_claims(page, refs)
        if found:
            pages += 1
            claims.extend(found)
    return claims
