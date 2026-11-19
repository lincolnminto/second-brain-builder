"""GitHub: device flow, personal access tokens, token refresh, user, installations, and repos.

Every function that calls GitHub takes an optional ``transport``
(``httpx.BaseTransport``), so tests can pass ``httpx.MockTransport``. No token
ever appears in an error message, a ``repr()``, or a log line.
"""

from second_brain_builder.github.client import GitHubClient, install_url
from second_brain_builder.github.errors import (
    DeviceFlowDisabledError,
    GitHubAuthError,
    GitHubConnectionError,
    GitHubError,
    GitHubNotFoundError,
    GitHubOAuthError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from second_brain_builder.github.models import (
    DeviceCode,
    DeviceDenied,
    DeviceExpired,
    DevicePending,
    DevicePollResult,
    DeviceSlowDown,
    GitHubUser,
    RepoInfo,
    TokenKind,
    TokenSet,
)
from second_brain_builder.github.oauth import poll_device_flow, refresh_token, start_device_flow

__all__ = [
    "DeviceCode",
    "DeviceDenied",
    "DeviceExpired",
    "DeviceFlowDisabledError",
    "DevicePending",
    "DevicePollResult",
    "DeviceSlowDown",
    "GitHubAuthError",
    "GitHubClient",
    "GitHubConnectionError",
    "GitHubError",
    "GitHubNotFoundError",
    "GitHubOAuthError",
    "GitHubPermissionError",
    "GitHubRateLimitError",
    "GitHubUser",
    "GitHubValidationError",
    "RepoInfo",
    "TokenKind",
    "TokenSet",
    "install_url",
    "poll_device_flow",
    "refresh_token",
    "start_device_flow",
]
