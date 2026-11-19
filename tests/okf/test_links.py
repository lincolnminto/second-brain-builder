"""Links in Markdown bodies: extract, resolve, rewrite after moves, and remove."""

import time
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin

from second_brain_builder.okf import (
    extract_links,
    footnote_labels,
    read_page,
    remove_links,
    resolve_link,
    rewrite_links,
)

P = PurePosixPath
FIXTURE_BRAIN = Path(__file__).parents[1] / "fixtures" / "okf" / "brain"

BODY_WITH_CODE = """\
# Captain Ahab

Ahab commands the [Pequod](/wiki/entities/pequod.md) and sails with
[Ishmael](../entities/ishmael.md#voyage). Inline code `[not a link](/code-span.md)`
is text, and so is ``a `[nested](/double-backtick.md)` span``. A span can
cross lines: `[still](/multi-line.md)
code`.

```markdown
[fenced](/fenced-backticks.md)
```

~~~
[tilde](/fenced-tildes.md)
~~~

    [indented](/indented-code.md)

- In a list, see [obsession](/wiki/concepts/obsession.md).

      [indented in a list](/indented-in-list.md)

  ```
  [fenced in a list](/fenced-in-list.md)
  ```

> A quote links [whaling](/wiki/concepts/whaling.md).
> ```
> [fenced in a quote](/fenced-in-quote.md)
> ```

<div>
[raw HTML block](/html-block.md)
</div>

| Ship | Captain |
|------|---------|
| [Pequod](/wiki/entities/pequod.md) | `[cell code](/cell-code.md)` |
"""


def test_extract_links_skips_code_spans_and_code_blocks() -> None:
    assert extract_links(BODY_WITH_CODE) == [
        "/wiki/entities/pequod.md",
        "../entities/ishmael.md#voyage",
        "/wiki/concepts/obsession.md",
        "/wiki/concepts/whaling.md",
    ]


def test_links_inside_link_titles_are_plain_text() -> None:
    body = '[real](/keep.md "[title](/old.md)")'
    assert extract_links(body) == ["/keep.md"]
    assert rewrite_links(body, {P("old.md"): P("new.md")}) == body
    assert remove_links(body, [P("old.md")]) == body


def _commonmark_link_targets(body: str) -> set[str]:
    """Link targets as a CommonMark parser with GFM tables and footnotes sees them."""
    parser = MarkdownIt("commonmark").enable("table").use(footnote_plugin)
    targets: set[str] = set()

    def walk(tokens: list[Token]) -> None:
        for token in tokens:
            if token.type == "link_open":
                targets.add(unquote(str(token.attrs["href"])))
            walk(token.children or [])

    walk(parser.parse(body))
    return targets


EDGE_CASES_BODY = (
    "A title in parentheses cannot nest: [a](/wiki/a.md (b(c))), but it can escape:\n"
    "[b](/wiki/b.md (b\\(c)). Destinations nest 32 parentheses at most:\n"
    f"[c](/wiki/c{'(' * 32}{')' * 32}.md) and [d](/wiki/d{'(' * 33}{')' * 33}.md).\n"
    "Fragments can escape: [e](/wiki/e.md#a\\)b), [f](</wiki/f.md#a)b>), and\n"
    "[g](/wiki/g.md#a&#41;b).\n"
)


@pytest.mark.parametrize(
    "body",
    [
        BODY_WITH_CODE,
        EDGE_CASES_BODY,
        read_page(FIXTURE_BRAIN, P("wiki/summaries/moby-dick.md")).body,
        read_page(FIXTURE_BRAIN, P("wiki/entities/captain-ahab.md")).body,
        read_page(FIXTURE_BRAIN, P("sources/classics/moby-dick/moby-dick.md")).body,
        (FIXTURE_BRAIN / "index.md").read_text(encoding="utf-8"),
    ],
)
def test_extract_links_agrees_with_a_commonmark_parser(body: str) -> None:
    assert {unquote(href) for href in extract_links(body)} == _commonmark_link_targets(body)


def test_extract_links_finds_every_link_form_but_not_images() -> None:
    body = """\
A [titled link](/wiki/entities/ishmael.md "Ishmael") and an
[angle link](</wiki/concepts/white whale.md>) and a [reference][ahab].
Escapes resolve: [file](/wiki/notes/a\\_b.md) and [amp](/wiki/notes/x&amp;y.md).
Balanced parentheses: [paren](/wiki/notes/moby_(novel).md).
An autolink: <https://example.com/whaling>.
An image is not a link: ![The Pequod](/sources/classics/moby-dick/assets/pequod.svg)
A linked image counts once: [![cover](/assets/cover.png)](/wiki/summaries/moby-dick.md)
Escaped brackets are text: \\[not](/escaped.md).
A wikilink is text: [[Captain Ahab]].
A repeat: [Ishmael again](/wiki/entities/ishmael.md).

[ahab]: /wiki/entities/captain-ahab.md "Captain Ahab"

Claim.[^moby-dick]

[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)
"""
    assert extract_links(body) == [
        "/wiki/entities/ishmael.md",
        "/wiki/concepts/white whale.md",
        "/wiki/notes/a_b.md",
        "/wiki/notes/x&y.md",
        "/wiki/notes/moby_(novel).md",
        "https://example.com/whaling",
        "/wiki/summaries/moby-dick.md",
        "/wiki/entities/captain-ahab.md",
        "/sources/classics/moby-dick/moby-dick.md",
    ]


ISHMAEL = "wiki/entities/ishmael.md"
MOBY_DICK = "sources/classics/moby-dick/moby-dick.md"


@pytest.mark.parametrize(
    ("from_path", "href", "expected"),
    [
        (ISHMAEL, "/wiki/entities/captain-ahab.md", "wiki/entities/captain-ahab.md"),
        (ISHMAEL, "captain-ahab.md", "wiki/entities/captain-ahab.md"),
        (ISHMAEL, "./captain-ahab.md#crew", "wiki/entities/captain-ahab.md"),
        (ISHMAEL, "../concepts/obsession.md?v=2", "wiki/concepts/obsession.md"),
        (ISHMAEL, "/wiki/concepts/white%20whale.md", "wiki/concepts/white whale.md"),
        (MOBY_DICK, "assets/pequod.svg", "sources/classics/moby-dick/assets/pequod.svg"),
        ("index.md", "wiki/", "wiki"),
        (ISHMAEL, "#voyage", ISHMAEL),
        (ISHMAEL, "../../../outside.md", None),
        (ISHMAEL, "/../outside.md", None),
        (ISHMAEL, "https://example.com/whaling.md", None),
        (ISHMAEL, "mailto:crew@example.com", None),
        (ISHMAEL, "//example.com/whaling.md", None),
    ],
)
def test_resolve_link(from_path: str, href: str, expected: str | None) -> None:
    resolved = resolve_link(P(from_path), href)
    assert resolved == (P(expected) if expected is not None else None)


# The Source moves from the "classics" Folder to "novels/19th-century".
SOURCE_MOVE = {P("sources/classics/moby-dick"): P("sources/novels/19th-century/moby-dick")}


def test_rewrite_links_after_a_source_directory_moves() -> None:
    body = """\
Ahab hunts the whale.[^moby-dick]
See [chapter 28](/sources/classics/moby-dick/moby-dick.md#ahab)
and the [drawing](</sources/classics/moby-dick/assets/pequod.svg> "The Pequod").
![The Pequod](/sources/classics/moby-dick/assets/pequod.svg)
Unrelated: [Ishmael](/wiki/entities/ishmael.md),
[web](https://example.com/sources/classics/moby-dick/moby-dick.md),
and code `[old](/sources/classics/moby-dick/moby-dick.md)`.

[doc]: /sources/classics/moby-dick/moby-dick.md

[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)
"""
    new = "/sources/novels/19th-century/moby-dick/"

    assert (
        rewrite_links(body, SOURCE_MOVE)
        == f"""\
Ahab hunts the whale.[^moby-dick]
See [chapter 28]({new}moby-dick.md#ahab)
and the [drawing](<{new}assets/pequod.svg> "The Pequod").
![The Pequod]({new}assets/pequod.svg)
Unrelated: [Ishmael](/wiki/entities/ishmael.md),
[web](https://example.com/sources/classics/moby-dick/moby-dick.md),
and code `[old](/sources/classics/moby-dick/moby-dick.md)`.

[doc]: {new}moby-dick.md

[^moby-dick]: [Moby-Dick]({new}moby-dick.md)
"""
    )


def test_rewrite_links_in_a_page_that_moved_with_its_directory() -> None:
    # The Canonical Document moves too: links into its own directory stay valid,
    # and relative links out of it need one more "../".
    body = (
        "![The Pequod](assets/pequod.svg) and ![again](./assets/pequod.svg)\n"
        "See [Ahab](../../../wiki/entities/captain-ahab.md#hunt) and "
        "[the Folder](../index.md).\n"
    )
    rewritten = rewrite_links(
        body, SOURCE_MOVE, from_path=P("sources/classics/moby-dick/moby-dick.md")
    )

    assert rewritten == (
        "![The Pequod](assets/pequod.svg) and ![again](./assets/pequod.svg)\n"
        "See [Ahab](../../../../wiki/entities/captain-ahab.md#hunt) and "
        "[the Folder](../../../classics/index.md).\n"
    )


def test_rewrite_relative_links_to_a_moved_file() -> None:
    moves = {P("wiki/concepts/obsession.md"): P("wiki/concepts/monomania.md")}
    body = "His [obsession](../concepts/obsession.md) and [again](./obsession.md).\n"

    assert rewrite_links(body, moves, from_path=P("wiki/entities/captain-ahab.md")) == (
        "His [obsession](../concepts/monomania.md) and [again](./obsession.md).\n"
    )
    # Without the page path, relative links cannot be resolved and stay as they are.
    assert rewrite_links(body, moves) == body


def test_rewrite_links_without_matching_moves_keeps_the_text() -> None:
    body = "Text with [a link](/wiki/entities/ishmael.md).\r\n"
    assert rewrite_links(body, SOURCE_MOVE) == "Text with [a link](/wiki/entities/ishmael.md).\n"


def test_remove_links_turns_links_to_deleted_pages_into_text() -> None:
    body = """\
[**Captain** Ahab](/wiki/entities/captain-ahab.md) commands the
[Pequod](../entities/pequod.md "The ship").
![Portrait of Ahab](/wiki/entities/captain-ahab.md)
[Ishmael](/wiki/entities/ishmael.md) stays, and so does `[code](/wiki/entities/captain-ahab.md)`.
"""
    deleted = {P("wiki/entities/captain-ahab.md"), P("wiki/entities/pequod.md")}

    assert (
        remove_links(body, deleted, from_path=P("wiki/concepts/obsession.md"))
        == """\
**Captain** Ahab commands the
Pequod.
Portrait of Ahab
[Ishmael](/wiki/entities/ishmael.md) stays, and so does `[code](/wiki/entities/captain-ahab.md)`.
"""
    )


@pytest.mark.parametrize(
    "destination",
    [
        "/wiki/entities/old.md#a\\)b",
        "/wiki/entities/old.md#a&#41;b",
        "/wiki/entities/old.md&#35;a\\)b",
        "/wiki/entities/old.md?q=\\(1\\)#x%20y",
        "</wiki/entities/old.md#a)b c>",
    ],
)
def test_rewrite_links_keeps_the_query_and_fragment_as_written(destination: str) -> None:
    body = f"See [Old]({destination}) now.\n"
    moves = {P("wiki/entities/old.md"): P("wiki/entities/new.md")}

    rewritten = rewrite_links(body, moves)

    assert rewritten == body.replace("/wiki/entities/old.md", "/wiki/entities/new.md")
    # A CommonMark parser reads the same link at the new path, and no text moves
    # out of the destination into the paragraph.
    [before] = _commonmark_link_targets(body)
    assert _commonmark_link_targets(rewritten) == {before.replace("/old.md", "/new.md")}


def test_rewrite_relative_links_keeps_the_fragment_as_written() -> None:
    moves = {P("wiki/entities/old.md"): P("wiki/entities/new.md")}
    body = "See [Old](../entities/old.md#a\\)b) now.\n"

    assert rewrite_links(body, moves, from_path=P("wiki/concepts/obsession.md")) == (
        "See [Old](../entities/new.md#a\\)b) now.\n"
    )


CRAFTED_PARAGRAPHS = {
    "closing brackets": "](" * 30_000,
    "unclosed destinations": "[a](" * 25_000,
    "unclosed titles": "[a](b (" * 20_000,
    "unclosed footnotes": "[^" * 30_000,
    "unclosed code spans": "".join("`" * length + "a" for length in range(1, 700)),
    "links and autolinks": "[a](/b.md) <https://example.com/> " * 10_000,
}


@pytest.mark.parametrize("paragraph", CRAFTED_PARAGRAPHS.values(), ids=CRAFTED_PARAGRAPHS.keys())
def test_link_scan_time_grows_linearly_with_the_body(paragraph: str) -> None:
    # Canonical Documents come from untrusted Sources. A scan that goes back or
    # forward through the paragraph for each "](" or "[^" takes minutes on these.
    body = paragraph + "\n\nSee [Ahab](/wiki/entities/captain-ahab.md).[^moby-dick]\n"

    started = time.perf_counter()
    links = extract_links(body)
    labels = footnote_labels(body)
    elapsed = time.perf_counter() - started

    assert links[-1] == "/wiki/entities/captain-ahab.md"
    assert "moby-dick" in labels
    assert elapsed < 5
