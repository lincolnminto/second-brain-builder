"""Canonical Documents retain only portable Markdown destinations."""

import pytest

from second_brain_builder.conversion.links import sanitize_links


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/chapter.xhtml#note",
        "http://example.com",
        "HTTPS://example.com",
        "mailto:reader@example.com",
        "#chapter",
        "assets/figure.png",
        "assets/transcript.vtt",
        "assets/figure.png#detail",
    ],
)
def test_allowed_links_keep_their_bytes(target):
    markdown = f'[**label**](<{target}> "title") ![alt]({target})\n'
    assert sanitize_links(markdown) == markdown


@pytest.mark.parametrize(
    "target",
    [
        "/tmp/docling_epub_zrio1u1i/chapter.xhtml",
        "/tmp/docling_epub_hd3xi451/chapter.xhtml#anchor",
        "file:///tmp/chapter.xhtml",
        "chapter.xhtml#anchor",
        "../chapter.html",
        "/absolute/path",
        "//example.com/file",
        "ftp://example.com/file",
        "assets/../chapter.xhtml",
        "assets/%2e%2e",
        "assets/a%2fb.png",
        "assets/",
        "",
    ],
)
def test_other_links_and_images_become_their_text(target):
    assert sanitize_links(f'[**label**](<{target}> "title") ![alt]({target})') == ("**label** alt")


def test_nested_image_and_balanced_or_escaped_destinations():
    assert sanitize_links("[![alt](/tmp/a.png)](chapter.xhtml)") == "alt"
    assert sanitize_links("[![alt](assets/a.png)](chapter.xhtml)") == "![alt](assets/a.png)"
    assert sanitize_links(r"[one](chapter(1).xhtml) [two](chapter\(2\).xhtml)") == "one two"


def test_code_and_escaped_markdown_are_untouched():
    markdown = (
        "`[inline](/tmp/a.xhtml)`\n\n"
        "``![ticks](/tmp/a.png) ` ``\n\n"
        "```md\n[code](/tmp/a.xhtml)\n```\n\n"
        "~~~\n![code](/tmp/a.png)\n~~~\n\n"
        "    [indented](/tmp/a.xhtml)\n\n"
        r"\[escaped](/tmp/a.xhtml)"
    )
    assert sanitize_links(markdown) == markdown


def test_autolinks_and_reference_links():
    markdown = (
        "<https://example.com> <file:///tmp/a.xhtml>\n\n"
        "[Chapter][bad] ![picture][BAD] [bad][] [bad]\n\n"
        "[web][good] ![asset][image]\n\n"
        "`[code][bad]`\n\n```\n[code][bad]\n```\n\n"
        '[bad]: /tmp/chapter.xhtml "title"\n'
        "[good]: https://example.com\n"
        "[image]: assets/picture.png\n"
    )
    result = sanitize_links(markdown)
    assert "<https://example.com> file:///tmp/a.xhtml" in result
    assert "Chapter picture bad bad" in result
    assert "[web][good] ![asset][image]" in result
    assert "`[code][bad]`\n\n```\n[code][bad]\n```" in result
    assert "/tmp/chapter.xhtml" not in result
    assert sanitize_links(result) == result
