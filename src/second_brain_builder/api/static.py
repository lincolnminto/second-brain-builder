"""The built web UI at ``/``, with a fallback to ``index.html`` for client-side routes.

The UI is a single-page app: ``/brains/<id>/pages/...`` is a client route, so every
GET outside ``/api`` and ``/mcp`` that is not a file of the build gets ``index.html``.
Only ``/assets/`` (Vite's hashed build output) answers 404 for a missing file, so a
stale script URL fails clearly instead of loading HTML.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from starlette.responses import FileResponse, Response

from second_brain_builder.api.errors import error_response
from second_brain_builder.api.schemas import ErrorResponse
from second_brain_builder.config import Settings

_log = logging.getLogger(__name__)

SOURCE_ROOT = Path(__file__).resolve().parents[3]
"""The repository root when the package runs from a source tree (also in the images)."""
DEFAULT_WEB_DIR = SOURCE_ROOT / "web" / "dist"
SKILL_FILE = SOURCE_ROOT / "skills" / "second-brain" / "SKILL.md"
SKILL_PATH = "/skills/second-brain/SKILL.md"
"""Where ``services.get_agent_setup`` tells agents to download the skill."""

INDEX_FILE = "index.html"
ASSETS_DIR = "assets"

UI_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "media-src 'self' blob:",
        "connect-src 'self'",
        "frame-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
)
"""The UI loads nothing from other origins, so untrusted Markdown cannot load remote images."""

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"


def find_web_dir(settings: Settings) -> Path | None:
    """``SBB_WEB_DIR``, else ``web/dist`` of the source tree; None without ``index.html``."""
    directory = settings.web_dir if settings.web_dir is not None else DEFAULT_WEB_DIR
    if (directory / INDEX_FILE).is_file():
        return directory.resolve()
    if settings.web_dir is not None:
        _log.warning("SBB_WEB_DIR %s has no %s; the web UI is off.", directory, INDEX_FILE)
    return None


def ui_response(web_dir: Path | None, path: str) -> Response:
    """The file of the build at ``path`` (from the URL path), or ``index.html``."""
    if web_dir is None:
        return error_response(
            404,
            ErrorResponse(
                code="not_found",
                message="The web UI is not built. Build web/ or set SBB_WEB_DIR.",
            ),
        )
    relative = path.lstrip("/")
    file = _build_file(web_dir, relative)
    if file is not None:
        under_assets = PurePosixPath(relative).parts[:1] == (ASSETS_DIR,)
        return _file(file, IMMUTABLE if under_assets else NO_CACHE)
    if PurePosixPath(relative).parts[:1] == (ASSETS_DIR,):
        return error_response(404, ErrorResponse(code="not_found", message="No such file."))
    return _file(web_dir / INDEX_FILE, NO_CACHE)


def skill_response() -> Response:
    """The agent skill ``skills/second-brain/SKILL.md``, as Markdown."""
    if not SKILL_FILE.is_file():
        return error_response(
            404, ErrorResponse(code="not_found", message="The agent skill is not installed.")
        )
    return FileResponse(
        SKILL_FILE,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": NO_CACHE},
    )


def _build_file(web_dir: Path, relative: str) -> Path | None:
    """The regular file ``relative`` inside ``web_dir``, or None. Never leaves the directory."""
    if not relative or "\\" in relative or "\x00" in relative:
        return None
    parts = PurePosixPath(relative).parts
    if any(part in {".", ".."} or part.startswith(".") for part in parts):
        return None
    candidate = web_dir.joinpath(*parts).resolve()
    if not candidate.is_relative_to(web_dir) or not candidate.is_file():
        return None
    return candidate


def _file(path: Path, cache_control: str) -> FileResponse:
    headers = {"Cache-Control": cache_control}
    if path.suffix == ".html":
        headers["Content-Security-Policy"] = UI_CONTENT_SECURITY_POLICY
    return FileResponse(path, headers=headers)
