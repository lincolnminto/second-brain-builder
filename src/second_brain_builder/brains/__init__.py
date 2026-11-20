"""Brain repos: layout, slugs, scaffold, git and Git LFS operations, page reading, and the graph.

See docs/architecture.md ("Brain repo layout" and "Module contracts") for the
contract that this package implements.
"""

from second_brain_builder.brains.folders import FOLDER_MARKER, folder_filter, kept_folders
from second_brain_builder.brains.git import (
    DEFAULT_BRANCH,
    GitConflictError,
    GitError,
    GitRepo,
    lfs_available,
)
from second_brain_builder.brains.layout import (
    ASSETS_DIR_NAME,
    INDEX_PATH,
    LOG_PATH,
    MARKDOWN_SOURCE_SUFFIX,
    SETTINGS_PATH,
    SOURCES_DIR,
    WIKI_DIR,
    WIKI_DIRS,
    BrainLayout,
)
from second_brain_builder.brains.models import (
    SCHEMA_VERSION,
    BrainSettings,
    GitAuth,
    GitIdentity,
    Graph,
    GraphEdge,
    GraphNode,
    PageSummary,
)
from second_brain_builder.brains.pages import build_graph, list_pages, read_pages
from second_brain_builder.brains.paths import BrainPathError, resolve_in_brain
from second_brain_builder.brains.scaffold import (
    GITATTRIBUTES,
    INDEX_DESCRIPTION,
    BrainSettingsError,
    read_settings,
    scaffold_brain,
    write_index,
)
from second_brain_builder.brains.slugs import (
    FALLBACK_SLUG,
    RESERVED_SLUGS,
    is_slug,
    slugify,
    unique_slug,
)

__all__ = [
    "ASSETS_DIR_NAME",
    "DEFAULT_BRANCH",
    "FALLBACK_SLUG",
    "FOLDER_MARKER",
    "GITATTRIBUTES",
    "INDEX_DESCRIPTION",
    "INDEX_PATH",
    "LOG_PATH",
    "MARKDOWN_SOURCE_SUFFIX",
    "RESERVED_SLUGS",
    "SCHEMA_VERSION",
    "SETTINGS_PATH",
    "SOURCES_DIR",
    "WIKI_DIR",
    "WIKI_DIRS",
    "BrainLayout",
    "BrainPathError",
    "BrainSettings",
    "BrainSettingsError",
    "GitAuth",
    "GitConflictError",
    "GitError",
    "GitIdentity",
    "GitRepo",
    "Graph",
    "GraphEdge",
    "GraphNode",
    "PageSummary",
    "build_graph",
    "folder_filter",
    "is_slug",
    "kept_folders",
    "lfs_available",
    "list_pages",
    "read_pages",
    "read_settings",
    "resolve_in_brain",
    "scaffold_brain",
    "slugify",
    "unique_slug",
    "write_index",
]
