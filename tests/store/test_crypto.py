import logging
import stat
import threading

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from second_brain_builder.config import Settings
from second_brain_builder.store import Crypto, DecryptionError, SecretKeyError

TOKEN = "example-provider-api-key-0000"


def test_first_load_creates_a_private_key_file_that_later_loads_reuse(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    first = Crypto.load_or_create(settings)
    ciphertext = first.encrypt(TOKEN)

    key_file = settings.secret_key_path
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    key = key_file.read_text().strip()
    assert TOKEN.encode() not in ciphertext
    # A restart (or the other container) reads the same key.
    assert Crypto.load_or_create(settings).decrypt(ciphertext) == TOKEN
    assert key not in caplog.text
    assert key not in repr(first)
    assert [path.name for path in settings.data_dir.iterdir()] == ["secret.key"]


def test_key_file_readable_by_others_is_made_private_before_use(settings: Settings) -> None:
    key = Fernet.generate_key()
    key_file = settings.secret_key_path
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key + b"\n")
    key_file.chmod(0o644)  # for example, restored from a backup

    crypto = Crypto.load_or_create(settings)

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert Crypto(key).decrypt(crypto.encrypt(TOKEN)) == TOKEN


def test_key_file_that_stays_readable_by_others_is_refused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_file = settings.secret_key_path
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(Fernet.generate_key() + b"\n")
    key_file.chmod(0o644)

    def owned_by_another_user(self: object, mode: int) -> None:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(type(key_file), "chmod", owned_by_another_user)

    with pytest.raises(SecretKeyError, match="0600"):
        Crypto.load_or_create(settings)


def test_secret_key_setting_takes_precedence_over_the_key_file(settings: Settings) -> None:
    file_crypto = Crypto.load_or_create(settings)
    key = Fernet.generate_key().decode()
    env_settings = settings.model_copy(update={"secret_key": SecretStr(key)})

    env_crypto = Crypto.load_or_create(env_settings)

    ciphertext = env_crypto.encrypt(TOKEN)
    assert Crypto(key.encode()).decrypt(ciphertext) == TOKEN
    with pytest.raises(DecryptionError, match="different secret key"):
        file_crypto.decrypt(ciphertext)


def test_secret_key_setting_does_not_create_a_key_file(settings: Settings) -> None:
    key = Fernet.generate_key().decode()

    Crypto.load_or_create(settings.model_copy(update={"secret_key": SecretStr(key)}))

    assert not settings.secret_key_path.exists()


def test_invalid_keys_are_rejected_without_showing_them(settings: Settings) -> None:
    bad_key = "not-a-fernet-key-example-value"
    with pytest.raises(SecretKeyError, match="SBB_SECRET_KEY") as raised:
        Crypto.load_or_create(settings.model_copy(update={"secret_key": SecretStr(bad_key)}))
    assert bad_key not in str(raised.value)

    settings.secret_key_path.write_text(bad_key)
    with pytest.raises(SecretKeyError, match="secret key file") as raised:
        Crypto.load_or_create(settings)
    assert bad_key not in str(raised.value)


def test_damaged_ciphertext_raises_decryption_error(settings: Settings) -> None:
    crypto = Crypto.load_or_create(settings)
    ciphertext = bytearray(crypto.encrypt(TOKEN))
    ciphertext[-5] ^= 0x01

    with pytest.raises(DecryptionError):
        crypto.decrypt(bytes(ciphertext))


def test_concurrent_first_loads_agree_on_one_key(settings: Settings) -> None:
    # The app and the worker start together on an empty data volume.
    count = 8
    barrier = threading.Barrier(count)
    results: list[Crypto] = []
    errors: list[BaseException] = []

    def load() -> None:
        barrier.wait()
        try:
            results.append(Crypto.load_or_create(settings))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=load) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(results) == count
    ciphertext = results[0].encrypt(TOKEN)
    assert all(crypto.decrypt(ciphertext) == TOKEN for crypto in results)
    assert [path.name for path in settings.data_dir.iterdir()] == ["secret.key"]
