"""REST client for the GitHub API, and the GitHub App install URL."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, SecretStr

from second_brain_builder.github._http import (
    API_URL,
    API_VERSION,
    OAUTH_URL,
    TIMEOUT,
    USER_AGENT,
    error_for_response,
    parse_model,
    response_json,
    send,
)
from second_brain_builder.github.errors import GitHubError
from second_brain_builder.github.models import GitHubUser, RepoInfo, TokenKind

PER_PAGE = 100
"""Items per page for list endpoints. 100 is the maximum that GitHub allows."""
APP_USER_PREFIX = "ghu_"
"""GitHub's documented prefix for GitHub App user access tokens."""

_TOKEN_PATTERN = re.compile(r"[\x21-\x7e]+")
"""Visible ASCII only. GitHub tokens never contain whitespace or other characters."""
_REPO_PART_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
"""The characters that GitHub allows in an owner login and in a repository name."""


class _UserPayload(BaseModel):
    id: int
    login: str


class _InstallationPayload(BaseModel):
    id: int


def install_url(app_slug: str) -> str:
    """Return the page where the user installs the GitHub App and picks repositories."""
    return f"{OAUTH_URL}/apps/{quote(app_slug, safe='')}/installations/new"


class GitHubClient:
    """Typed calls to the GitHub REST API with one token.

    ``kind`` selects how ``list_repos`` finds repositories. If it is ``None``, the
    kind comes from the token prefix: ``ghu_`` (a GitHub App user token) gives
    ``app``, and every other prefix gives ``pat``.

    Every method raises ``GitHubError`` or one of its subclasses on failure:
    ``GitHubAuthError`` (401), ``GitHubPermissionError`` (403),
    ``GitHubNotFoundError`` (404), ``GitHubValidationError`` (422),
    ``GitHubRateLimitError``, and ``GitHubConnectionError``. Error messages and
    ``repr()`` never contain the token.

    Raises:
        ValueError: ``token`` is empty, or has a character that no GitHub token
            has: whitespace (such as a line break pasted with the token), a
            control character, or a non-ASCII character. The message does not
            show the token. Strip pasted input before you pass it.
    """

    def __init__(
        self,
        token: SecretStr,
        transport: httpx.BaseTransport | None = None,
        *,
        kind: TokenKind | None = None,
    ) -> None:
        _check_token(token)
        self._token = token
        self._transport = transport
        self._kind: TokenKind = kind if kind is not None else _infer_kind(token)

    @property
    def kind(self) -> TokenKind:
        """The token kind that ``list_repos`` uses."""
        return self._kind

    def __repr__(self) -> str:
        return f"GitHubClient(kind={self._kind!r})"

    def user(self) -> GitHubUser:
        """Return the user who owns the token."""
        with self._session() as session:
            body = session.get_json("/user")
        payload = parse_model(_UserPayload, body, "GitHub request GET /user failed")
        return GitHubUser(
            id=payload.id,
            login=payload.login,
            noreply_email=f"{payload.id}+{payload.login}@users.noreply.github.com",
        )

    def list_repos(self) -> list[RepoInfo]:
        """Return the repositories that this token can use, sorted by ``full_name``.

        An ``app`` token sees the repositories of each installation of the app that
        the user can access. A ``pat`` sees every repository that the token can access.
        """
        with self._session() as session:
            if self._kind == "app":
                items = list(self._installation_repo_items(session))
            else:
                items = list(session.paginate("/user/repos", key=None))
        repos = [parse_model(RepoInfo, item, "GitHub repository list failed") for item in items]
        unique = {repo.full_name.lower(): repo for repo in repos}
        return sorted(unique.values(), key=lambda repo: repo.full_name.lower())

    def get_repo(self, full_name: str) -> RepoInfo:
        """Return one repository by ``owner/name``.

        Raises:
            ValueError: ``full_name`` is not in the form ``owner/name``, where each
                part has only letters, digits, ``-``, ``_``, and ``.``, and is not
                ``.`` or ``..``. No request is sent.
        """
        owner, _, name = full_name.partition("/")
        if not (_is_repo_part(owner) and _is_repo_part(name)):
            raise ValueError(
                "Expected a repository name in the form 'owner/name', where each part has "
                f"only letters, digits, '-', '_', and '.', and is not '.' or '..': {full_name!r}"
            )
        # The parts have no '/', '%', '?', or '#', and they are not dot segments,
        # which httpx would resolve into another API path.
        path = f"/repos/{owner}/{name}"
        with self._session() as session:
            body = session.get_json(path)
        return parse_model(RepoInfo, body, f"GitHub request GET {path} failed")

    def create_private_repo(self, name: str, description: str) -> RepoInfo:
        """Create an empty private repository for the user.

        Raises:
            GitHubValidationError: GitHub rejected the name, for example because
                the user already has a repository with that name.
        """
        payload = {"name": name, "description": description, "private": True}
        with self._session() as session:
            body = session.send_json("POST", "/user/repos", payload)
        return parse_model(RepoInfo, body, "GitHub request POST /user/repos failed")

    @contextmanager
    def _session(self) -> Iterator["_Session"]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        with httpx.Client(
            base_url=API_URL,
            transport=self._transport,
            timeout=TIMEOUT,
            headers=headers,
            # httpx removes the Authorization header on a redirect to another origin.
            follow_redirects=True,
        ) as client:
            yield _Session(client, self._token.get_secret_value())

    @staticmethod
    def _installation_repo_items(session: "_Session") -> Iterator[Any]:
        for item in session.paginate("/user/installations", key="installations"):
            installation = parse_model(
                _InstallationPayload, item, "GitHub installation list failed"
            )
            yield from session.paginate(
                f"/user/installations/{installation.id}/repositories", key="repositories"
            )


class _Session:
    """The requests of one ``GitHubClient`` method, on one httpx client."""

    def __init__(self, client: httpx.Client, secret: str) -> None:
        self._client = client
        self._secret = secret
        self._origin = httpx.URL(API_URL)

    def get_json(self, path: str) -> Any:
        response, _ = self._request("GET", path)
        return response_json(response)

    def send_json(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        response, _ = self._request(method, path, payload=payload)
        return response_json(response)

    def paginate(self, path: str, key: str | None) -> Iterator[Any]:
        """Yield every item of a list endpoint, following ``Link: rel="next"``.

        ``key`` names the list inside an object body, such as ``repositories``.
        ``None`` means that the body itself is the list.
        """
        url = path
        params: dict[str, int] | None = {"per_page": PER_PAGE}
        while True:
            response, summary = self._request("GET", url, params=params)
            body = response_json(response)
            items = _list_in_body(body, key)
            if not isinstance(items, list):
                expected = f"an object with a '{key}' list" if key else "a list"
                raise GitHubError(
                    f"{summary}: unexpected response from GitHub (expected {expected})"
                )
            yield from items
            next_url = response.links.get("next", {}).get("url")
            if next_url is None:
                return
            self._check_same_origin(next_url, summary)
            url, params = next_url, None

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, int] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response, str]:
        request = self._client.build_request(method, url, params=params, json=payload)
        summary = f"GitHub request {method} {request.url.path} failed"
        response = send(self._client, request, summary)
        if not response.is_success:
            raise error_for_response(response, summary, secrets=(self._secret,))
        return response, summary

    def _check_same_origin(self, url: str, summary: str) -> None:
        # The client sends the token with every request, so it follows a link only
        # if the link stays on the API origin.
        try:
            target = httpx.URL(url)
        except httpx.InvalidURL:
            raise GitHubError(
                f"{summary}: GitHub sent a pagination link that is not a valid URL"
            ) from None
        if (target.scheme, target.host, target.port) != (
            self._origin.scheme,
            self._origin.host,
            self._origin.port,
        ):
            raise GitHubError(
                f"{summary}: GitHub sent a pagination link to another origin "
                f"({target.scheme}://{target.host}); the client did not follow it"
            )


def _list_in_body(body: Any, key: str | None) -> Any:
    """Return the body itself (``key`` is ``None``) or ``body[key]`` of an object body."""
    if key is None:
        return body
    return body.get(key) if isinstance(body, dict) else None


def _check_token(token: SecretStr) -> None:
    """Raise ``ValueError`` for a token that cannot be a GitHub token.

    Such a token would fail in httpx or h11, and h11 quotes the header value in
    its error. The message here never contains the token.
    """
    value = token.get_secret_value()
    if not value:
        raise ValueError("The GitHub token is empty.")
    if _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "The GitHub token has whitespace, a control character, or a non-ASCII "
            "character. GitHub tokens have only visible ASCII characters."
        )


def _is_repo_part(part: str) -> bool:
    return _REPO_PART_PATTERN.fullmatch(part) is not None and part not in {".", ".."}


def _infer_kind(token: SecretStr) -> TokenKind:
    return "app" if token.get_secret_value().startswith(APP_USER_PREFIX) else "pat"
