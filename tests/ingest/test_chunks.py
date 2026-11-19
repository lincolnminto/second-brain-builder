"""split_into_chunks: a Canonical Document body cut on headings into chunks of bounded size."""

import pytest

from second_brain_builder.ingest import split_into_chunks


def words(text: str) -> list[str]:
    return text.split()


def test_a_short_document_is_one_chunk_with_all_its_text() -> None:
    body = "# Field Guide\n\nIntro.\n\n## One\n\nFirst.\n\n\n\n## Two\n\nSecond.\n"

    chunks = split_into_chunks(body)

    assert len(chunks) == 1
    assert words(chunks[0].text) == words(body)


def test_whole_sections_are_packed_and_every_chunk_starts_at_a_heading() -> None:
    sections = [f"## Section {n}\n\n" + f"Sentence {n}. " * 30 for n in range(10)]
    body = "Preface text.\n\n" + "\n\n".join(sections)

    chunks = split_into_chunks(body, max_chars=1200)

    assert len(chunks) > 2
    assert all(len(chunk.text) <= 1200 for chunk in chunks)
    assert all(chunk.text.startswith("## Section ") for chunk in chunks[1:])
    assert words(" ".join(chunk.text for chunk in chunks)) == words(body)
    for n in range(10):
        assert sum(f"## Section {n}\n" in chunk.text for chunk in chunks) == 1


def test_hash_lines_in_fenced_code_are_not_headings() -> None:
    fence = "```bash\n# install the tools\nmake build\n```"
    body = "# Guide\n\n" + "intro " * 10 + "\n\n" + fence + "\n\n## Next\n\nmore text"

    chunks = split_into_chunks(body, max_chars=120)

    assert len(chunks) == 2
    assert fence in chunks[0].text
    assert chunks[1].headings == ("# Guide", "## Next")


def test_a_long_section_is_split_between_paragraphs_and_keeps_its_heading_trail() -> None:
    paragraphs = [f"Paragraph {n} " + "word " * 40 for n in range(20)]
    body = "# Book\n\n## Long Chapter\n\n" + "\n\n".join(paragraphs) + "\n\n### Short\n\nEnd."

    chunks = split_into_chunks(body, max_chars=1000)

    assert all(len(chunk.text) <= 1000 for chunk in chunks)
    # The short section before the long one starts the chunk of its first paragraphs.
    assert chunks[0].headings == ("# Book",)
    assert chunks[0].text.startswith("# Book\n\n## Long Chapter\n\nParagraph 0 ")
    assert len(chunks) > 2
    assert all(chunk.headings == ("# Book", "## Long Chapter") for chunk in chunks[1:])
    for chunk in chunks:
        for block in chunk.text.split("\n\n"):
            assert (
                block in {"# Book", "## Long Chapter", "### Short", "End."} or block in paragraphs
            )
    # The last paragraphs of the long section share a chunk with the section after it.
    assert chunks[-1].text.startswith("Paragraph ")
    assert chunks[-1].text.endswith("\n\n### Short\n\nEnd.")
    assert words(" ".join(chunk.text for chunk in chunks)) == words(body)


@pytest.mark.parametrize("lines", [27, 70], ids=["fits-a-chunk-alone", "longer-than-a-chunk"])
def test_a_heading_is_never_a_chunk_of_its_own_before_a_paragraph_that_does_not_fit_after_it(
    lines: int,
) -> None:
    paragraph = "\n".join(f"Line {n:02} of the interview transcript." for n in range(lines))
    body = "# Interview\n\n" + paragraph + "\n\n## Next\n\nEnd."
    assert len("# Interview\n\n" + paragraph) > 1000
    assert (len(paragraph) <= 1000) is (lines == 27)

    chunks = split_into_chunks(body, max_chars=1000)

    assert all(len(chunk.text) <= 1000 for chunk in chunks)
    assert chunks[0].text.startswith("# Interview\n\nLine 00 of the interview transcript.\nLine 01")
    line = len("Line 00 of the interview transcript.\n")
    assert all(len(chunk.text) > 1000 - line for chunk in chunks[:-1])
    assert chunks[-1].text.endswith(" transcript.\n\n## Next\n\nEnd.")
    assert words(" ".join(chunk.text for chunk in chunks)) == words(body)


def test_a_line_longer_than_a_chunk_is_cut_inside_the_line() -> None:
    body = "x" * 2500

    chunks = split_into_chunks(body, max_chars=1000)

    assert [len(chunk.text) for chunk in chunks] == [1000, 1000, 500]
    assert "".join(chunk.text for chunk in chunks) == body


def test_a_body_without_text_gives_no_chunks() -> None:
    assert split_into_chunks("") == []
    assert split_into_chunks(" \n\n\t\n") == []
