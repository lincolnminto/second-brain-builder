"""Create a new Brain, read its settings, and keep its index.md current."""

from __future__ import annotations

import contextlib
import io
import os
import secrets
import stat
from importlib import resources
from pathlib import Path, PurePosixPath
from string import Template
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from second_brain_builder.brains.layout import INDEX_PATH, LOG_PATH, SETTINGS_PATH
from second_brain_builder.brains.models import BrainSettings
from second_brain_builder.brains.pages import read_pages
from second_brain_builder.okf import (
    LogKind,
    OKFError,
    log_entry,
    parse_page,
    prepend_log,
    render_index,
)

GITATTRIBUTES = (
    "sources/** filter=lfs diff=lfs merge=lfs -text\n"
    "sources/**/*.md !filter !diff !merge text\n"
    "sources/**/assets/** !filter !diff !merge\n"
)
"""``.gitattributes`` of a Brain: Sources in Git LFS; Markdown and Assets in plain git.

``!attr`` makes an attribute unspecified again, so git diffs and merges these
files as text. ``-diff`` and ``-merge`` would mark them as binary.
"""

INDEX_DESCRIPTION = "Sources and the wiki pages written from them."
"""The ``description`` of a new ``index.md``."""

AGENTS_PATH = PurePosixPath("AGENTS.md")
CLAUDE_PATH = PurePosixPath("CLAUDE.md")
GITATTRIBUTES_PATH = PurePosixPath(".gitattributes")

_TEMPLATES = resources.files("second_brain_builder.brains") / "templates"


class BrainSettingsError(ValueError):
    """``.brain/brain.yaml`` is not valid YAML or does not match ``BrainSettings``."""


class _SettingsConstructor(SafeConstructor):
    """Keeps timestamps as strings, as in YAML 1.2; ``BrainSettings`` parses them."""


_SettingsConstructor.add_constructor(
    "tag:yaml.org,2002:timestamp", _SettingsConstructor.construct_yaml_str
)


def scaffold_brain(root: Path, settings: BrainSettings) -> None:
    """Write the files of a new Brain into ``root``, typically a fresh clone.

    Writes ``AGENTS.md`` and ``CLAUDE.md`` from the templates in
    ``brains/templates/``, ``index.md`` (with ``okf.render_index``; it lists pages
    that are already there), ``log.md`` with a Create entry, ``.gitattributes``,
    and ``.brain/brain.yaml``. Nothing is committed.

    Raises:
        FileExistsError: One of these files exists already, also as a symbolic
            link, or its path goes through a symbolic link (``.brain``). Nothing
            was written.
    """
    targets = [AGENTS_PATH, CLAUDE_PATH, INDEX_PATH, LOG_PATH, GITATTRIBUTES_PATH, SETTINGS_PATH]
    existing = [path.as_posix() for path in targets if _occupied(root, path)]
    if existing:
        raise FileExistsError(f"{root} already has Brain files: {', '.join(existing)}")
    template_values = {"brain_name": settings.name, "language": settings.language}
    _write(root, AGENTS_PATH, _render_template("AGENTS.md", template_values))
    _write(root, CLAUDE_PATH, _render_template("CLAUDE.md", template_values))
    _write(root, GITATTRIBUTES_PATH, GITATTRIBUTES)
    _write(root, SETTINGS_PATH, _dump_settings(settings))
    log = prepend_log("", settings.created_at.date(), [log_entry(LogKind.CREATE, settings.name)])
    _write(root, LOG_PATH, log)
    write_index(root)


def read_settings(root: Path) -> BrainSettings:
    """Read ``.brain/brain.yaml`` of the Brain at ``root``.

    Raises:
        FileNotFoundError: The file does not exist; ``root`` is not a Brain.
        BrainSettingsError: The file is not UTF-8, not valid YAML, or not valid
            settings, or it or ``.brain`` is a symbolic link.
    """
    if _through_symlink(root, SETTINGS_PATH):
        raise BrainSettingsError(f"{SETTINGS_PATH} goes through a symbolic link")
    try:
        text = (root / SETTINGS_PATH).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrainSettingsError(f"{SETTINGS_PATH} is not UTF-8 text") from exc
    yaml = YAML(typ="safe", pure=True)
    yaml.Constructor = _SettingsConstructor
    try:
        data: Any = yaml.load(text)
    except YAMLError as exc:
        raise BrainSettingsError(f"{SETTINGS_PATH} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BrainSettingsError(f"{SETTINGS_PATH} is not a YAML mapping")
    try:
        return BrainSettings.model_validate(data)
    except ValidationError as exc:
        raise BrainSettingsError(f"{SETTINGS_PATH} has invalid settings: {exc}") from exc


def write_index(root: Path) -> None:
    """Write ``index.md`` again from the pages of the Brain at ``root``.

    The title is the Brain name from ``.brain/brain.yaml``. The description and
    any other frontmatter keys of the current ``index.md`` stay. If the current
    frontmatter cannot be read (also when the file is not UTF-8), a new one
    replaces it. A symbolic link at ``index.md`` is never read or written
    through: a regular file replaces it.

    Raises:
        FileNotFoundError: ``root`` has no ``.brain/brain.yaml``.
        BrainSettingsError: ``.brain/brain.yaml`` is not valid.
    """
    settings = read_settings(root)
    previous = _read_index(root)
    description = INDEX_DESCRIPTION
    if previous is not None:
        try:
            frontmatter = parse_page(INDEX_PATH, previous).frontmatter
        except OKFError:
            previous = None
        else:
            if frontmatter is not None and frontmatter.description:
                description = frontmatter.description
    text = render_index(settings.name, description, read_pages(root), previous)
    _write(root, INDEX_PATH, text)


def _render_template(name: str, values: dict[str, str]) -> str:
    template = (_TEMPLATES / name).read_text(encoding="utf-8")
    return Template(template).substitute(values)


def _dump_settings(settings: BrainSettings) -> str:
    mapping = CommentedMap()
    mapping["okf_version"] = DoubleQuotedScalarString(settings.okf_version)
    mapping["schema_version"] = settings.schema_version
    mapping["name"] = DoubleQuotedScalarString(settings.name)
    mapping["language"] = DoubleQuotedScalarString(settings.language)
    mapping["created_at"] = DoubleQuotedScalarString(settings.created_at.isoformat())
    yaml = YAML(typ="rt", pure=True)
    yaml.allow_unicode = True
    yaml.width = 1_000_000  # never fold long lines
    buffer = io.StringIO()
    yaml.dump(mapping, buffer)
    return buffer.getvalue()


def _read_index(root: Path) -> str | None:
    """The text of ``index.md``; ``None`` if it is missing, not a regular file, or not UTF-8."""
    index_file = root / INDEX_PATH
    # A link from a pushed commit can point at any file, also outside the Brain.
    if index_file.is_symlink() or not index_file.is_file():
        return None
    try:
        return index_file.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def _through_symlink(root: Path, path: PurePosixPath) -> bool:
    """True when ``root / path``, or a directory on the way to it, is a symbolic link."""
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _occupied(root: Path, path: PurePosixPath) -> bool:
    return _through_symlink(root, path) or (root / path).exists()


def _write(root: Path, path: PurePosixPath, text: str) -> None:
    """Write ``text`` to ``root / path`` without following a symbolic link.

    The text goes to a temporary file in the same directory, which then replaces
    the target in one step. A symbolic link at the target is replaced, never
    followed, and a reader sees the old file or the new file.

    Raises:
        ValueError: A directory on the way to the target is a symbolic link.
    """
    if _through_symlink(root, path.parent):
        raise ValueError(f"{path} goes through a symbolic link")
    data = text.encode("utf-8")
    target = root.joinpath(*path.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A dot name: the Brain code and OKF skip dot-paths, so no reader takes it for a page.
    temporary = target.with_name(f".sbb-{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        with contextlib.suppress(FileNotFoundError):
            current = target.lstat()
            if stat.S_ISREG(current.st_mode):  # keep the mode of a file that is replaced
                temporary.chmod(stat.S_IMODE(current.st_mode))
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
