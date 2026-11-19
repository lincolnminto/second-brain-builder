import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from second_brain_builder.brains import (
    BrainLayout,
    BrainSettings,
    BrainSettingsError,
    read_settings,
    scaffold_brain,
    write_index,
)
from second_brain_builder.okf import parse_page, validate_bundle

RunGit = Callable[..., str]


def test_scaffold_writes_a_brain_that_passes_okf_validation(
    tmp_path: Path, brain_settings: BrainSettings
) -> None:
    root = tmp_path / "brain"
    scaffold_brain(root, brain_settings)

    assert validate_bundle(root) == []
    assert read_settings(root) == brain_settings
    assert (root / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert (root / ".gitattributes").read_text() == (
        "sources/** filter=lfs diff=lfs merge=lfs -text\n"
        "sources/**/*.md !filter !diff !merge text\n"
        "sources/**/assets/** !filter !diff !merge\n"
    )
    index = parse_page(PurePosixPath("index.md"), (root / "index.md").read_text())
    assert index.frontmatter is not None
    assert index.frontmatter.extensions["okf_version"] == "0.3"
    assert index.frontmatter.title == "Example Notes"
    assert (root / "log.md").read_text() == "# Log\n\n## 2026-11-18\n* **Create**: Example Notes\n"


def test_source_files_go_to_lfs_and_a_markdown_source_is_not_an_okf_page(
    tmp_path: Path, brain_settings: BrainSettings, run_git: RunGit
) -> None:
    root = tmp_path / "brain"
    scaffold_brain(root, brain_settings)
    run_git(root, "init", "--quiet")
    layout = BrainLayout()
    folder = PurePosixPath("journal")
    note = layout.source_file_path(folder, "trip", "md")
    scan = layout.source_file_path(folder, "scan", "pdf")
    canonical = layout.canonical_path(folder, "trip")
    captions = layout.assets_dir(folder, "trip") / "trip.vtt"
    (root / note).parent.mkdir(parents=True)
    # The user's note has frontmatter of its own and an undefined footnote. A Source
    # never changes, so OKF must not read it as a page that has to be fixed.
    (root / note).write_text("---\ntype: journal\ntitle: Trip\n---\n\nA note.[^later]\n")

    paths = [str(path) for path in (note, scan, canonical, captions)]
    assert run_git(root, "check-attr", "filter", "--", *paths).splitlines() == [
        f"{note}: filter: lfs",
        f"{scan}: filter: lfs",
        f"{canonical}: filter: unspecified",
        f"{captions}: filter: unspecified",
    ]
    assert validate_bundle(root) == []


def test_agents_md_is_rendered_from_the_template(
    tmp_path: Path, brain_settings: BrainSettings
) -> None:
    root = tmp_path / "brain"
    scaffold_brain(root, brain_settings)

    agents = (root / "AGENTS.md").read_text()
    assert agents.startswith("# Example Notes\n")
    assert "`en`" in agents
    assert "$" not in agents
    # AGENTS.md is not an OKF page: no frontmatter, so no type.
    assert parse_page(PurePosixPath("AGENTS.md"), agents).frontmatter is None


def test_scaffold_does_not_overwrite_an_existing_file(
    tmp_path: Path, brain_settings: BrainSettings
) -> None:
    root = tmp_path / "brain"
    root.mkdir()
    (root / "index.md").write_text("# Someone else's index\n")

    with pytest.raises(FileExistsError, match=r"index\.md"):
        scaffold_brain(root, brain_settings)

    assert (root / "index.md").read_text() == "# Someone else's index\n"
    assert sorted(path.name for path in root.iterdir()) == ["index.md"]


@pytest.mark.parametrize("link", ["index.md", ".brain"])
def test_scaffold_does_not_write_through_a_symlink(
    tmp_path: Path, brain_settings: BrainSettings, link: str
) -> None:
    root = tmp_path / "brain"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # A dangling link: Path.exists() is False for it.
    (root / link).symlink_to(outside / "missing")

    with pytest.raises(FileExistsError, match=re.escape(link)):
        scaffold_brain(root, brain_settings)

    assert list(outside.iterdir()) == []
    assert [path.name for path in root.iterdir()] == [link]


@pytest.mark.parametrize(
    "name", ["Notes: 1984 # draft", "yes", "0.3", 'Física "quoted" \\ notes', "- list?"]
)
def test_settings_round_trip_names_that_yaml_could_misread(tmp_path: Path, name: str) -> None:
    settings = BrainSettings(
        name=name,
        language="pt-BR",
        created_at=datetime(2026, 11, 18, 9, 30, tzinfo=timezone(timedelta(hours=-3))),
    )
    scaffold_brain(tmp_path, settings)
    assert read_settings(tmp_path) == settings


def test_read_settings_accepts_a_hand_edited_unquoted_timestamp(tmp_path: Path) -> None:
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "brain.yaml").write_text(
        'okf_version: "0.3"\nschema_version: 1\nname: Notes\n'
        "created_at: 2026-11-18T12:00:00+02:00\nx-future-key: kept out\n"
    )
    settings = read_settings(tmp_path)
    assert settings.created_at == datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
    assert settings.created_at.utcoffset() == timedelta(hours=2)
    assert settings.language == "en"


@pytest.mark.parametrize(
    "data",
    [
        b"name: [unclosed\n",
        b"- a list\n",
        b'name: Notes\ncreated_at: "2026-11-18T12:00:00"\n',  # no UTC offset
        b'name: Notes\nlanguage: "not a tag!"\ncreated_at: "2026-11-18T12:00:00Z"\n',
        b'name: "  "\ncreated_at: "2026-11-18T12:00:00Z"\n',
        b'name: "Notes \xff"\ncreated_at: "2026-11-18T12:00:00Z"\n',  # not UTF-8
    ],
)
def test_read_settings_rejects_invalid_files(tmp_path: Path, data: bytes) -> None:
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "brain.yaml").write_bytes(data)
    with pytest.raises(BrainSettingsError):
        read_settings(tmp_path)


def test_read_settings_needs_the_settings_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_settings(tmp_path)


def test_read_settings_does_not_read_through_a_symlink(
    tmp_path: Path, brain_settings: BrainSettings
) -> None:
    # The settings of another Brain must not leak into this one's index.md.
    other = tmp_path / "other"
    scaffold_brain(other, brain_settings)
    root = tmp_path / "brain"
    (root / ".brain").mkdir(parents=True)
    (root / ".brain" / "brain.yaml").symlink_to(other / ".brain" / "brain.yaml")

    with pytest.raises(BrainSettingsError, match="symbolic link"):
        read_settings(root)


def test_write_index_lists_every_page_and_keeps_the_index_metadata(sample_brain: Path) -> None:
    write_index(sample_brain)

    text = (sample_brain / "index.md").read_text()
    index = parse_page(PurePosixPath("index.md"), text)
    assert index.frontmatter is not None
    assert index.frontmatter.title == "Example Field Notes"
    assert index.frontmatter.description == "Synthetic notes about imaginary coastal birds."
    assert index.frontmatter.extensions["license"] == "CC0-1.0"
    assert index.body == (
        "# Sources\n\n"
        "* [Coastal Birds](/sources/nature/coastal-birds/coastal-birds.md) - "
        "Plain text, 2 sections.\n"
        "* [Trip Notes](/sources/journal/trip-notes/trip-notes.md) - Markdown, 1 section.\n\n"
        "# Summaries\n\n"
        "* [Coastal Birds](/wiki/summaries/coastal-birds.md) - Summary of Coastal Birds, "
        "a short guide to imaginary shore birds.\n\n"
        "# Entities\n\n"
        "* [Grey Heron](/wiki/entities/grey-heron.md) - A tall wading bird of the Example "
        "Islands.\n\n"
        "# Concepts\n\n"
        "* [Wading](/wiki/concepts/wading.md) - Feeding while walking in shallow water.\n\n"
        "# Comparisons\n\n"
        "* [heron-vs-egret](/wiki/comparisons/heron-vs-egret.md)\n"
    )


@pytest.mark.parametrize("data", [b"---\ntitle: [unclosed\n---\n", b"---\ntitle: \xff\xfe\n---\n"])
def test_write_index_replaces_frontmatter_that_cannot_be_read(
    sample_brain: Path, data: bytes
) -> None:
    (sample_brain / "index.md").write_bytes(data)
    write_index(sample_brain)

    index = parse_page(PurePosixPath("index.md"), (sample_brain / "index.md").read_text())
    assert index.frontmatter is not None
    assert index.frontmatter.title == "Example Field Notes"
    assert index.frontmatter.description == "Sources and the wiki pages written from them."


def test_write_index_replaces_a_symlink_and_never_writes_through_it(
    sample_brain: Path, tmp_path: Path
) -> None:
    # A pushed commit can turn index.md into a link to a file outside the Brain,
    # such as the key file of the data volume.
    key_file = tmp_path / "data" / "secret.key"
    key_file.parent.mkdir()
    key_file.write_text("synthetic-key\n")
    (sample_brain / "index.md").unlink()
    (sample_brain / "index.md").symlink_to("../data/secret.key")

    write_index(sample_brain)

    assert key_file.read_text() == "synthetic-key\n"
    assert not (sample_brain / "index.md").is_symlink()
    index = parse_page(PurePosixPath("index.md"), (sample_brain / "index.md").read_text())
    assert index.frontmatter is not None
    assert index.frontmatter.title == "Example Field Notes"
    assert index.frontmatter.description == "Sources and the wiki pages written from them."
    assert [path.name for path in sample_brain.iterdir() if path.name.endswith(".tmp")] == []
