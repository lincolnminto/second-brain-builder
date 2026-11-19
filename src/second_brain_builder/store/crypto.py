"""Credential encryption with Fernet.

The key comes from ``SBB_SECRET_KEY``. If that is unset, the key is read from
``<data_dir>/secret.key``, which is created with mode 0600 on first use. No
message, log line, or ``repr()`` contains key material.
"""

import logging
import os
import stat
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from second_brain_builder.config import Settings

logger = logging.getLogger(__name__)

KEY_FILE_MODE = 0o600


class SecretKeyError(ValueError):
    """The configured secret key or the key file is not a valid Fernet key."""


class DecryptionError(ValueError):
    """A ciphertext is damaged or was encrypted with another key."""


class Crypto:
    """Encrypts and decrypts credentials with one Fernet key."""

    def __init__(self, key: bytes) -> None:
        """Use ``key``, a URL-safe base64 Fernet key (``Fernet.generate_key()``)."""
        self._fernet = _fernet_from(key, origin="The key")

    def __repr__(self) -> str:
        return "Crypto(key=<redacted>)"

    @classmethod
    def load_or_create(cls, settings: Settings) -> "Crypto":
        """Return a ``Crypto`` with the key from the settings or the key file.

        ``settings.secret_key`` (``SBB_SECRET_KEY``) comes first. If it is unset,
        the key is read from ``settings.secret_key_path``. If that file does not
        exist, a new key is written to it with mode 0600. When two processes
        create the key at the same time, both get the same key: the file appears
        complete, in one step, or not at all.
        """
        if settings.secret_key is not None:
            key = settings.secret_key.get_secret_value().strip().encode()
            return cls(_fernet_key(key, origin="SBB_SECRET_KEY"))
        return cls(_load_or_create_key_file(settings.secret_key_path))

    def encrypt(self, plaintext: str) -> bytes:
        """Return the Fernet token for ``plaintext`` (UTF-8)."""
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        """Return the plaintext of a token from ``encrypt``.

        Raises ``DecryptionError`` if the token is damaged or another key made it,
        for example after ``SBB_SECRET_KEY`` changed.
        """
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken:
            raise DecryptionError(
                "The credential cannot be decrypted. It is damaged, or it was "
                "encrypted with a different secret key."
            ) from None
        return plaintext.decode("utf-8")


def _fernet_from(key: bytes, origin: str) -> Fernet:
    try:
        return Fernet(key)
    except ValueError:
        # The original error does not contain the key, but the traceback is not
        # needed: the message says what to fix.
        raise SecretKeyError(
            f"{origin} is not a valid Fernet key (32 bytes, URL-safe base64)."
        ) from None


def _fernet_key(key: bytes, origin: str) -> bytes:
    _fernet_from(key, origin)
    return key


def _load_or_create_key_file(path: Path) -> bytes:
    if not path.exists():
        return _create_key_file(path)
    return _read_key_file(path)


def _read_key_file(path: Path) -> bytes:
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        # The key decrypts every credential: never use it while other users can read it.
        try:
            path.chmod(KEY_FILE_MODE)
        except OSError:
            raise SecretKeyError(
                f"Other users can read the secret key file {path}, and its mode cannot be "
                "changed. Set its mode to 0600, or supply the key in SBB_SECRET_KEY."
            ) from None
        logger.warning(
            "The secret key file %s was readable by other users. Its mode is now 0600.", path
        )
    return _fernet_key(path.read_bytes().strip(), origin=f"The secret key file {path}")


def _create_key_file(path: Path) -> bytes:
    """Write a new key to ``path`` atomically, or read the key another process wrote."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Write the key to a private temporary file, then hard-link it to the final
    # name. The link fails if the file exists, so a concurrent process never
    # replaces a key that is in use, and no process reads a partial file.
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.touch(mode=KEY_FILE_MODE, exist_ok=False)
        temporary.chmod(KEY_FILE_MODE)
        with temporary.open("wb") as handle:
            handle.write(key + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            path.hardlink_to(temporary)
        except FileExistsError:
            return _read_key_file(path)
    finally:
        temporary.unlink(missing_ok=True)
    logger.info("Created a new secret key file at %s.", path)
    return key
