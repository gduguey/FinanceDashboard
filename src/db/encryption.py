"""App-level encryption for anything stored in `user_secrets.ciphertext`.

Postgres itself never sees a broker token or an LLM API key in plaintext —
every value is encrypted here, in the application, before `db.models.UserSecret`
writes it, and decrypted here after reading it back. `key_version` on that
model records which key below encrypted a given row, so the *current* signing
key can rotate (`APP_SECRETS_ENCRYPTION_KEY_VERSION` bumped, a new
`APP_SECRETS_ENCRYPTION_KEY` set) without making every already-encrypted row
undecryptable — `APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS` is where a rotated-out
key keeps living until every row encrypted under it has been re-encrypted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class SecretsEncryptionSettings(BaseSettings):
    """The key(s) that encrypt/decrypt `user_secrets.ciphertext`, read from `.env` or the environment.

    `current_key`/`current_version` are used for every new encryption.
    `previous_keys_json`, when set, is a JSON object mapping an older
    `key_version` (as a string) to the key that encrypted it — populated
    once a key is rotated out, so rows encrypted under it stay decryptable
    until they're re-encrypted under the current key.
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    current_key: SecretStr = Field(validation_alias="APP_SECRETS_ENCRYPTION_KEY")
    current_version: int = Field(default=1, validation_alias="APP_SECRETS_ENCRYPTION_KEY_VERSION")
    # `SecretStr`, not a plain `str`: this holds rotated-out key material, so it
    # must never surface in a repr/log/traceback of the settings object.
    previous_keys_json: SecretStr | None = Field(default=None, validation_alias="APP_SECRETS_ENCRYPTION_KEYS_PREVIOUS")

    def previous_keys(self) -> dict[int, str]:
        """Every rotated-out key, keyed by the `key_version` it encrypted rows under.

        Returns
        -------
        dict[int, str]
            Empty if `previous_keys_json` is unset.
        """
        if self.previous_keys_json is None:
            return {}
        raw = self.previous_keys_json.get_secret_value()
        if not raw:
            return {}
        return {int(version): key for version, key in json.loads(raw).items()}


@dataclass(frozen=True)
class EncryptedSecret:
    """One secret's ciphertext, plus which key version encrypted it — what `user_secrets` actually stores."""

    ciphertext: str
    key_version: int


def get_secrets_encryption_settings() -> SecretsEncryptionSettings:
    """Read encryption key settings from `.env`/the environment.

    A thin wrapper so every `SecretsEncryptor` resolves settings through one
    function — tests intercept this the same way
    `trades.utils.statement_archive.get_r2_credentials` is intercepted for R2.

    Returns
    -------
    SecretsEncryptionSettings
    """
    # current_key has no default (deliberately — see the class docstring) — pydantic-settings
    # fills it from APP_SECRETS_ENCRYPTION_KEY at runtime, but mypy has no pydantic plugin
    # configured here to know that, so it sees a required constructor argument never passed.
    return SecretsEncryptionSettings()  # type: ignore[call-arg]


class SecretsEncryptor:
    """Encrypts a secret under the current key; decrypts one under whichever key version it was encrypted with."""

    def __init__(self, settings: SecretsEncryptionSettings | None = None) -> None:
        """Bind this encryptor to one set of encryption keys.

        Parameters
        ----------
        settings
            Defaults to `get_secrets_encryption_settings()`.
        """
        self._settings = settings if settings is not None else get_secrets_encryption_settings()

    def encrypt(self, plaintext: str) -> EncryptedSecret:
        """Encrypt `plaintext` under the current key.

        Parameters
        ----------
        plaintext
            The secret value to encrypt.

        Returns
        -------
        EncryptedSecret
        """
        fernet = Fernet(self._settings.current_key.get_secret_value().encode())
        ciphertext = fernet.encrypt(plaintext.encode()).decode()
        return EncryptedSecret(ciphertext=ciphertext, key_version=self._settings.current_version)

    def decrypt(self, ciphertext: str, key_version: int) -> str:
        """Decrypt `ciphertext`, using whichever key `key_version` names.

        `_key_for_version` raises `ValueError` if `key_version` doesn't
        match the current key or any registered previous key.

        Parameters
        ----------
        ciphertext
            An `EncryptedSecret.ciphertext` previously returned by `encrypt`.
        key_version
            The matching `EncryptedSecret.key_version` — which key to decrypt with.

        Returns
        -------
        str
            The original plaintext.
        """
        key = self._key_for_version(key_version)
        return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()

    def _key_for_version(self, key_version: int) -> str:
        """Look up the raw key that encrypted `key_version`.

        Returns
        -------
        str

        Raises
        ------
        ValueError
            If `key_version` is neither the current version nor a known previous one.
        """
        if key_version == self._settings.current_version:
            return self._settings.current_key.get_secret_value()
        previous_keys = self._settings.previous_keys()
        if key_version not in previous_keys:
            message = f"No encryption key registered for key version {key_version}"
            raise ValueError(message)
        return previous_keys[key_version]
