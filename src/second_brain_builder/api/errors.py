"""Error responses: every error of ``/api`` has the body ``ErrorResponse``.

``ServiceError`` subclasses map to status codes here. Request validation errors,
unknown routes, and unexpected exceptions get the same body shape, so a client
reads ``code`` and ``message`` from every error.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from second_brain_builder.api.schemas import ErrorResponse, ValidationIssue
from second_brain_builder.services import (
    AuthError,
    ConfirmationRequiredError,
    ConflictError,
    DuplicateSourceError,
    InvalidInputError,
    NotConfiguredError,
    NotConnectedError,
    NotFoundError,
    PublicRepoError,
    QuotaExceededError,
    ServiceError,
    UploadTooLargeError,
    UpstreamError,
)

_log = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    413: "content_too_large",
    415: "unsupported_media_type",
    422: "invalid_request",
}


class ApiError(Exception):
    """An error that the HTTP layer finds itself, such as a missing header or a bad body."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


def error_response(
    status_code: int, body: ErrorResponse, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """A JSON error response. Fields that are ``None`` are left out of the body."""
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True),
        status_code=status_code,
        headers={**NO_STORE, **(headers or {})},
    )


def service_error_status(exc: ServiceError) -> int:
    """The HTTP status code of a use-case error."""
    if isinstance(exc, UploadTooLargeError):
        return 413
    if isinstance(exc, InvalidInputError):
        return 422
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(
        exc, (ConflictError, ConfirmationRequiredError, NotConnectedError, NotConfiguredError)
    ):
        return 409
    if isinstance(exc, AuthError):
        # "app": the caller's API token. Otherwise GitHub or a Provider rejected a stored
        # credential; the caller is signed in, so this is a bad gateway, not a 401.
        return 401 if exc.service == "app" else 502
    if isinstance(exc, UpstreamError):
        return 503 if exc.retry_at is not None else 502
    return 400


def service_error_body(exc: ServiceError) -> ErrorResponse:
    """The ``ErrorResponse`` of a use-case error, with the details of its class."""
    body = ErrorResponse(code=exc.code, message=exc.message)
    if isinstance(exc, DuplicateSourceError):
        body.existing_source = exc.existing
        body.pending_job = exc.pending_job
    elif isinstance(exc, UploadTooLargeError):
        body.max_bytes = exc.max_bytes
    elif isinstance(exc, QuotaExceededError):
        body.used_bytes = exc.used_bytes
        body.upload_bytes = exc.upload_bytes
        body.quota_bytes = exc.quota_bytes
    elif isinstance(exc, PublicRepoError):
        body.repo_full_name = exc.repo_full_name
    elif isinstance(exc, AuthError):
        body.service = exc.service
    elif isinstance(exc, UpstreamError):
        body.service = exc.service
        body.retry_at = exc.retry_at
    return body


def service_error_response(exc: ServiceError) -> JSONResponse:
    headers: dict[str, str] = {}
    if isinstance(exc, UpstreamError) and exc.retry_at is not None:
        headers["Retry-After"] = str(_seconds_until(exc.retry_at))
    if isinstance(exc, AuthError) and exc.service == "app":
        headers["WWW-Authenticate"] = "Bearer"
    return error_response(service_error_status(exc), service_error_body(exc), headers)


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers that turn every error into an ``ErrorResponse``."""
    app.add_exception_handler(ServiceError, _handle_service_error)
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)


async def _handle_service_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ServiceError):  # pragma: no cover - registered for ServiceError
        raise exc
    return service_error_response(exc)


async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):  # pragma: no cover - registered for ApiError
        raise exc
    return error_response(
        exc.status_code, ErrorResponse(code=exc.code, message=exc.message), exc.headers
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    issues = [
        ValidationIssue(
            location=[part if isinstance(part, int) else str(part) for part in error["loc"]],
            message=str(error["msg"]),
            type=str(error["type"]),
        )
        for error in exc.errors()
    ]
    body = ErrorResponse(code="invalid_request", message="The request is not valid.", issues=issues)
    return error_response(422, body)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        raise exc
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else code
    return error_response(exc.status_code, ErrorResponse(code=code, message=message), exc.headers)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    _log.error("Unexpected error in %s %s", request.method, request.url.path, exc_info=exc)
    body = ErrorResponse(code="internal", message="An internal error occurred.")
    return error_response(500, body)


def _seconds_until(moment: datetime) -> int:
    now = datetime.now(timezone.utc)
    return max(1, math.ceil((moment - now).total_seconds()))
