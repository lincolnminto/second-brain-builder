"""Page content and query text are untrusted: they must not inject markup, fail, or stall."""

import time
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

import pytest
from markdown_it import MarkdownIt

import second_brain_builder.search.index as index_module
from second_brain_builder.search import HIGHLIGHT_MARK, SearchHit, SearchIndex
from second_brain_builder.search.text import markdown_to_text

MakePage = Callable[..., PurePosixPath]

BRAIN = "brain-a"


def paths(hits: list[SearchHit]) -> set[str]:
    return {str(hit.path) for hit in hits}


# --- Snippets render as text, with only the matches in bold ----------------------


class _Rendered(HTMLParser):
    """The tags, the bold texts, and the visible text of rendered HTML."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.bold: list[str] = []
        self._text: list[str] = []
        self._in_bold = False
        self.feed(html)
        self.close()

    @property
    def text(self) -> str:
        return " ".join("".join(self._text).split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        if tag == "strong":
            self._in_bold = True
            self.bold.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong":
            self._in_bold = False

    def handle_data(self, data: str) -> None:
        self._text.append(data)
        if self._in_bold:
            self.bold[-1] += data


def render(snippet: str) -> _Rendered:
    """``snippet`` as a CommonMark renderer that allows raw HTML shows it: the worst case."""
    return _Rendered(MarkdownIt("commonmark").render(snippet))


@pytest.mark.parametrize(
    ("body", "visible"),
    [
        ("Heron \\[x\\](https://evil.example/x)", "Heron [x](https://evil.example/x)"),
        (
            "Heron \\!\\[x\\](https://tracker.example/p.png)",
            "Heron ![x](https://tracker.example/p.png)",
        ),
        ("Heron \\<https://evil.example\\> \\[^1\\]", "Heron <https://evil.example> [^1]"),
        ("Heron &lt;img src=x onerror=alert(1)&gt;", "Heron <img src=x onerror=alert(1)>"),
        (
            "<div>\n&lt;script&gt;alert(1)&lt;/script&gt; Heron\n</div>\n",
            "<script>alert(1)</script> Heron",
        ),
        (
            "Write `<img src=x onerror=alert(1)>` for a Heron.",
            "Write <img src=x onerror=alert(1)> for a Heron.",
        ),
        (
            "In code, `2**3` gives Heron, and `a**b` raises.",
            "In code, 2**3 gives Heron, and a**b raises.",
        ),
        ("a ** b Heron ** c", "a ** b Heron ** c"),
        ("2\\*Heron\\*3", "2*Heron*3"),
        ("~~Heron~~ | \\_wing\\_ `x_y`", "~~Heron~~ | _wing_ x_y"),
        ("AT&amp;T Heron &amp;copy;", "AT&T Heron &copy;"),
        ("\\# Heron colony", "# Heron colony"),
        ("\\> Heron colony", "> Heron colony"),
        ("\\- Heron colony", "- Heron colony"),
        ("1\\. Heron colony", "1. Heron colony"),
        ("\\=== Heron", "=== Heron"),
    ],
)
def test_a_body_snippet_renders_as_the_page_text_with_only_the_match_in_bold(
    index: SearchIndex, tmp_path: Path, make_page: MakePage, body: str, visible: str
) -> None:
    root = tmp_path / "brain"
    make_page(root, "wiki/concepts/page.md", "concept", title="Page", body=body)
    index.rebuild(BRAIN, root)

    (hit,) = index.query(BRAIN, "heron")
    rendered = render(hit.snippet)

    assert rendered.tags == {"p", "strong"}, hit.snippet
    assert rendered.bold == ["Heron"], hit.snippet
    assert rendered.text == visible, hit.snippet
    assert hit.snippet.count(HIGHLIGHT_MARK) == 2


def test_a_description_snippet_renders_as_the_description_text(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    description = "<b>Plover</b> & [x](https://evil.example) *notes*"
    make_page(
        root, "wiki/entities/plover.md", "entity", title="Shore bird", description=description
    )
    index.rebuild(BRAIN, root)

    (hit,) = index.query(BRAIN, "plover")
    rendered = render(hit.snippet)

    assert rendered.tags == {"p", "strong"}, hit.snippet
    assert rendered.bold == ["Plover"]
    assert rendered.text == description


def test_a_snippet_without_a_match_renders_as_text(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    make_page(
        root,
        "wiki/entities/heron.md",
        "entity",
        title="Heron",
        body="<span>Tall</span> &amp;copy; [grey](https://evil.example) \\<b\\>wings\\</b\\>.",
    )
    index.rebuild(BRAIN, root)

    (hit,) = index.query(BRAIN, "heron")
    rendered = render(hit.snippet)

    assert rendered.tags == {"p"}, hit.snippet
    assert rendered.text == "Tall &copy; grey <b>wings</b>."


# --- Text that Python can hold but UTF-8 cannot encode ---------------------------


def test_lone_surrogates_in_frontmatter_do_not_stop_the_other_pages(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    good = make_page(root, "wiki/concepts/good.md", "concept", title="Good", body="A heron.")
    odd = PurePosixPath("wiki/entities/odd.md")
    # YAML escapes give a str with surrogates: a pair, and lone ones.
    (root / odd).parent.mkdir(parents=True)
    (root / odd).write_text(
        "---\n"
        "type: entity\n"
        'title: "\\ud83d\\ude42 Heron \\udfff"\n'
        'description: "Odd \\ud800 bird"\n'
        'tags: ["shore\\udc00"]\n'
        "---\n\nA heron.\n",
        encoding="utf-8",
    )

    index.rebuild("by-rebuild", root)
    index.update("by-update", root, [odd, good])

    for brain_id in ("by-rebuild", "by-update"):
        hits = index.query(brain_id, "heron")
        assert paths(hits) == {str(good), str(odd)}
        assert {hit.title for hit in hits} == {"Good", "🙂 Heron \ufffd"}
        assert paths(index.query(brain_id, "odd bird")) == {str(odd)}
    assert paths(index.query("by-rebuild", "heron\udcff")) == {str(good), str(odd)}


# --- Text that takes long to process ---------------------------------------------


def test_a_long_html_block_with_unclosed_tags_is_fast_to_index(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    make_page(
        root,
        "wiki/concepts/junk.md",
        "concept",
        title="Junk",
        # The HTML block ends at the blank line, and no ">" follows the "<a".
        body="<div>\n" + "<a " * 100_000 + "\n\nA heron.\n",
    )

    start = time.perf_counter()
    index.rebuild(BRAIN, root)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.5, f"rebuild took {elapsed:.1f} s"
    assert paths(index.query(BRAIN, "heron")) == {"wiki/concepts/junk.md"}


def test_a_long_run_of_footnote_openers_is_fast_to_index(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    make_page(
        root,
        "wiki/concepts/junk.md",
        "concept",
        title="Junk",
        # With a definition, each "[^" starts a footnote reference label that has
        # no "]": a scan to the end of the line from each one takes quadratic time.
        body="A heron " + "[^" * 30_000 + " ends.[^a]\n\n[^a]: A note.\n",
    )

    start = time.perf_counter()
    index.rebuild(BRAIN, root)
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0, f"rebuild took {elapsed:.1f} s"
    assert paths(index.query(BRAIN, "heron")) == {"wiki/concepts/junk.md"}


def test_footnotes_are_read_as_gfm_reads_them() -> None:
    body = "Match ^[a-z]+ here.[^Note]\n\n[^note]: The note.\n"

    # "^[a-z]+" is text (no inline footnotes), and "[^Note]" refers to "[^note]".
    assert markdown_to_text(body) == "Match ^[a-z]+ here.\nThe note.\n"


def test_a_frequent_word_in_long_bodies_is_fast_to_search(
    index: SearchIndex, tmp_path: Path, make_page: MakePage
) -> None:
    root = tmp_path / "brain"
    sentence = "The keeper said that the tide at the shore was the highest of the year. "
    for number in range(3):
        make_page(
            root,
            f"sources/logs/log-{number}/log-{number}.md",
            "source",
            title=f"Log {number}",
            body=sentence * 4000,
        )
    index.rebuild(BRAIN, root)

    # The search palette sends a query for the first letter that the user types.
    for text in ("t", "the"):
        start = time.perf_counter()
        hits = index.query(BRAIN, text)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.5, f"{text!r} took {elapsed:.1f} s"
        assert len(hits) == 3
        assert all("**The** keeper said" in hit.snippet for hit in hits)


def test_a_page_that_cannot_be_converted_does_not_stop_the_other_pages(
    index: SearchIndex, sample_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def convert(body: str) -> str:
        if "The grey heron stands still" in body:
            raise RecursionError("maximum recursion depth exceeded")
        return markdown_to_text(body)

    monkeypatch.setattr(index_module, "markdown_to_text", convert)

    index.rebuild(BRAIN, sample_brain)

    hits = paths(index.query(BRAIN, "fieldnote"))
    assert "wiki/entities/grey-heron.md" not in hits
    assert "wiki/concepts/wading.md" in hits
