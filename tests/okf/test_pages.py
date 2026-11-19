"""Reading and writing OKF pages: frontmatter, canonical output, and file access."""

import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from second_brain_builder.okf import (
    Frontmatter,
    OKFError,
    Page,
    PageType,
    SourceRef,
    Stamp,
    parse_page,
    parse_timestamp,
    read_page,
    render_page,
    write_page,
)
from second_brain_builder.okf import pages as page_io

FIXTURE_BRAIN = Path(__file__).parents[1] / "fixtures" / "okf" / "brain"
AHAB = PurePosixPath("wiki/entities/captain-ahab.md")
MOBY_DICK_DOC = "/sources/classics/moby-dick/moby-dick.md"


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "log.md",
        "sources/classics/moby-dick/moby-dick.md",
        "wiki/summaries/moby-dick.md",
        "wiki/entities/captain-ahab.md",
        "wiki/concepts/obsession.md",
    ],
)
def test_canonical_files_read_and_render_byte_for_byte(path: str) -> None:
    # A page that code wrote and did not change must not create a git diff.
    page = read_page(FIXTURE_BRAIN, PurePosixPath(path))
    assert render_page(page) == (FIXTURE_BRAIN / path).read_text(encoding="utf-8")


def test_page_built_in_code_renders_canonical_frontmatter() -> None:
    page = Page(
        path=PurePosixPath("wiki/syntheses/1984-and-moby-dick.md"),
        frontmatter=Frontmatter(
            type=PageType.SYNTHESIS,
            title="1984",
            description="Why: obsession drives the plot.",
            tags=["theme", "yes"],
            sources=[SourceRef(id="moby-dick", resource=MOBY_DICK_DOC, title="Moby-Dick")],
            generated=Stamp(
                by="sbb/test-model", at=datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
            ),
            verified=[
                Stamp(
                    by="human:example-reviewer",
                    at=datetime(2026, 11, 19, 9, 30, tzinfo=timezone(timedelta(hours=-3))),
                )
            ],
            status="draft",
            stale_after=datetime(2027, 1, 1, tzinfo=timezone.utc),
            extensions={
                "x-sbb-answer": {
                    "question": "Why does Ahab hunt the whale?",
                    "asked_at": datetime(2026, 11, 18, 11, 59, tzinfo=timezone.utc),
                }
            },
        ),
        body=f"# Obsession\n\nAhab hunts the whale.[^moby-dick]\n\n"
        f"[^moby-dick]: [Moby-Dick]({MOBY_DICK_DOC})\n",
    )

    # Strings that YAML 1.1 or 1.2 could read as numbers, booleans, or timestamps
    # are quoted (OKF §4), and timestamps keep their offset.
    assert render_page(page) == (
        "---\n"
        "type: synthesis\n"
        'title: "1984"\n'
        'description: "Why: obsession drives the plot."\n'
        'tags: [theme, "yes"]\n'
        "sources:\n"
        "  - id: moby-dick\n"
        f"    resource: {MOBY_DICK_DOC}\n"
        "    title: Moby-Dick\n"
        'generated: {by: sbb/test-model, at: "2026-11-18T12:00:00+00:00"}\n'
        "verified:\n"
        '  - {by: "human:example-reviewer", at: "2026-11-19T09:30:00-03:00"}\n'
        "status: draft\n"
        'stale_after: "2027-01-01T00:00:00+00:00"\n'
        "x-sbb-answer:\n"
        "  question: Why does Ahab hunt the whale?\n"
        '  asked_at: "2026-11-18T11:59:00+00:00"\n'
        "---\n"
        "\n"
        "# Obsession\n"
        "\n"
        "Ahab hunts the whale.[^moby-dick]\n"
        "\n"
        f"[^moby-dick]: [Moby-Dick]({MOBY_DICK_DOC})\n"
    )
    assert parse_page(page.path, render_page(page)).frontmatter == page.frontmatter.model_copy(
        update={
            "extensions": {
                "x-sbb-answer": {
                    "question": "Why does Ahab hunt the whale?",
                    "asked_at": "2026-11-18T11:59:00+00:00",
                }
            }
        }
    )


UNKNOWN_KEYS_PAGE = """\
---
type: entity
x-acme-owner: team:research
title: Captain Ahab
sources:
  - id: moby-dick
    resource: /sources/classics/moby-dick/moby-dick.md
    usage_count: 12
    x-sbb-pages: [3, 7]
generated: {by: sbb/test-model, at: "2026-11-18T12:00:00Z", x-sbb-tokens: 1200}
verified:
  - {by: "human:example-reviewer", at: "2026-11-19T08:00:00Z", x-sbb-note: spot check}
usage_window: {from: "2026-06-01T00:00:00Z", to: "2026-06-30T00:00:00Z"}
runtime: bigquery
legacy_flag: on
x-sbb-review:
  status: pending
  notes:
    - first pass
    - null
---
Ahab hunts the white whale.[^moby-dick]

[^moby-dick]: [Moby-Dick](/sources/classics/moby-dick/moby-dick.md)
"""


def test_unknown_keys_round_trip_at_every_level() -> None:
    page = parse_page(AHAB, UNKNOWN_KEYS_PAGE)

    assert page.frontmatter is not None
    assert page.frontmatter.extensions == {
        "x-acme-owner": "team:research",
        "usage_window": {"from": "2026-06-01T00:00:00Z", "to": "2026-06-30T00:00:00Z"},
        "runtime": "bigquery",
        "legacy_flag": "on",  # YAML 1.2: "on" is a string, not a boolean
        "x-sbb-review": {"status": "pending", "notes": ["first pass", None]},
    }
    assert page.frontmatter.sources[0].extensions == {"usage_count": 12, "x-sbb-pages": [3, 7]}
    assert page.frontmatter.generated is not None
    assert page.frontmatter.generated.extensions == {"x-sbb-tokens": 1200}
    assert page.frontmatter.verified[0].extensions == {"x-sbb-note": "spot check"}

    rendered = render_page(page)
    assert parse_page(AHAB, rendered) == page
    assert render_page(parse_page(AHAB, rendered)) == rendered


def test_values_are_read_with_the_okf_consumer_rules() -> None:
    page = parse_page(
        PurePosixPath("wiki/entities/pequod.md"),
        "---\n"
        "type: entity\n"
        "title: 1851\n"
        "description: ''\n"
        "tags: ship\n"
        "sources: {id: moby-dick, resource: /sources/classics/moby-dick/moby-dick.md}\n"
        "generated: {by: sbb/test-model, at: 2026-11-18T12:00:00+02:00}\n"
        "verified: {by: 'human:example-reviewer', at: 2026-11-19}\n"
        "status: ~\n"
        "stale_after:\n"
        "---\n",
    )

    # OKF §11.2: one item where a list is expected is a list with one item, and
    # null and empty values are absent. A date only is the start of the day in UTC.
    assert page.frontmatter == Frontmatter(
        type="entity",
        title="1851",
        tags=["ship"],
        sources=[SourceRef(id="moby-dick", resource=MOBY_DICK_DOC)],
        generated=Stamp(
            by="sbb/test-model",
            at=datetime(2026, 11, 18, 12, tzinfo=timezone(timedelta(hours=2))),
        ),
        verified=[
            Stamp(by="human:example-reviewer", at=datetime(2026, 11, 19, tzinfo=timezone.utc))
        ],
    )
    assert page.body == ""


def test_timestamp_without_offset_is_kept_for_validation_to_report() -> None:
    text = "---\ntype: concept\nstale_after: 2027-01-01T00:00:00\n---\n\nText.\n"
    page = parse_page(PurePosixPath("wiki/concepts/whaling.md"), text)

    assert page.frontmatter is not None
    assert page.frontmatter.stale_after == datetime(2027, 1, 1)  # naive: no offset invented
    assert 'stale_after: "2027-01-01T00:00:00"' in render_page(page)


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    [
        ("type: entity\ntitle: [Ahab\n", "not valid YAML"),
        ("type: entity\ntype: concept\n", "duplicate key"),
        ("- type: entity\n", "not a YAML mapping"),
        ("type: entity\nsources:\n  - resource: /sources/a.md\n", "sources.0.id"),
        ("type: entity\nsources: [moby-dick]\n", "sources[0] must be a mapping"),
        ("type: entity\ngenerated: {at: 2026-11-18}\n", "generated.by"),
        ("type: entity\nstale_after: next spring\n", "stale_after"),
        ("type: entity\nstale_after: 2026\n", "stale_after"),
        ("type: entity\ntitle: {first: Captain}\n", "title must be a string"),
        ("type: entity\nx-sbb-data: !!binary aGVsbG8=\n", "outside the core schema"),
        ("type: entity\nx-sbb-data: {[a, b]: c}\n", "key outside the core schema"),
        ("type: entity\nx-sbb-data: !custom value\n", "not valid YAML"),
        # Frontmatter is untrusted: none of these may raise anything but OKFError.
        ("type: entity\nx-sbb-a: &a [*a]\n", "anchors or aliases (line 3)"),
        ("type: entity\ntitle: &t Ahab\ndescription: *t\n", "anchors or aliases (line 3)"),
        ("type: entity\nx-sbb-deep: " + "[" * 3000 + "]" * 3000 + "\n", "deeper than 32"),
        ("type: entity\nx-sbb-n: " + "7" * 5000 + "\n", "cannot be read"),
        ("type: entity\nx-sbb-n: !!int seven\n", "cannot be read"),
        ("type: entity\nx-sbb-n: !!int ''\n", "cannot be read"),
        ("type: entity\nx-sbb-b: !!bool maybe\n", "cannot be read"),
    ],
)
def test_frontmatter_that_cannot_be_read_raises_okf_error(frontmatter: str, message: str) -> None:
    with pytest.raises(OKFError, match=re.escape(message)):
        parse_page(AHAB, f"---\n{frontmatter}---\nBody.\n")


def test_alias_bomb_is_rejected_before_it_expands() -> None:
    # Each level lists the one before it nine times: 9**12 strings in about 600
    # bytes. A reader that walks or renders the shared references never ends.
    levels = ["x-sbb-l0: &l0 [" + ", ".join(["whale"] * 9) + "]"]
    levels += [f"x-sbb-l{n}: &l{n} [" + ", ".join([f"*l{n - 1}"] * 9) + "]" for n in range(1, 12)]
    text = "---\ntype: entity\n" + "\n".join(levels) + "\n---\n"

    with pytest.raises(OKFError, match="anchors or aliases"):
        parse_page(AHAB, text)


@pytest.mark.parametrize("value", ["?todo", "? draft", "??", "?%x", "?: x", ":x", "?"])
def test_values_that_start_with_a_yaml_indicator_round_trip_in_flow_collections(
    value: str,
) -> None:
    # In [...] and {...}, a plain "?x" starts a key, so the value must be quoted.
    page = Page(
        path=PurePosixPath("wiki/syntheses/open-questions.md"),
        frontmatter=Frontmatter(
            type="synthesis",
            tags=[value, "theme"],
            generated=Stamp(
                by=value,
                at=datetime(2026, 11, 18, 12, tzinfo=timezone.utc),
                extensions={"x-sbb-note": value, value: "key", "x-sbb-list": [value]},
            ),
            extensions={"x-sbb-flags": {value: value}},
        ),
        body="Text.\n",
    )

    rendered = render_page(page)

    assert parse_page(page.path, rendered) == page
    assert render_page(parse_page(page.path, rendered)) == rendered


def test_tag_that_starts_with_a_question_mark_is_quoted() -> None:
    page = Page(path=AHAB, frontmatter=Frontmatter(type="entity", tags=["?todo", "-draft"]))

    assert 'tags: ["?todo", -draft]\n' in render_page(page)


def test_yaml_error_reports_the_file_line() -> None:
    text = "---\ntype: entity\ntitle: Captain Ahab\ntitle: Ahab\n---\n"
    with pytest.raises(OKFError, match=r"duplicate key .* \(line 4\)"):
        parse_page(AHAB, text)


@pytest.mark.parametrize(
    "body",
    [
        "---\n\nPart one.\n\n---\n\nPart two.\n",
        "---\nstatus: draft\n---\nNotes.\n",
        "\ufeff---\r\nstatus: draft\r\n---\r\n",
    ],
)
def test_page_without_frontmatter_cannot_have_a_body_that_reads_as_frontmatter(
    tmp_path: Path, body: str
) -> None:
    # No file has this page: the body would read back as frontmatter, or fail.
    page = Page(path=PurePosixPath("notes.md"), frontmatter=None, body=body)

    with pytest.raises(ValueError, match="without frontmatter"):
        render_page(page)
    with pytest.raises(ValueError, match="without frontmatter"):
        write_page(tmp_path, page)
    assert not (tmp_path / "notes.md").exists()


@pytest.mark.parametrize(
    "text",
    [
        "# Example Brain\n\nStart with index.md.\n",
        "---\n\nA thematic break first, and no closing fence.\n",
        "--- \ntype: entity\n---\nThe first line is not exactly three dashes.\n",
    ],
)
def test_text_without_a_frontmatter_block_is_all_body(text: str) -> None:
    page = parse_page(PurePosixPath("AGENTS.md"), text)

    assert page.frontmatter is None
    assert page.body == text
    assert render_page(page) == text


def test_frontmatter_without_type_keeps_its_keys() -> None:
    # The bundle-root index.md has frontmatter but no type: it is not a concept.
    text = '---\nokf_version: "0.3"\ntitle: Example Brain\nlicense: CC0-1.0\n---\n\n# Sources\n'
    page = parse_page(PurePosixPath("index.md"), text)

    assert page.frontmatter is not None
    assert page.frontmatter.type == ""
    assert page.frontmatter.extensions == {"okf_version": "0.3", "license": "CC0-1.0"}
    assert parse_page(page.path, render_page(page)) == page


def test_byte_order_mark_and_crlf_line_endings_are_normalized() -> None:
    lf = "---\ntype: concept\ntitle: Whaling\n---\n\nA [link](/wiki/entities/ishmael.md).\n"
    crlf = "\ufeff" + lf.replace("\n", "\r\n")

    assert parse_page(PurePosixPath("wiki/concepts/whaling.md"), crlf) == parse_page(
        PurePosixPath("wiki/concepts/whaling.md"), lf
    )


def test_extension_keys_cannot_repeat_a_field() -> None:
    # The page would render with a duplicate YAML key.
    with pytest.raises(ValidationError, match="title"):
        Frontmatter(type="entity", title="Ahab", extensions={"title": "Captain Ahab"})


def test_write_page_creates_directories_and_reads_back(tmp_path: Path) -> None:
    original = read_page(FIXTURE_BRAIN, AHAB)

    write_page(tmp_path, original)

    assert (tmp_path / AHAB).read_bytes() == (FIXTURE_BRAIN / AHAB).read_bytes()
    assert read_page(tmp_path, AHAB) == original


@pytest.mark.parametrize("path", [".git/hooks/pre-commit.md", ".brain/brain.md", "wiki/.hidden.md"])
def test_dot_paths_are_outside_the_bundle(tmp_path: Path, path: str) -> None:
    page = Page(path=PurePosixPath(path), frontmatter=Frontmatter(type="concept"))

    with pytest.raises(ValueError, match="not a path inside the bundle"):
        write_page(tmp_path, page)
    with pytest.raises(ValueError, match="not a path inside the bundle"):
        read_page(tmp_path, PurePosixPath(path))
    assert not (tmp_path / path).exists()


@pytest.mark.parametrize("path", ["../outside.md", "/etc/okf.md", "wiki/../../outside.md"])
def test_page_paths_must_be_relative_to_the_bundle_root(path: str) -> None:
    with pytest.raises(ValidationError, match="relative to the bundle root"):
        Page(path=PurePosixPath(path), frontmatter=None)


def test_write_page_does_not_follow_a_symlink_out_of_the_bundle(tmp_path: Path) -> None:
    root = tmp_path / "brain"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "wiki").symlink_to(outside, target_is_directory=True)
    page = Page(path=PurePosixPath("wiki/escape.md"), frontmatter=Frontmatter(type="concept"))

    with pytest.raises(ValueError, match="symbolic link"):
        write_page(root, page)
    assert list(outside.iterdir()) == []


@pytest.fixture
def cloned_brain(tmp_path: Path) -> Path:
    """A Brain clone with a git config, a root index, and a Canonical Document."""
    root = tmp_path / "brain"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n\tbare = false\n")
    (root / "index.md").write_text('---\nokf_version: "0.3"\ntitle: Example Brain\n---\n')
    (root / "sources" / "notes").mkdir(parents=True)
    (root / "sources" / "notes" / "notes.md").write_text("---\ntype: source\n---\n\nText.\n")
    (root / "wiki" / "entities").mkdir(parents=True)
    return root


@pytest.mark.parametrize(
    ("link", "target", "path"),
    [
        # A file link to git internals, which git checks out from a pulled repo.
        ("wiki/entities/ahab.md", "../../.git/config", "wiki/entities/ahab.md"),
        # A file link to a file that code writes and the LLM must not touch.
        ("wiki/entities/ahab.md", "../../index.md", "wiki/entities/ahab.md"),
        # A directory link into the Sources.
        ("wiki/concepts", "../sources/notes", "wiki/concepts/notes.md"),
        # A link to itself: a loop.
        ("wiki/entities/loop.md", "loop.md", "wiki/entities/loop.md"),
    ],
)
def test_symlinks_inside_the_bundle_are_not_followed(
    cloned_brain: Path, link: str, target: str, path: str
) -> None:
    (cloned_brain / link).symlink_to(target)
    files = sorted(file for file in cloned_brain.rglob("*") if file.is_file())
    before = {file: file.read_bytes() for file in files}
    page = Page(
        path=PurePosixPath(path),
        frontmatter=Frontmatter(type="entity", title="Captain Ahab"),
        body="Text.\n",
    )

    with pytest.raises(ValueError, match="symbolic link"):
        read_page(cloned_brain, PurePosixPath(path))
    with pytest.raises(ValueError, match="symbolic link"):
        write_page(cloned_brain, page)
    assert {file: file.read_bytes() for file in files} == before


def test_write_page_replaces_the_file_in_one_step(tmp_path: Path) -> None:
    # A reader that has the old page open (the app container reads clones while
    # the worker writes them) reads the whole old page, never a truncated one.
    original = read_page(FIXTURE_BRAIN, AHAB)
    write_page(tmp_path, original)
    page_file = tmp_path / AHAB
    page_file.chmod(0o640)
    old_text = page_file.read_bytes()
    changed = original.model_copy(update={"body": "Ahab is gone.\n"})

    with page_file.open("rb") as reader:
        write_page(tmp_path, changed)
        assert reader.read() == old_text

    assert read_page(tmp_path, AHAB) == changed
    assert stat.S_IMODE(page_file.stat().st_mode) == 0o640
    assert sorted(path.name for path in page_file.parent.iterdir()) == [AHAB.name]


def test_read_page_rejects_text_that_is_not_utf8(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_bytes(
        "---\ntype: concept\ntitle: Caf\xe9\n---\n".encode("latin-1")
    )

    with pytest.raises(OKFError, match="not UTF-8"):
        read_page(tmp_path, PurePosixPath("notes.md"))


@pytest.mark.parametrize("replace_parent", [False, True])
def test_read_page_rejects_symlinks_inserted_after_path_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_parent: bool
) -> None:
    root = tmp_path / "brain"
    directory = root / "wiki"
    directory.mkdir(parents=True)
    (directory / "notes.md").write_text("Inside.\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text("Outside.\n")
    original = page_io.bundle_file

    def swap_after_check(root: Path, path: PurePosixPath) -> Path:
        target = original(root, path)
        if replace_parent:
            directory.rename(root / "old-wiki")
            directory.symlink_to(outside, target_is_directory=True)
        else:
            target.unlink()
            target.symlink_to(outside / "notes.md")
        return target

    monkeypatch.setattr(page_io, "bundle_file", swap_after_check)
    with pytest.raises(ValueError, match="symbolic link"):
        read_page(root, PurePosixPath("wiki/notes.md"))


@pytest.mark.parametrize("offset", ["+00:60", "-00:60", "+00:99", "-22:99"])
def test_invalid_offset_minutes_are_rejected(offset: str) -> None:
    timestamp = f"2026-11-18T12:00:00{offset}"
    with pytest.raises(ValueError):
        parse_timestamp(timestamp)
    with pytest.raises(OKFError, match="RFC 3339"):
        parse_page(AHAB, f'---\ntype: entity\ngenerated: {{by: test, at: "{timestamp}"}}\n---\n')
