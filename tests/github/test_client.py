"""Contract tests for GitHubClient against a scripted api.github.com."""

import copy
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from second_brain_builder.github import (
    GitHubAuthError,
    GitHubClient,
    GitHubConnectionError,
    GitHubError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubValidationError,
    RepoInfo,
    TokenKind,
    install_url,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"
API = "https://api.github.com"
PAT = "ghp_syntheticPersonalToken000000000001"
APP_TOKEN = "ghu_syntheticAccessToken000000000000001"


class FakeGitHub:
    """A scripted api.github.com: one response per (method, URL), and a log of requests.

    A request without a scripted response fails the test.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], tuple[int, Any, dict[str, str]]] = {}
        self.requests: list[httpx.Request] = []

    def on(
        self,
        method: str,
        url: str,
        body: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._routes[(method, url)] = (status, body, headers or {})

    def client(self, token: str = PAT, kind: TokenKind | None = None) -> GitHubClient:
        return GitHubClient(SecretStr(token), httpx.MockTransport(self._handle), kind=kind)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url))
        if key not in self._routes:
            raise AssertionError(f"Unscripted request: {request.method} {request.url}")
        status, body, headers = self._routes[key]
        if isinstance(body, str):
            return httpx.Response(status, text=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@cache
def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _repo(full_name: str, *, private: bool = True) -> dict[str, Any]:
    """A full GitHub repository payload, based on the fixture."""
    payload = copy.deepcopy(_fixture("repository.json"))
    owner, name = full_name.split("/")
    payload.update(
        name=name,
        full_name=full_name,
        private=private,
        visibility="private" if private else "public",
        clone_url=f"https://github.com/{full_name}.git",
        html_url=f"https://github.com/{full_name}",
        url=f"{API}/repos/{full_name}",
    )
    payload["owner"]["login"] = owner
    return payload


def _next_link(next_url: str, last_url: str) -> dict[str, str]:
    return {"Link": f'<{next_url}>; rel="next", <{last_url}>; rel="last"'}


def _names(repos: list[RepoInfo]) -> list[str]:
    return [repo.full_name for repo in repos]


# --- user -----------------------------------------------------------------------


def test_user_sends_api_headers_and_builds_noreply_email(github: FakeGitHub) -> None:
    github.on("GET", f"{API}/user", _fixture("user.json"))

    user = github.client().user()

    assert (user.id, user.login) == (1000001, "test-user")
    assert user.noreply_email == "1000001+test-user@users.noreply.github.com"
    request = github.requests[0]
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["Authorization"] == f"Bearer {PAT}"


# --- list_repos -----------------------------------------------------------------


def test_pat_lists_repos_across_link_pages(github: FakeGitHub) -> None:
    first = f"{API}/user/repos?per_page=100"
    second = f"{API}/user/repos?per_page=100&page=2"
    third = f"{API}/user/repos?per_page=100&page=3"
    github.on(
        "GET",
        first,
        [_repo("test-user/notes"), _repo("example-org/handbook")],
        headers=_next_link(second, third),
    )
    github.on("GET", second, [_repo("test-user/archive")], headers=_next_link(third, third))
    github.on(
        "GET",
        third,
        [_repo("test-user/blog", private=False)],
        headers={"Link": f'<{first}>; rel="first", <{second}>; rel="prev"'},
    )

    repos = github.client(PAT).list_repos()

    assert [str(request.url) for request in github.requests] == [first, second, third]
    assert _names(repos) == [
        "example-org/handbook",
        "test-user/archive",
        "test-user/blog",
        "test-user/notes",
    ]
    blog = repos[2]
    assert blog == RepoInfo(
        full_name="test-user/blog",
        private=False,
        clone_url="https://github.com/test-user/blog.git",
        default_branch="main",
    )


def test_app_token_lists_repos_of_every_installation(github: FakeGitHub) -> None:
    installations = f"{API}/user/installations?per_page=100"
    installations_2 = f"{API}/user/installations?per_page=100&page=2"
    github.on(
        "GET",
        installations,
        {"total_count": 2, "installations": [{"id": 11, "app_slug": "synthetic-app"}]},
        headers=_next_link(installations_2, installations_2),
    )
    github.on(
        "GET",
        installations_2,
        {"total_count": 2, "installations": [{"id": 22, "app_slug": "synthetic-app"}]},
    )
    repos_11 = f"{API}/user/installations/11/repositories?per_page=100"
    repos_11_page_2 = f"{API}/user/installations/11/repositories?per_page=100&page=2"
    github.on(
        "GET",
        repos_11,
        {"total_count": 2, "repositories": [_repo("test-user/notes")]},
        headers=_next_link(repos_11_page_2, repos_11_page_2),
    )
    github.on(
        "GET",
        repos_11_page_2,
        {"total_count": 2, "repositories": [_repo("test-user/archive")]},
    )
    github.on(
        "GET",
        f"{API}/user/installations/22/repositories?per_page=100",
        {"total_count": 1, "repositories": [_repo("example-org/handbook")]},
    )

    repos = github.client(APP_TOKEN, kind="app").list_repos()

    assert _names(repos) == ["example-org/handbook", "test-user/archive", "test-user/notes"]
    assert "/user/repos" not in github.paths()


@pytest.mark.parametrize(
    ("token", "kind", "expected_path"),
    [
        (APP_TOKEN, None, "/user/installations"),  # ghu_ prefix: GitHub App user token
        (PAT, None, "/user/repos"),  # classic PAT
        ("github_pat_synthetic0000000000000001", None, "/user/repos"),  # fine-grained PAT
        (PAT, "app", "/user/installations"),  # an explicit kind wins over the prefix
    ],
)
def test_token_kind_selects_the_repo_listing(
    github: FakeGitHub, token: str, kind: TokenKind | None, expected_path: str
) -> None:
    github.on("GET", f"{API}/user/installations?per_page=100", {"installations": []})
    github.on("GET", f"{API}/user/repos?per_page=100", [])

    assert github.client(token, kind).list_repos() == []

    assert github.paths() == [expected_path]


def test_pagination_does_not_follow_a_link_to_another_origin(github: FakeGitHub) -> None:
    other = "https://api.example.com/user/repos?per_page=100&page=2"
    github.on(
        "GET",
        f"{API}/user/repos?per_page=100",
        [_repo("test-user/notes")],
        headers=_next_link(other, other),
    )

    with pytest.raises(GitHubError, match="another origin"):
        github.client(PAT).list_repos()

    # The token was sent to api.github.com only.
    assert [request.url.host for request in github.requests] == ["api.github.com"]


def test_invalid_pagination_link_raises_github_error(github: FakeGitHub) -> None:
    github.on(
        "GET",
        f"{API}/user/repos?per_page=100",
        [_repo("test-user/notes")],
        headers={"Link": '<https://api.github.com:not-a-port/user/repos?page=2>; rel="next"'},
    )

    with pytest.raises(GitHubError, match="not a valid URL"):
        github.client(PAT).list_repos()


def test_unexpected_list_body_raises_github_error(github: FakeGitHub) -> None:
    github.on("GET", f"{API}/user/installations?per_page=100", [{"id": 11}])

    with pytest.raises(GitHubError, match="'installations' list"):
        github.client(APP_TOKEN).list_repos()


# --- get_repo and create_private_repo ------------------------------------------


@pytest.mark.parametrize(
    "full_name", ["test-user/notes", "test-user/.github", "example-org/notes.v2_draft-1"]
)
def test_get_repo_returns_repo_info(github: FakeGitHub, full_name: str) -> None:
    github.on("GET", f"{API}/repos/{full_name}", _repo(full_name))

    repo = github.client().get_repo(full_name)

    assert repo == RepoInfo(
        full_name=full_name,
        private=True,
        clone_url=f"https://github.com/{full_name}.git",
        default_branch="main",
    )


@pytest.mark.parametrize(
    "full_name",
    [
        "notes",
        "/notes",
        "test-user/",
        "a/b/c",
        # Dot segments: httpx would resolve /repos/../user into GET /user.
        "../user",
        "../..",
        "test-user/..",
        "./notes",
        "test-user/.",
        # Characters that GitHub does not allow in names.
        "test-user/my notes",
        "test-user/notes?per_page=1",
        "test-user/%2E%2E",
    ],
)
def test_get_repo_rejects_names_that_are_not_owner_slash_name(
    github: FakeGitHub, full_name: str
) -> None:
    with pytest.raises(ValueError, match="owner/name"):
        github.client().get_repo(full_name)

    assert github.requests == []


def test_create_private_repo_posts_a_private_repo(github: FakeGitHub) -> None:
    github.on("POST", f"{API}/user/repos", _repo("test-user/brain"), status=201)

    repo = github.client().create_private_repo("brain", "Second brain")

    assert repo.full_name == "test-user/brain"
    assert repo.private is True
    assert json.loads(github.requests[0].content) == {
        "name": "brain",
        "description": "Second brain",
        "private": True,
    }


def test_create_private_repo_with_a_taken_name_raises_validation_error(
    github: FakeGitHub,
) -> None:
    github.on(
        "POST",
        f"{API}/user/repos",
        {
            "message": "Repository creation failed.",
            "errors": [
                {
                    "resource": "Repository",
                    "code": "custom",
                    "field": "name",
                    "message": "name already exists on this account",
                }
            ],
            "documentation_url": "https://docs.github.com/rest/repos/repos",
            "status": "422",
        },
        status=422,
    )

    with pytest.raises(GitHubValidationError) as info:
        github.client().create_private_repo("notes", "Second brain")

    assert info.value.status_code == 422
    assert info.value.github_message == (
        "Repository creation failed.; name already exists on this account"
    )


# --- error mapping --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "message", "error_class"),
    [
        (401, "Bad credentials", GitHubAuthError),
        (403, "Resource not accessible by integration", GitHubPermissionError),
        (404, "Not Found", GitHubNotFoundError),
        (500, "Server Error", GitHubError),
    ],
)
def test_http_errors_map_to_typed_errors(
    github: FakeGitHub, status: int, message: str, error_class: type[GitHubError]
) -> None:
    github.on(
        "GET",
        f"{API}/repos/test-user/notes",
        {"message": message, "documentation_url": "https://docs.github.com/rest"},
        status=status,
    )

    with pytest.raises(GitHubError) as info:
        github.client().get_repo("test-user/notes")

    assert type(info.value) is error_class
    assert info.value.status_code == status
    assert info.value.github_message == message
    assert str(info.value) == (
        f"GitHub request GET /repos/test-user/notes failed: HTTP {status}: {message}"
    )


def test_error_without_json_body_keeps_status_and_reason(github: FakeGitHub) -> None:
    github.on("GET", f"{API}/user", "<html>Unicorn!</html>", status=502)

    with pytest.raises(GitHubError) as info:
        github.client().user()

    assert info.value.status_code == 502
    assert info.value.github_message == "Bad Gateway"


def test_primary_rate_limit_gives_reset_time(github: FakeGitHub) -> None:
    github.on(
        "GET",
        f"{API}/user",
        {"message": "API rate limit exceeded for user ID 1000001."},
        status=403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"},
    )

    with pytest.raises(GitHubRateLimitError) as info:
        github.client().user()

    assert info.value.status_code == 403
    assert info.value.reset_at == datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_secondary_rate_limit_gives_retry_time(github: FakeGitHub) -> None:
    github.on(
        "GET",
        f"{API}/user",
        {"message": "You have exceeded a secondary rate limit."},
        status=429,
        headers={"retry-after": "60"},
    )

    before = datetime.now(timezone.utc)
    with pytest.raises(GitHubRateLimitError) as info:
        github.client().user()

    assert info.value.reset_at is not None
    assert info.value.reset_at >= before + timedelta(seconds=60)


def test_errors_reprs_and_logs_never_contain_the_token(
    github: FakeGitHub,
    caplog: pytest.LogCaptureFixture,
    exposed_text: Callable[[BaseException], str],
) -> None:
    caplog.set_level(logging.DEBUG)
    github.on("GET", f"{API}/user", _fixture("user.json"))
    # A worst case: GitHub echoes the token in its error message.
    github.on(
        "GET",
        f"{API}/repos/test-user/notes",
        {"message": f"Bad credentials: {PAT}"},
        status=401,
    )
    client = github.client(PAT)
    client.user()

    with pytest.raises(GitHubAuthError) as info:
        client.get_repo("test-user/notes")

    exc = info.value
    assert "Bad credentials" in str(exc)
    assert PAT not in "\n".join([exposed_text(exc), repr(client), caplog.text])


@pytest.mark.parametrize(
    "token",
    [
        f"{PAT}\n",  # pasted with the line break
        f" {PAT}",
        f"{PAT}\t",
        f"{PAT}\u200b",  # a zero-width space copied from a web page
        f"{PAT}\x00",
        "",
    ],
)
def test_token_that_cannot_be_a_github_token_is_rejected_without_showing_it(
    token: str, exposed_text: Callable[[BaseException], str]
) -> None:
    with pytest.raises(ValueError, match="GitHub token") as info:
        GitHubClient(SecretStr(token))

    assert PAT not in exposed_text(info.value)


def test_httpx_error_that_quotes_the_token_leaves_no_trace_of_it(
    exposed_text: Callable[[BaseException], str],
) -> None:
    def reject_header(request: httpx.Request) -> httpx.Response:
        # h11 raises this for a header value that it rejects, and quotes the value.
        value = request.headers["Authorization"].encode()
        raise httpx.LocalProtocolError(f"Illegal header value {value!r}", request=request)

    client = GitHubClient(SecretStr(PAT), httpx.MockTransport(reject_header))

    with pytest.raises(GitHubConnectionError) as info:
        client.user()

    assert info.value.status_code is None
    assert "LocalProtocolError" in str(info.value)
    assert PAT not in exposed_text(info.value)


def _redirect_loop(github: FakeGitHub) -> None:
    github.on("GET", f"{API}/user", "", status=301, headers={"Location": f"{API}/user"})


def _corrupt_gzip_body(github: FakeGitHub) -> None:
    github.on("GET", f"{API}/user", "not gzip data", headers={"Content-Encoding": "gzip"})


@pytest.mark.parametrize(
    ("script", "failure"),
    [(_redirect_loop, "TooManyRedirects"), (_corrupt_gzip_body, "DecodingError")],
)
def test_response_that_httpx_cannot_use_raises_github_error(
    github: FakeGitHub, script: Callable[[FakeGitHub], None], failure: str
) -> None:
    script(github)

    with pytest.raises(GitHubError) as info:
        github.client().user()

    # A response arrived, so this is not a connection error, and it has no status.
    assert type(info.value) is GitHubError
    assert info.value.status_code is None
    assert str(info.value) == f"GitHub request GET /user failed: {failure}"


def test_install_url_points_to_the_app_installation_page() -> None:
    assert install_url("synthetic-app") == (
        "https://github.com/apps/synthetic-app/installations/new"
    )
