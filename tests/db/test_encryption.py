"""Tests for `db.encryption`: symmetric encryption of secrets before they reach Postgres."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from db.encryption import SecretsEncryptionSettings, SecretsEncryptor


def _settings(current_version: int = 1, previous_keys_json: str | None = None) -> SecretsEncryptionSettings:
    return SecretsEncryptionSettings(
        _env_file=None,
        APP_SECRETS_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        APP_SECRETS_ENCRYPTION_KEY_VERSION=current_version,
        APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS=previous_keys_json,
    )


def test_encrypt_returns_ciphertext_different_from_plaintext() -> None:
    encryptor = SecretsEncryptor(_settings())

    encrypted = encryptor.encrypt("ibkr-flex-token-abc123")

    assert encrypted.ciphertext != "ibkr-flex-token-abc123"
    assert encrypted.key_version == 1


def test_decrypt_reverses_encrypt() -> None:
    encryptor = SecretsEncryptor(_settings())

    encrypted = encryptor.encrypt("super-secret-value")

    assert encryptor.decrypt(encrypted.ciphertext, encrypted.key_version) == "super-secret-value"


def test_two_encryptions_of_the_same_plaintext_produce_different_ciphertext() -> None:
    encryptor = SecretsEncryptor(_settings())

    first = encryptor.encrypt("same-value")
    second = encryptor.encrypt("same-value")

    assert first.ciphertext != second.ciphertext


def test_decrypt_with_unregistered_key_version_raises() -> None:
    encryptor = SecretsEncryptor(_settings())

    with pytest.raises(ValueError, match=r"key.*version"):
        encryptor.decrypt("anything", key_version=99)


def test_decrypt_still_works_after_rotating_the_current_key() -> None:
    old_key = Fernet.generate_key().decode()
    old_encryptor = SecretsEncryptor(
        SecretsEncryptionSettings(
            _env_file=None, APP_SECRETS_ENCRYPTION_KEY=old_key, APP_SECRETS_ENCRYPTION_KEY_VERSION=1
        )
    )
    encrypted_under_old_key = old_encryptor.encrypt("rotate-me")

    rotated_encryptor = SecretsEncryptor(
        SecretsEncryptionSettings(
            _env_file=None,
            APP_SECRETS_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            APP_SECRETS_ENCRYPTION_KEY_VERSION=2,
            APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS=f'{{"1": "{old_key}"}}',
        )
    )

    assert rotated_encryptor.decrypt(encrypted_under_old_key.ciphertext, encrypted_under_old_key.key_version) == (
        "rotate-me"
    )
    # New encryptions use the new current version, not the rotated-out one.
    assert rotated_encryptor.encrypt("rotate-me").key_version == 2
