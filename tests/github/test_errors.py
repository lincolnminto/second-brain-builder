"""GitHub errors keep every attribute across pickle and copy.

The worker can run jobs in other processes. An error that a child process
raises reaches the parent through pickle, and the parent needs its fields, for
example ``error == "bad_refresh_token"`` to ask the user to connect GitHub again.
"""

import copy
import pickle
from collections.abc import Callable
from datetime import datetime, timezone

import pytest

from second_brain_builder.github import (
    DeviceFlowDisabledError,
    GitHubConnectionError,
    GitHubError,
    GitHubOAuthError,
    GitHubRateLimitError,
    GitHubValidationError,
)

ERRORS = [
    GitHubError("GitHub request GET /user failed", status_code=500, github_message="Server Error"),
    GitHubConnectionError("GitHub request GET /user failed: ConnectTimeout"),
    GitHubValidationError(
        "GitHub request POST /user/repos failed",
        status_code=422,
        github_message="name already exists on this account",
    ),
    GitHubRateLimitError(
        "GitHub request GET /user failed",
        status_code=429,
        github_message="You have exceeded a secondary rate limit.",
        reset_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    ),
    GitHubOAuthError(
        "GitHub token refresh failed",
        error="bad_refresh_token",
        description="The refresh token passed is incorrect or expired.",
        status_code=200,
    ),
    DeviceFlowDisabledError(
        "GitHub device flow start failed", error="device_flow_disabled", status_code=400
    ),
]


def _through_pickle(error: GitHubError) -> GitHubError:
    restored = pickle.loads(pickle.dumps(error))  # noqa: S301 - the test pickled it itself
    assert isinstance(restored, GitHubError)
    return restored


@pytest.mark.parametrize("error", ERRORS, ids=lambda error: type(error).__name__)
@pytest.mark.parametrize("duplicate", [_through_pickle, copy.copy], ids=["pickle", "copy"])
def test_error_keeps_its_type_message_and_fields(
    error: GitHubError, duplicate: Callable[[GitHubError], GitHubError]
) -> None:
    restored = duplicate(error)

    assert type(restored) is type(error)
    assert str(restored) == str(error)
    assert vars(restored) == vars(error)
