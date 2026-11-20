"""The claim behind a footnote reference: its sentence, its locator, and its key terms.

``services.citations`` resolves a footnote reference of a Wiki Page to the passage
of the cited Canonical Document: it takes the sentence that holds the reference
(``cited_sentence``), reads a locator in parentheses (``parse_locator``), and
queries the passage index (``SearchIndex.find_passages``) with the key terms of the
sentence (``key_terms``).

Page text is untrusted: everything here is plain text that goes into FTS5 only as
escaped strings.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from markdown_it.token import Token

from second_brain_builder.search.text import markdown_tokens

MAX_KEY_TERMS = 24
"""The most key terms that ``key_terms`` gives."""

# clean_text (in markdown_tokens) turns every NUL of the body into a space, so a
# NUL in the inline text marks the reference.
_MARK = "\x00"
_TEXT_TOKENS = frozenset({"text", "text_special", "code_inline"})
_LINE_BREAK_TOKENS = frozenset({"softbreak", "hardbreak"})

# The end of a sentence: end punctuation, closing quotes or brackets, the marked
# reference right after them, and then whitespace or the end of the text. A match
# starts only at the first mark of a run: a start at each mark of a long run, such
# as "....x", would scan the rest of the run each time, in quadratic time.
_SENTENCE_END_RE = re.compile(
    r"(?<![.!?\u2026])[.!?\u2026]+[\"'\u201d\u2019\u00bb)\]]*\x00?(?:\s+|$)"
)
_PARENTHESES_RE = re.compile(r"\(([^()]{1,200})\)")
# "11:1-9" → "11", "3.2" → "3", "3-5" → "3": a heading has the first number only.
_NUMBER_RANGE_RE = re.compile(
    r"(\d+)(?:\s*[:.,]\s*\d+)?(?:\s*[-\u2013\u2014]\s*\d+(?:\s*[:.,]\s*\d+)?)?"
)
_WORD_RE = re.compile(r"[^\W_]+")

# Words that name a kind of division in a locator ("chapter 3"), folded.
_LOCATOR_KEYWORDS = frozenset(
    {
        # English
        "act", "art", "article", "articles", "book", "books", "canto", "ch", "chap",
        "chapter", "chapters", "item", "para", "paragraph", "part", "parts", "scene",
        "sec", "sect", "section", "sections", "verse", "verses", "vol", "volume", "vv",
        # Portuguese and Spanish
        "artigo", "articulo", "cap", "capitulo", "capitulos", "libro", "livro", "livros",
        "paragrafo", "parte", "partes", "secao", "secoes", "seccion", "versiculo",
        "versiculos",
        # French and German
        "abschnitt", "chapitre", "kapitel", "livre", "partie", "teil", "verset",
    }
)  # fmt: skip
# Words that give a page or a line: their numbers are never in a heading.
_PAGE_KEYWORDS = frozenset(
    {
        "f", "ff", "fol", "folio", "l", "line", "linha", "linhas", "lines", "ll", "p",
        "pag", "page", "pages", "pagina", "paginas", "pg", "pp", "s", "seite",
    }
)  # fmt: skip
# Common words, folded, that carry no topic: left out of key terms and locators.
_STOPWORDS = frozenset(
    {
        # English
        "about", "after", "again", "also", "and", "any", "are", "because", "been",
        "before", "being", "both", "but", "can", "compare", "could", "did", "does",
        "each", "for", "from", "had", "has", "have", "her", "hers", "him", "his", "how",
        "into", "its", "may", "more", "most", "not", "now", "only", "other", "our",
        "out", "over", "see", "she", "should", "some", "such", "than", "that", "the",
        "their", "them", "then", "there", "these", "they", "this", "those", "through",
        "too", "under", "very", "was", "were", "what", "when", "where", "which", "while",
        "who", "whom", "why", "will", "with", "would", "you", "your",
        # Portuguese
        "aos", "aquela", "aquele", "aquilo", "as", "com", "como", "das", "dela", "dele",
        "depois", "dos", "ela", "elas", "ele", "eles", "entre", "essa", "esse", "esta",
        "este", "foi", "isso", "isto", "mais", "mas", "mesmo", "muito", "nao",
        "nas", "nem", "nos", "num", "numa", "onde", "para", "pela", "pelas", "pelo",
        "pelos", "por", "porque", "qual", "quando", "que", "quem", "sao", "seu", "seus",
        "sem", "sob", "sobre", "sua", "suas", "tambem", "tem", "uma", "umas", "uns",
        "veja", "ver", "vide",
        # Spanish
        "del", "las", "los", "pero", "segun", "son", "unos",
        # Latin abbreviations
        "cf", "cfr", "ibid", "idem",
        # Short function words, for locators ("Book of Tides 3")
        "am", "an", "at", "by", "da", "de", "do", "du", "el", "em", "im", "in", "la",
        "le", "na", "no", "of", "on", "or", "ou", "to",
    }
)  # fmt: skip


@dataclass(frozen=True)
class CitedSentence:
    """The sentence that holds one footnote reference, as plain text."""

    label: str
    """The footnote label, as the reference writes it. In a Wiki Page, it is the id
    of the cited Source; compare it without case, as GFM does."""
    sentence: str
    """The sentence, on one line, without footnote references."""
    claim: str
    """The sentence without its locator: the text to take key terms from."""
    locator: str | None
    """The text of the locator in parentheses, such as ``Gênesis 11:1-9``, if any."""
    definition_href: str | None
    """The destination of the first link in the footnote definition, if any."""


@dataclass(frozen=True)
class Locator:
    """A locator in parentheses, as lowercase words without diacritics."""

    names: tuple[str, ...]
    """Words that name a part of the document, such as ``genesis``."""
    keywords: tuple[str, ...]
    """Words for a kind of division, such as ``chapter``."""
    numbers: tuple[str, ...]
    """The first number of each number or range: ``11:1-9`` gives ``11``."""

    def heading_word_sets(self) -> list[tuple[str, ...]]:
        """The words that a matching heading trail contains, most specific first.

        Every word, then without the keywords (a heading can be "3. Tides" and not
        "Chapter 3"), then the names only (the heading can write the number in
        another way, such as "XI").
        """
        candidates = [
            (*self.names, *self.keywords, *self.numbers),
            (*self.names, *self.numbers),
            self.names if self.numbers else (),
        ]
        unique = dict.fromkeys(candidate for candidate in candidates if candidate)
        return list(unique)


def cited_sentence(body: str, ref: int) -> CitedSentence | None:
    """The sentence of footnote reference number ``ref`` (from 0) in a Markdown body.

    References count in the order of the rendered page: first the references in
    the body, then the references in the footnote definitions, which a renderer
    shows at the end. A ``[^label]`` without a definition is text, not a reference.
    Labels match without case, as GFM matches them, and ``label`` is the label as
    the reference writes it. ``^[...]`` is text: GFM has no inline footnotes.

    A sentence ends at ``.``, ``!``, ``?``, or ``…`` followed by whitespace, outside
    parentheses. A reference right after the end of a sentence belongs to that
    sentence ("…fish.[^a] It…"); a reference inside a sentence takes the whole
    sentence. The locator is the last text in parentheses in the sentence that
    ``parse_locator`` accepts.

    Returns:
        The sentence, or None when the body has no reference number ``ref``.
    """
    if ref < 0:
        return None
    found: tuple[Token, int] | None = None
    target_id: int | None = None
    label = ""
    hrefs: dict[int, str] = {}
    definition: int | None = None
    count = 0
    for token in markdown_tokens(body):
        if token.type == "footnote_open":
            definition = int(token.meta["id"])
        elif token.type == "footnote_close":
            definition = None
        elif token.type == "inline":
            for position, child in enumerate(token.children or []):
                if child.type == "footnote_ref":
                    if count == ref:
                        found = (token, position)
                        target_id = int(child.meta["id"])
                        label = str(child.meta["label"])
                    count += 1
                elif child.type == "link_open" and definition is not None:
                    hrefs.setdefault(definition, str(child.attrs.get("href", "")))
    if found is None or target_id is None:
        return None
    inline, position = found
    sentence = _sentence_at(_inline_text(inline.children or [], position))
    locator_match = None
    for match in _PARENTHESES_RE.finditer(sentence):
        if parse_locator(match.group(1)) is not None:
            locator_match = match
    if locator_match is None:
        claim, locator = sentence, None
    else:
        claim = sentence[: locator_match.start()] + " " + sentence[locator_match.end() :]
        claim, locator = " ".join(claim.split()), " ".join(locator_match.group(1).split())
    return CitedSentence(
        label=label,
        sentence=sentence,
        claim=claim,
        locator=locator,
        definition_href=hrefs.get(target_id) or None,
    )


def parse_locator(text: str) -> Locator | None:
    """The locator in ``text``, the text between parentheses, or None if it is not one.

    A locator has a number and a name or a division keyword: ``Gênesis 11:1-9``,
    ``chapter 3``, ``2 Samuel 7``. A year alone (``1990``) is not a locator. A page
    or line locator (``p. 42``) gives only its names, because headings do not hold
    page numbers.
    """
    folded = _fold(text)
    if not any(char.isdigit() for char in folded):
        return None
    words = _WORD_RE.findall(_NUMBER_RANGE_RE.sub(r" \1 ", folded))
    names: list[str] = []
    keywords: list[str] = []
    numbers: list[str] = []
    pages = False
    for word in words:
        if word[0].isdigit():
            numbers.append(word)
        elif word in _PAGE_KEYWORDS:
            pages = True
        elif word in _LOCATOR_KEYWORDS:
            keywords.append(word)
        elif len(word) >= 2 and word not in _STOPWORDS:
            names.append(word)
    if not names and not keywords:
        return None
    if pages:
        if not names:
            return None
        keywords, numbers = [], []
    return Locator(
        names=tuple(dict.fromkeys(names)),
        keywords=tuple(dict.fromkeys(keywords)),
        numbers=tuple(dict.fromkeys(numbers)),
    )


def key_terms(text: str, limit: int = MAX_KEY_TERMS) -> list[str]:
    """The distinct topic words of ``text``, lowercase, in order.

    Words of at least three letters that are not numbers or common words. When
    there are more than ``limit``, the longest ones stay. When none is left, the
    words of two letters or more are used instead.
    """
    words = _WORD_RE.findall(unicodedata.normalize("NFC", text).lower())
    terms = [
        word
        for word in words
        if len(word) >= 3 and not word.isdigit() and _fold(word) not in _STOPWORDS
    ]
    if not terms:
        terms = [word for word in words if len(word) >= 2 and not word.isdigit()]
    unique = list(dict.fromkeys(terms))
    if len(unique) <= limit:
        return unique
    longest = sorted(range(len(unique)), key=lambda index: -len(unique[index]))[:limit]
    return [unique[index] for index in sorted(longest)]


def _fold(text: str) -> str:
    """Lowercase, without diacritics, as the index tokenizer compares words."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _inline_text(children: Sequence[Token], target: int) -> str:
    """The plain text of inline tokens, with ``_MARK`` for the child at ``target``."""
    parts: list[str] = []
    for position, child in enumerate(children):
        if child.type in _TEXT_TOKENS:
            parts.append(child.content)
        elif child.type in _LINE_BREAK_TOKENS:
            parts.append(" ")
        elif child.type == "image":
            parts.append(_inline_text(child.children or [], -1))
        elif position == target:
            parts.append(_MARK)
    return "".join(parts)


def _sentence_at(text: str) -> str:
    """The sentence of ``text`` that holds ``_MARK``, on one line and without the mark."""
    depth = 0
    depths: list[int] = []
    for char in text:
        depths.append(depth)
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
    mark = text.index(_MARK)
    start, end = 0, len(text)
    for match in _SENTENCE_END_RE.finditer(text):
        if depths[match.start()] > 0:
            continue
        if match.end() <= mark:
            start = match.end()
        elif match.end() > mark:
            end = match.end()
            break
    return " ".join(text[start:end].replace(_MARK, "").split())
