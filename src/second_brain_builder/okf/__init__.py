"""OKF v0.3: frontmatter models, page read and write, links, index.md, log.md, and validation.

This package is the only code that knows the Open Knowledge Format spec. See
docs/architecture.md ("OKF rules" and "Module contracts") for how Brains use it.
"""

from second_brain_builder.okf.citations import cited_labels, footnote_labels, remove_citations
from second_brain_builder.okf.index import SECTION_TITLES, render_index
from second_brain_builder.okf.links import (
    apply_moves,
    bundle_href,
    extract_links,
    is_external,
    remove_links,
    resolve_link,
    rewrite_links,
)
from second_brain_builder.okf.log import LOG_TITLE, LogKind, log_entry, prepend_log
from second_brain_builder.okf.models import (
    OKF_VERSION,
    RESERVED_FILENAMES,
    WIKI_PAGE_TYPES,
    Frontmatter,
    Issue,
    IssueCode,
    OKFError,
    Page,
    PageType,
    Severity,
    SourceRef,
    Stamp,
    Timestamp,
    is_concept,
    parse_timestamp,
)
from second_brain_builder.okf.pages import parse_page, read_page, render_page, write_page
from second_brain_builder.okf.validation import validate_bundle, validate_page

__all__ = [
    "LOG_TITLE",
    "OKF_VERSION",
    "RESERVED_FILENAMES",
    "SECTION_TITLES",
    "WIKI_PAGE_TYPES",
    "Frontmatter",
    "Issue",
    "IssueCode",
    "LogKind",
    "OKFError",
    "Page",
    "PageType",
    "Severity",
    "SourceRef",
    "Stamp",
    "Timestamp",
    "apply_moves",
    "bundle_href",
    "cited_labels",
    "extract_links",
    "footnote_labels",
    "is_concept",
    "is_external",
    "log_entry",
    "parse_page",
    "parse_timestamp",
    "prepend_log",
    "read_page",
    "remove_citations",
    "remove_links",
    "render_index",
    "render_page",
    "resolve_link",
    "rewrite_links",
    "validate_bundle",
    "validate_page",
    "write_page",
]
