"""Footnote citations: labels, and removal when a Source is deleted."""

from second_brain_builder.okf import cited_labels, footnote_labels, remove_citations


def test_link_destinations_and_titles_do_not_cite_sources() -> None:
    body = (
        '[hidden](/keep.md "[^title]") [destination](/[^destination].md) '
        '[visible [^visible]](/keep.md "[^hidden]")'
    )
    assert cited_labels(body) == {"visible"}
    assert remove_citations(body, "title") == body


SYNTHESIS_BODY = """\
# Two voyages

Ahab hunts one whale for years.[^moby-dick] Gulliver sails to four strange
lands [^gulliver].[^moby-dick-notes] Both voyages end far from home [^gulliver] and
[^moby-dick] change the narrator.

> A quoted claim.[^gulliver]

Code keeps its text: `see [^gulliver]`.

```
[^gulliver]: not a definition, this is code
```

[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)

[^gulliver]: [Gulliver's Travels](/sources/classics/gullivers-travels/gullivers-travels.md)
    The 1726 edition, with a second paragraph that belongs to the footnote.

[^moby-dick-notes]: [Notes on Moby-Dick](/sources/notes/moby-dick-notes/moby-dick-notes.md)
"""


def test_footnote_labels_include_references_and_definitions_outside_code() -> None:
    body = SYNTHESIS_BODY + "\n[^unused]: A definition without a reference.\n"

    assert footnote_labels(body) == {"moby-dick", "gulliver", "moby-dick-notes", "unused"}
    assert cited_labels(body) == {"moby-dick", "gulliver", "moby-dick-notes"}


def test_remove_citations_removes_references_and_the_definition_block() -> None:
    assert (
        remove_citations(SYNTHESIS_BODY, "gulliver")
        == """\
# Two voyages

Ahab hunts one whale for years.[^moby-dick] Gulliver sails to four strange
lands.[^moby-dick-notes] Both voyages end far from home and
[^moby-dick] change the narrator.

> A quoted claim.

Code keeps its text: `see [^gulliver]`.

```
[^gulliver]: not a definition, this is code
```

[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)

[^moby-dick-notes]: [Notes on Moby-Dick](/sources/notes/moby-dick-notes/moby-dick-notes.md)
"""
    )


def test_remove_citations_matches_the_whole_label() -> None:
    result = remove_citations(SYNTHESIS_BODY, "moby-dick")

    assert cited_labels(result) == {"gulliver", "moby-dick-notes"}
    assert footnote_labels(result) == {"gulliver", "moby-dick-notes"}
    assert "[^moby-dick-notes]: [Notes on Moby-Dick]" in result


def test_remove_the_last_citation_of_a_page() -> None:
    body = (
        "# Ishmael\n\nIshmael goes to sea.[^moby-dick]\n\n"
        "[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)\n"
    )

    assert remove_citations(body, "moby-dick") == "# Ishmael\n\nIshmael goes to sea.\n"


def test_remove_a_definition_that_interrupts_a_paragraph() -> None:
    body = (
        "Ishmael goes to sea.[^moby-dick]\n"
        "[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)\n"
        "\n"
        "He signs on to the Pequod.\n"
    )

    assert remove_citations(body, "moby-dick") == (
        "Ishmael goes to sea.\n\nHe signs on to the Pequod.\n"
    )
