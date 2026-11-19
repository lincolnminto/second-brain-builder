"""The web UI session: a signed cookie, used only when ``SBB_PASSWORD`` is set.

The cookie holds a random session id, signed with ``itsdangerous`` and a timestamp.
The signing key is derived from the app's secret key (``SBB_SECRET_KEY`` or
``<data_dir>/secret.key``) and the password, so a session stays valid across
restarts, and a new password or secret key ends every session. Logout removes the
cookie from the browser; a copied cookie stays valid until it expires.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.responses import Response

from second_brain_builder.config import Settings

SESSION_COOKIE = "sbb_session"
SESSION_MAX_AGE = timedelta(days=30)
MAX_COOKIE_CHARS = 1024
_SALT = "sbb-session"
_KEY_CONTEXT = b"sbb-session-key-v1\x00"


class SessionSigner:
    """Issues and checks session cookies."""

    def __init__(
        self, secret: bytes, *, secure: bool, max_age: timedelta = SESSION_MAX_AGE
    ) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt=_SALT)
        self._secure = secure
        self._max_age = max_age

    def __repr__(self) -> str:
        return f"SessionSigner(secure={self._secure})"

    @classmethod
    def from_settings(cls, settings: Settings) -> SessionSigner:
        """A signer for the password and secret key of ``settings``.

        The cookie is ``Secure`` when ``SBB_PUBLIC_URL`` is an ``https://`` URL.
        Build the ``AppContext`` first: it creates the secret key file.
        """
        password = "" if settings.password is None else settings.password.get_secret_value()
        secret = hmac.new(
            _secret_key_material(settings),
            _KEY_CONTEXT + password.encode("utf-8"),
            hashlib.sha256,
        )
        return cls(secret.digest(), secure=settings.public_url.lower().startswith("https://"))

    def issue(self) -> str:
        """A new signed session value."""
        return self._serializer.dumps({"sid": secrets.token_urlsafe(16)})

    def is_valid(self, value: str | None) -> bool:
        """True for a value from ``issue`` that is not older than the maximum age."""
        if not value or len(value) > MAX_COOKIE_CHARS:
            return False
        try:
            self._serializer.loads(value, max_age=int(self._max_age.total_seconds()))
        except BadSignature:  # SignatureExpired is a subclass
            return False
        return True

    def set_cookie(self, response: Response) -> None:
        """Start a session: ``HttpOnly``, ``SameSite=Strict``, ``Secure`` for https."""
        response.set_cookie(
            SESSION_COOKIE,
            self.issue(),
            max_age=int(self._max_age.total_seconds()),
            path="/",
            secure=self._secure,
            httponly=True,
            samesite="strict",
        )

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(
            SESSION_COOKIE, path="/", secure=self._secure, httponly=True, samesite="strict"
        )


def _secret_key_material(settings: Settings) -> bytes:
    """The Fernet key of the app, the same one that ``store.Crypto`` uses."""
    if settings.secret_key is not None:
        return settings.secret_key.get_secret_value().strip().encode("utf-8")
    try:
        return settings.secret_key_path.read_bytes().strip()
    except FileNotFoundError:
        raise RuntimeError(
            f"The secret key file {settings.secret_key_path} does not exist. "
            "Create the AppContext before the session signer."
        ) from None
