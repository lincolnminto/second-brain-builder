"""Validation of pages and bundles against OKF v0.3 and the Brain rules."""

import shutil
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.okf import Issue, parse_page, read_page, validate_bundle, validate_page

FIXTURE_BRAIN = Path(__file__).parents[1] / "fixtures" / "okf" / "brain"
AHAB = PurePosixPath("wiki/entities/captain-ahab.md")


def _existing_paths(root: Path) -> set[PurePosixPath]:
    return {
        PurePosixPath(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file() and not path.relative_to(root).parts[0].startswith(".")
    }


EXISTING = _existing_paths(FIXTURE_BRAIN)


def _findings(issues: list[Issue]) -> list[tuple[str, str, str]]:
    return [(str(issue.path), issue.code, issue.severity) for issue in issues]


def _validate(text: str, path: PurePosixPath = AHAB) -> list[tuple[str, str]]:
    return [
        (issue.code, issue.severity) for issue in validate_page(parse_page(path, text), EXISTING)
    ]


@pytest.fixture
def brain(tmp_path: Path) -> Path:
    """A copy of the fixture Brain that a test can change."""
    return Path(shutil.copytree(FIXTURE_BRAIN, tmp_path / "brain"))


def test_fixture_brain_has_no_issues() -> None:
    # The fixture has AGENTS.md without frontmatter (not a concept, no issue) and
    # .brain/scratch.md with broken YAML (a dot-path, not part of the bundle).
    assert validate_bundle(FIXTURE_BRAIN) == []


def test_fixture_wiki_page_has_no_issues() -> None:
    assert validate_page(read_page(FIXTURE_BRAIN, AHAB), EXISTING) == []


def test_page_without_type() -> None:
    text = "---\ntitle: Captain Ahab\n---\n\n# Captain Ahab\n"
    assert _validate(text) == [("missing-type", "error")]


def test_wiki_page_citations_must_match_sources() -> None:
    text = """\
---
type: entity
title: Captain Ahab
sources:
  - id: moby-dick
    resource: /sources/classics/moby-dick/moby-dick.md
  - id: gulliver
    resource: /sources/classics/moby-dick/moby-dick.md
---

Ahab commands the Pequod.[^moby-dick] He lost a leg to the whale.[^typee]

[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)
[^gulliver]: A definition alone does not cite the source.
"""
    issues = validate_page(parse_page(AHAB, text), EXISTING)

    assert [(issue.code, issue.severity) for issue in issues] == [
        ("unknown-citation", "error"),
        ("uncited-source", "error"),
        ("undefined-footnote", "warning"),
    ]
    assert "[^typee]" in issues[0].message
    assert "'gulliver'" in issues[1].message
    assert "[^typee]" in issues[2].message


def test_wiki_page_needs_sources() -> None:
    text = "---\ntype: concept\ntitle: Obsession\n---\n\nA fixed idea.[^moby-dick]\n"
    assert _validate(text, PurePosixPath("wiki/concepts/obsession.md")) == [
        ("missing-sources", "error"),
        ("unknown-citation", "error"),
        ("undefined-footnote", "warning"),
    ]


def test_source_ids_are_unique() -> None:
    text = """\
---
type: summary
sources:
  - {id: moby-dick, resource: /sources/classics/moby-dick/moby-dick.md}
  - {id: moby-dick, resource: /sources/classics/moby-dick/moby-dick.txt}
---

Summary.[^moby-dick]

[^moby-dick]: Moby-Dick
"""
    assert _validate(text, PurePosixPath("wiki/summaries/moby-dick.md")) == [
        ("duplicate-source-id", "error")
    ]


def test_canonical_document_footnotes_are_not_citations() -> None:
    # A faithful conversion keeps the footnotes of the original document.
    text = """\
---
type: source
title: Moby-Dick
resource: ./moby-dick.txt
---

The whale is a mammal.[^1]

[^1]: The narrator disagrees, in chapter 32.
"""
    assert _validate(text, PurePosixPath("sources/classics/moby-dick/moby-dick.md")) == []


def test_broken_links_and_path_fields_are_warnings() -> None:
    text = """\
---
type: entity
title: Captain Ahab
sources:
  - id: moby-dick
    resource: /sources/classics/moby-dick/moby-dick.md
  - id: typee
    resource: /sources/classics/typee/typee.md
  - id: archive
    resource: all whaling logs in the example archive
superseded_by: /wiki/entities/ahab.md
---

Ahab commands the Pequod.[^moby-dick][^typee][^archive] He meets [Ishmael](ishmael.md),
[Starbuck](/wiki/entities/starbuck.md), and the [crew](/wiki/entities/) on the
[voyage](#voyage). ![Pequod](/sources/classics/moby-dick/assets/pequod.png)
See also [the web](https://example.com/ahab.md) and [outside](../../../../ahab.md).

[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)
[^typee]: Typee
[^archive]: Archive
"""
    issues = validate_page(parse_page(AHAB, text), EXISTING)

    assert all(issue.code == "broken-link" and issue.severity == "warning" for issue in issues)
    assert [issue.message for issue in issues] == [
        "superseded_by /wiki/entities/ahab.md does not exist (wiki/entities/ahab.md).",
        "sources[1].resource /sources/classics/typee/typee.md does not exist "
        "(sources/classics/typee/typee.md).",
        "Link target /wiki/entities/starbuck.md does not exist (wiki/entities/starbuck.md).",
        "Image target /sources/classics/moby-dick/assets/pequod.png does not exist "
        "(sources/classics/moby-dick/assets/pequod.png).",
    ]


def test_extension_keys_need_the_x_prefix() -> None:
    text = """\
---
type: summary
owner: example-team
x-sbb-review: pending
runtime: bigquery
usage_window: {from: "2026-06-01T00:00:00Z", to: "2026-06-30T00:00:00Z"}
sources:
  - id: moby-dick
    resource: /sources/classics/moby-dick/moby-dick.md
    usage_count: 3
    pages: [1, 2]
generated: {by: sbb/test-model, at: "2026-11-18T12:00:00Z", tokens: 900}
verified:
  - {by: "human:example-reviewer", at: "2026-11-19T12:00:00Z", x-sbb-note: ok, note: ok}
---

Summary.[^moby-dick]

[^moby-dick]: Moby-Dick
"""
    issues = validate_page(parse_page(PurePosixPath("wiki/summaries/moby-dick.md"), text), EXISTING)

    assert all(
        issue.code == "unprefixed-extension-key" and issue.severity == "warning" for issue in issues
    )
    assert [issue.message.split(" ")[0] for issue in issues] == [
        "owner",
        "sources[0].pages",
        "generated.tokens",
        "verified[0].note",
    ]


def test_wikilinks_are_reported_outside_code() -> None:
    text = """\
---
type: source
title: Notes
---

Ahab is [[Captain Ahab]], see `[[not reported]]`.
"""
    issues = validate_page(
        parse_page(PurePosixPath("sources/notes/notes/notes.md"), text), EXISTING
    )

    assert [(issue.code, issue.severity) for issue in issues] == [("wikilink", "warning")]
    assert issues[0].message.startswith("[[Captain Ahab]]")


def test_timestamps_need_an_offset() -> None:
    text = """\
---
type: summary
sources:
  - id: moby-dick
    resource: /sources/classics/moby-dick/moby-dick.md
    last_modified: last spring
generated: {by: sbb/test-model, at: "2026-11-18T12:00:00"}
verified:
  - {by: "human:example-reviewer", at: "2026-11-19T12:00:00Z"}
stale_after: "2027-01-01"
usage_window: {from: "2026-06-01T00:00:00", to: 2026-06-30}
---

Summary.[^moby-dick]

[^moby-dick]: Moby-Dick
"""
    issues = validate_page(parse_page(PurePosixPath("wiki/summaries/moby-dick.md"), text), EXISTING)

    assert [(issue.code, issue.message.split(" ")[0]) for issue in issues] == [
        ("timestamp-without-offset", "generated.at"),
        ("timestamp-without-offset", "usage_window.from"),
        ("invalid-timestamp", "sources[0].last_modified"),
    ]
    assert all(issue.severity == "error" for issue in issues)


def test_bundle_without_root_index(brain: Path) -> None:
    (brain / "index.md").unlink()

    assert _findings(validate_bundle(brain)) == [("index.md", "missing-index", "error")]


@pytest.mark.parametrize(
    "version_line",
    ["", "okf_version: 0.3\n", 'okf_version: "0.2"\n'],
    ids=["missing", "not-a-string", "other-version"],
)
def test_root_index_declares_okf_version(brain: Path, version_line: str) -> None:
    index = brain / "index.md"
    index.write_text(index.read_text().replace('okf_version: "0.3"\n', version_line))

    assert _findings(validate_bundle(brain)) == [("index.md", "okf-version", "error")]


def test_markdown_files_that_are_not_concepts(brain: Path) -> None:
    (brain / "README.md").write_text("---\ntitle: Read me\n---\n\nAbout this Brain.\n")
    (brain / "CONTRIBUTING.md").write_text("# Contributing\n\nNo frontmatter, no issue.\n")
    (brain / "wiki" / "index.md").write_text("---\ntitle: Wiki\n---\n\n# Wiki\n")
    (brain / "wiki" / "log.md").write_text(
        "---\ntitle: Wiki log\n---\n\n## 2026-11-01\n* **Add**: x\n## Yesterday\n"
        "## 2026-11-02\n* **Add**: y\n"
    )

    assert _findings(validate_bundle(brain)) == [
        ("README.md", "not-a-concept", "warning"),
        ("wiki/index.md", "index-frontmatter", "error"),
        ("wiki/log.md", "log-frontmatter", "error"),
        ("wiki/log.md", "log-date-heading", "error"),
        ("wiki/log.md", "log-order", "warning"),
    ]


def test_pages_that_cannot_be_read(brain: Path) -> None:
    ishmael = brain / "wiki" / "entities" / "ishmael.md"
    ishmael.write_text(ishmael.read_text().replace("tags: [character]", "tags: [character"))
    (brain / "wiki" / "concepts" / "whaling.md").write_bytes(
        "---\ntype: concept\ntitle: P\xeache\n---\n".encode("latin-1")
    )
    # Hostile frontmatter in one file, for example in a pulled repo, is an issue of
    # that file: it does not stop the validation of the others.
    hostile = brain / "sources" / "classics" / "hostile"
    hostile.mkdir()
    (hostile / "alias.md").write_text("---\ntype: source\nx-a: &a [*a]\n---\n")
    (hostile / "number.md").write_text("---\ntype: source\nx-a: " + "9" * 5000 + "\n---\n")
    (hostile / "nesting.md").write_text("---\ntype: source\nx-a: " + "[" * 3000 + "\n---\n")

    issues = validate_bundle(brain)

    assert _findings(issues) == [
        ("sources/classics/hostile/alias.md", "invalid-frontmatter", "error"),
        ("sources/classics/hostile/nesting.md", "invalid-frontmatter", "error"),
        ("sources/classics/hostile/number.md", "invalid-frontmatter", "error"),
        ("wiki/concepts/whaling.md", "invalid-encoding", "error"),
        ("wiki/entities/ishmael.md", "invalid-frontmatter", "error"),
    ]
    assert "not valid YAML" in issues[4].message


def test_deleted_source_leaves_broken_links(brain: Path) -> None:
    shutil.rmtree(brain / "sources" / "classics" / "moby-dick")

    issues = validate_bundle(brain)

    # log.md is history: its entries can name deleted files, so its links are not checked.
    assert {issue.code for issue in issues} == {"broken-link"}
    assert sorted({str(issue.path) for issue in issues}) == [
        "index.md",
        "wiki/concepts/obsession.md",
        "wiki/entities/captain-ahab.md",
        "wiki/entities/ishmael.md",
        "wiki/summaries/moby-dick.md",
    ]


def test_symbolic_links_are_skipped(brain: Path, tmp_path: Path) -> None:
    # read_page and write_page do not follow links, so validation does not either:
    # a link can lead out of the bundle, to a dot-path, or round in a loop.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "broken.md").write_text("---\ntype: [broken\n---\n")
    (brain / "wiki" / "outside").symlink_to(outside, target_is_directory=True)
    (brain / "wiki" / "secret.md").symlink_to(outside / "broken.md")
    (brain / "wiki" / "scratch.md").symlink_to("../.brain/scratch.md")  # broken YAML
    (brain / "wiki" / "brain").symlink_to("../.brain", target_is_directory=True)
    (brain / "wiki" / "loop.md").symlink_to("loop.md")

    assert validate_bundle(brain) == []
