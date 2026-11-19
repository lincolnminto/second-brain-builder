import pytest

from second_brain_builder.brains import is_slug, slugify, unique_slug


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dune - Frank Herbert", "dune-frank-herbert"),
        ("Crème Brûlée: A History", "creme-brulee-a-history"),
        ("Straße und Ærø", "strasse-und-aero"),
        ("Don't Panic", "dont-panic"),
        ("Don\u2019t Panic", "dont-panic"),
        ("Dune\u2014Messiah", "dune-messiah"),
        ("  --Report__2024 (final).pdf--  ", "report-2024-final-pdf"),
        ("\ufb01le \u2116 5", "file-no-5"),
        ("\uff26\uff35\uff2c\uff2c \uff57\uff49\uff44\uff54\uff48", "full-width"),
    ],
)
def test_slugify_folds_unicode_to_lowercase_ascii(text: str, expected: str) -> None:
    assert slugify(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "!!!", "日本語", "\U0001f600"])
def test_slugify_never_returns_an_empty_slug(text: str) -> None:
    assert slugify(text) == "untitled"


@pytest.mark.parametrize(("text", "expected"), [("Index", "index-1"), ("LOG", "log-1")])
def test_slugify_avoids_names_that_okf_reserves(text: str, expected: str) -> None:
    # index.md and log.md are not pages at any level of an OKF bundle.
    assert slugify(text) == expected


def test_slugify_avoids_the_assets_name_that_gitattributes_keeps_out_of_lfs() -> None:
    assert slugify("Assets") == "assets-1"


def test_slugify_cuts_long_titles_at_a_word_boundary() -> None:
    slug = slugify("word " * 40)
    assert len(slug) <= 80
    assert slug.endswith("word")
    assert is_slug(slug)


def test_slugify_cuts_a_single_long_word() -> None:
    assert slugify("x" * 200) == "x" * 80


@pytest.mark.parametrize(
    "text",
    ["Dune - Frank Herbert", "Ærø", "日本語", "Index", "a" * 300, "--", "Kapitel 3: Über alles"],
)
def test_every_slugify_result_is_a_slug(text: str) -> None:
    assert is_slug(slugify(text))


def test_unique_slug_appends_the_first_free_number() -> None:
    assert unique_slug("dune", set()) == "dune"
    assert unique_slug("dune", {"dune"}) == "dune-2"
    assert unique_slug("dune", {"dune", "dune-2", "dune-3"}) == "dune-4"


@pytest.mark.parametrize("base", ["", "Dune", "dune--messiah", "-dune", "index", "../dune"])
def test_unique_slug_rejects_a_base_that_is_not_a_slug(base: str) -> None:
    with pytest.raises(ValueError, match="not a slug"):
        unique_slug(base, set())
