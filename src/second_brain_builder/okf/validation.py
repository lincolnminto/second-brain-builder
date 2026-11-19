"""Validation of pages and bundles against OKF v0.3 and the Brain rules.

Errors break OKF v0.3 or a Brain rule that code relies on (see
docs/architecture.md, "OKF rules"). Warnings are advice: OKF consumers accept the
files, for example with broken links (OKF §6.1) or unknown keys (OKF §4.1).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Set
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from second_brain_builder.okf import _markdown, _yaml
from second_brain_builder.okf.links import is_external, resolve_link, split_href
from second_brain_builder.okf.log import DATE_HEADING_RE
from second_brain_builder.okf.models import (
    OKF_VERSION,
    WIKI_PAGE_TYPES,
    Frontmatter,
    Issue,
    IssueCode,
    OKFError,
    Page,
    Severity,
    parse_timestamp,
)
from second_brain_builder.okf.pages import normalize_text, parse_page, split_frontmatter

SPEC_CONCEPT_KEYS = frozenset(
    {"usage_window", "runtime", "parameters", "computation", "executor", "attester"}
)
"""Top-level keys that OKF v0.3 defines and that have no field in ``Frontmatter``."""

SPEC_SOURCE_KEYS = frozenset({"usage_count", "last_modified", "usage_window"})
"""``sources`` entry keys that OKF v0.3 defines and that have no field in ``SourceRef``."""

BUNDLE_METADATA_KEYS = frozenset({"okf_version", "title", "description", "license"})
"""The keys of the bundle-root ``index.md`` frontmatter (OKF §8.1)."""

_ERROR: Severity = "error"
_WARNING: Severity = "warning"


def _issue(path: PurePosixPath, code: IssueCode, severity: Severity, message: str) -> Issue:
    return Issue(path=path, code=code.value, message=message, severity=severity)


def validate_page(page: Page, existing_paths: set[PurePosixPath]) -> list[Issue]:
    """Check one page that should be a concept.

    ``existing_paths`` holds the bundle paths of all files; links and path fields
    are checked against them and the directories that contain them.

    Checks: ``type`` is present; Wiki Pages have ``sources``, every footnote label is
    a source id, and every source is cited; source ids are unique; timestamps carry
    an offset; extension keys use the ``x-`` prefix; links and path fields resolve;
    ``[[wikilinks]]`` are reported, because OKF does not treat them as links.
    """
    return _check_page(page, existing_paths, _directories(existing_paths))


def _directories(paths: Set[PurePosixPath]) -> frozenset[PurePosixPath]:
    return frozenset(parent for path in paths for parent in path.parents)


def _check_page(
    page: Page,
    existing_paths: Set[PurePosixPath],
    directories: Set[PurePosixPath],
) -> list[Issue]:
    issues: list[Issue] = []
    frontmatter = page.frontmatter
    if frontmatter is None or not frontmatter.type:
        issues.append(
            _issue(page.path, IssueCode.MISSING_TYPE, _ERROR, "The frontmatter has no type.")
        )
    found = _markdown.scan(page.body)
    if frontmatter is not None:
        issues += _check_sources(page.path, frontmatter, found)
        issues += _check_timestamps(page.path, frontmatter)
        issues += _check_extension_keys(page.path, frontmatter)
        issues += _check_path_fields(page.path, frontmatter, existing_paths, directories)
    issues += _check_links(page.path, found, existing_paths, directories)
    for wikilink in found.wikilinks:
        issues.append(
            _issue(
                page.path,
                IssueCode.WIKILINK,
                _WARNING,
                f"[[{wikilink.target}]] is not a link in OKF. Use a Markdown link with a "
                "/-rooted path.",
            )
        )
    return issues


def _check_sources(
    path: PurePosixPath, frontmatter: Frontmatter, found: _markdown.Scan
) -> list[Issue]:
    issues: list[Issue] = []
    ids = [ref.id for ref in frontmatter.sources]
    for source_id, count in Counter(ids).items():
        if count > 1:
            issues.append(
                _issue(
                    path,
                    IssueCode.DUPLICATE_SOURCE_ID,
                    _ERROR,
                    f"The source id {source_id!r} occurs {count} times in sources.",
                )
            )
    if frontmatter.type not in WIKI_PAGE_TYPES:
        return issues
    if not ids:
        issues.append(
            _issue(
                path,
                IssueCode.MISSING_SOURCES,
                _ERROR,
                f"A {frontmatter.type} page must list its Sources in sources.",
            )
        )
    cited = [ref.label for ref in found.footnote_refs]
    defined = {definition.label for definition in found.footnote_defs}
    labels = dict.fromkeys([*cited, *(definition.label for definition in found.footnote_defs)])
    for label in labels:
        if label not in ids:
            issues.append(
                _issue(
                    path,
                    IssueCode.UNKNOWN_CITATION,
                    _ERROR,
                    f"The footnote [^{label}] does not match a source id in sources.",
                )
            )
    for source_id in dict.fromkeys(ids):
        if source_id not in cited:
            issues.append(
                _issue(
                    path,
                    IssueCode.UNCITED_SOURCE,
                    _ERROR,
                    f"The source {source_id!r} is not cited with [^{source_id}] in the body.",
                )
            )
    for label in dict.fromkeys(cited):
        if label not in defined:
            issues.append(
                _issue(
                    path,
                    IssueCode.UNDEFINED_FOOTNOTE,
                    _WARNING,
                    f"The footnote [^{label}] has no definition line [^{label}]: ...",
                )
            )
    return issues


def _timestamp_values(frontmatter: Frontmatter) -> Iterator[tuple[str, Any]]:
    """Every timestamp key of OKF §5 in the frontmatter, with its value."""
    if frontmatter.generated is not None:
        yield "generated.at", frontmatter.generated.at
    for index, stamp in enumerate(frontmatter.verified):
        yield f"verified[{index}].at", stamp.at
    if frontmatter.stale_after is not None:
        yield "stale_after", frontmatter.stale_after
    yield from _usage_window("usage_window", frontmatter.extensions.get("usage_window"))
    for index, ref in enumerate(frontmatter.sources):
        last_modified = ref.extensions.get("last_modified")
        if last_modified not in (None, ""):
            yield f"sources[{index}].last_modified", last_modified
        yield from _usage_window(
            f"sources[{index}].usage_window", ref.extensions.get("usage_window")
        )


def _usage_window(key: str, window: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(window, Mapping):
        for bound in ("from", "to"):
            if window.get(bound) not in (None, ""):
                yield f"{key}.{bound}", window[bound]


def _check_timestamps(path: PurePosixPath, frontmatter: Frontmatter) -> list[Issue]:
    issues: list[Issue] = []
    for key, value in _timestamp_values(frontmatter):
        moment: datetime | None = None
        if isinstance(value, datetime):
            moment = value
        elif isinstance(value, str):
            try:
                moment = parse_timestamp(value)
            except ValueError:
                moment = None
        if moment is None:
            issues.append(
                _issue(
                    path,
                    IssueCode.INVALID_TIMESTAMP,
                    _ERROR,
                    f"{key} is not an RFC 3339 date-time or date: {value!r}.",
                )
            )
        elif moment.tzinfo is None:
            issues.append(
                _issue(
                    path,
                    IssueCode.TIMESTAMP_WITHOUT_OFFSET,
                    _ERROR,
                    f"{key} has no UTC offset. Add Z or +HH:MM: {moment.isoformat()}.",
                )
            )
    return issues


def _unprefixed(keys: Iterable[str], allowed: Set[str]) -> list[str]:
    return [key for key in keys if key not in allowed and not key.startswith("x-")]


def _check_extension_keys(path: PurePosixPath, frontmatter: Frontmatter) -> list[Issue]:
    found = [(key, key) for key in _unprefixed(list(frontmatter.extensions), SPEC_CONCEPT_KEYS)]
    for index, ref in enumerate(frontmatter.sources):
        found += [
            (f"sources[{index}].{key}", key)
            for key in _unprefixed(list(ref.extensions), SPEC_SOURCE_KEYS)
        ]
    stamps = [("generated", frontmatter.generated)] if frontmatter.generated else []
    stamps += [(f"verified[{index}]", stamp) for index, stamp in enumerate(frontmatter.verified)]
    for where, stamp in stamps:
        if stamp is not None:
            found += [(f"{where}.{key}", key) for key in _unprefixed(list(stamp.extensions), set())]
    return [
        _issue(
            path,
            IssueCode.UNPREFIXED_EXTENSION_KEY,
            _WARNING,
            f"{where} is not an OKF key. Name extension keys x-<producer>-<name>, "
            f"for example x-sbb-{key}.",
        )
        for where, key in found
    ]


def _exists(
    target: PurePosixPath, existing_paths: Set[PurePosixPath], directories: Set[PurePosixPath]
) -> bool:
    return target in existing_paths or target in directories


def _check_links(
    path: PurePosixPath,
    found: _markdown.Scan,
    existing_paths: Set[PurePosixPath],
    directories: Set[PurePosixPath],
) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[str] = set()
    for link in found.links:
        if link.href in seen:
            continue
        seen.add(link.href)
        target_path, _ = split_href(link.href)
        if not target_path:
            continue
        target = resolve_link(path, link.href)
        if target is not None and not _exists(target, existing_paths, directories):
            what = "Image" if link.kind == "image" else "Link"
            issues.append(
                _issue(
                    path,
                    IssueCode.BROKEN_LINK,
                    _WARNING,
                    f"{what} target {link.href} does not exist ({target}).",
                )
            )
    return issues


def _check_path_fields(
    path: PurePosixPath,
    frontmatter: Frontmatter,
    existing_paths: Set[PurePosixPath],
    directories: Set[PurePosixPath],
) -> list[Issue]:
    fields: list[tuple[str, str | None]] = [
        ("resource", frontmatter.resource),
        ("superseded_by", frontmatter.superseded_by),
    ]
    for index, ref in enumerate(frontmatter.sources):
        fields.append((f"sources[{index}].resource", ref.resource))
    issues: list[Issue] = []
    for key, value in fields:
        # A value with whitespace is a scope descriptor, not a path (OKF §5.1, §6).
        if not value or is_external(value) or any(char.isspace() for char in value):
            continue
        target = resolve_link(path, value)
        if target is not None and not _exists(target, existing_paths, directories):
            issues.append(
                _issue(
                    path,
                    IssueCode.BROKEN_LINK,
                    _WARNING,
                    f"{key} {value} does not exist ({target}).",
                )
            )
    return issues


def validate_bundle(root: Path) -> list[Issue]:
    """Check every file of the bundle at ``root``.

    Dot-paths are not part of a bundle and are skipped (OKF §3.2), and so are
    symbolic links: ``read_page`` and ``write_page`` do not follow them, because a
    link can lead to a dot-path or out of ``root``. A ``.md`` file without frontmatter
    (``AGENTS.md``, ``README.md``) is not a concept and is skipped; one with
    frontmatter but no ``type`` gets a warning. ``index.md`` and ``log.md`` are
    checked against OKF §8 and §9, and the root ``index.md`` must declare
    ``okf_version: "0.3"``. Every other ``.md`` file is checked with
    ``validate_page``. Issues come in path order.
    """
    files = sorted(_bundle_files(root))
    existing_paths = frozenset(files)
    directories = _directories(existing_paths)
    issues: list[Issue] = []
    root_index = PurePosixPath("index.md")
    if root_index not in existing_paths:
        issues.append(
            _issue(
                root_index,
                IssueCode.MISSING_INDEX,
                _ERROR,
                f'The bundle root has no index.md with okf_version: "{OKF_VERSION}".',
            )
        )
    for path in files:
        if path.suffix != ".md":
            continue
        try:
            text = (root / path).read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            issues.append(
                _issue(path, IssueCode.INVALID_ENCODING, _ERROR, "The file is not UTF-8 text.")
            )
            continue
        if path.name == "index.md":
            issues += _check_index(path, text, existing_paths, directories)
        elif path.name == "log.md":
            issues += _check_log(path, text)
        else:
            issues += _check_file(path, text, existing_paths, directories)
    return issues


def _bundle_files(root: Path) -> Iterator[PurePosixPath]:
    """Bundle paths of the regular files under ``root``, without dot-paths or symbolic links."""
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                yield PurePosixPath(entry.relative_to(root).as_posix())


def _check_file(
    path: PurePosixPath,
    text: str,
    existing_paths: Set[PurePosixPath],
    directories: Set[PurePosixPath],
) -> list[Issue]:
    try:
        page = parse_page(path, text)
    except OKFError as exc:
        return [_issue(path, IssueCode.INVALID_FRONTMATTER, _ERROR, str(exc))]
    if page.frontmatter is None:
        return []
    if not page.frontmatter.type:
        return [
            _issue(
                path,
                IssueCode.NOT_A_CONCEPT,
                _WARNING,
                "The file has frontmatter but no type, so it is not a concept. "
                "OKF consumers skip it.",
            )
        ]
    return _check_page(page, existing_paths, directories)


def _check_index(
    path: PurePosixPath,
    text: str,
    existing_paths: Set[PurePosixPath],
    directories: Set[PurePosixPath],
) -> list[Issue]:
    issues: list[Issue] = []
    block, body = split_frontmatter(normalize_text(text))
    if len(path.parts) > 1:
        if block is not None:
            issues.append(
                _issue(
                    path,
                    IssueCode.INDEX_FRONTMATTER,
                    _ERROR,
                    "Only the bundle-root index.md can have frontmatter.",
                )
            )
    else:
        issues += _check_bundle_metadata(path, block)
    issues += _check_links(path, _markdown.scan(body), existing_paths, directories)
    return issues


def _check_bundle_metadata(path: PurePosixPath, block: str | None) -> list[Issue]:
    try:
        metadata = _yaml.load_mapping(block, first_line=2) if block is not None else {}
    except OKFError as exc:
        return [_issue(path, IssueCode.INVALID_FRONTMATTER, _ERROR, str(exc))]
    version = metadata.get("okf_version")
    problem = None
    if version is None:
        problem = "The bundle-root index.md does not declare okf_version."
    elif not isinstance(version, str):
        problem = f'okf_version must be a quoted string, for example "{OKF_VERSION}".'
    elif version != OKF_VERSION:
        problem = f"okf_version is {version!r}; this Brain format is OKF {OKF_VERSION}."
    issues = []
    if problem is not None:
        issues.append(_issue(path, IssueCode.OKF_VERSION, _ERROR, problem))
    issues += [
        _issue(
            path,
            IssueCode.UNPREFIXED_EXTENSION_KEY,
            _WARNING,
            f"{key} is not an OKF bundle metadata key. Name extension keys "
            f"x-<producer>-<name>, for example x-sbb-{key}.",
        )
        for key in _unprefixed(list(metadata), BUNDLE_METADATA_KEYS)
    ]
    return issues


def _check_log(path: PurePosixPath, text: str) -> list[Issue]:
    issues: list[Issue] = []
    text = normalize_text(text)
    block, _ = split_frontmatter(text)
    if block is not None:
        issues.append(
            _issue(path, IssueCode.LOG_FRONTMATTER, _ERROR, "log.md must not have frontmatter.")
        )
    days: list[str] = []
    for line in text.split("\n"):
        if not line.startswith("## "):
            continue
        match = DATE_HEADING_RE.fullmatch(line)
        if match is None or not _is_date(match.group(1)):
            issues.append(
                _issue(
                    path,
                    IssueCode.LOG_DATE_HEADING,
                    _ERROR,
                    f"Log headings must be dates in the form ## YYYY-MM-DD: {line.strip()!r}.",
                )
            )
        else:
            days.append(match.group(1))
    if days != sorted(days, reverse=True):
        issues.append(
            _issue(path, IssueCode.LOG_ORDER, _WARNING, "Log dates are not newest first.")
        )
    return issues


def _is_date(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True
