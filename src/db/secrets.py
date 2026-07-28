"""Get/set/delete one user's encrypted secret — what every credential module in this app is built on.

Every broker token and LLM API key ends up here, in Postgres, encrypted at
rest (see `db.encryption`) — never in `.env`, never in a JSON file on disk.
`kind` is a caller-chosen label (e.g. `"broker_credentials"`, `"llm_api_key"`)
for filtering/auditing; `key` is the caller-chosen lookup name (e.g.
`"broker:ibkr"`, `"llm:gemini"`) two different secrets never collide on by
accident, the same convention `trades.brokers.*` and `accounting.llm.settings`
build their own key names from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.encryption import SecretsEncryptor
from db.models import UserSecret

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def get_secret(session: Session, user_id: uuid.UUID, key: str) -> str | None:
    """Return the decrypted plaintext for `(user_id, key)`, or `None` if nothing's saved.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose secret this is.
    key
        The secret's lookup name, e.g. `"broker:ibkr"`.

    Returns
    -------
    str or None
    """
    row = session.get(UserSecret, (user_id, key))
    if row is None:
        return None
    return SecretsEncryptor().decrypt(row.ciphertext, row.encryption_key_version)


def set_secret(session: Session, user_id: uuid.UUID, key: str, kind: str, plaintext: str) -> None:
    """Encrypt `plaintext` and persist it as `(user_id, key)`, overwriting whatever was there before.

    Commits immediately — a secret write is never batched with unrelated
    changes in the same transaction, so a caller never has to remember to
    commit on its behalf.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose secret this is.
    key
        The secret's lookup name, e.g. `"broker:ibkr"`.
    kind
        A caller-chosen label grouping secrets by shape, e.g. `"broker_credentials"`.
    plaintext
        The secret value to encrypt and persist.
    """
    encrypted = SecretsEncryptor().encrypt(plaintext)
    row = session.get(UserSecret, (user_id, key))
    if row is None:
        session.add(
            UserSecret(
                user_id=user_id,
                key=key,
                kind=kind,
                ciphertext=encrypted.ciphertext,
                encryption_key_version=encrypted.key_version,
            )
        )
    else:
        row.kind = kind
        row.ciphertext = encrypted.ciphertext
        row.encryption_key_version = encrypted.key_version
    session.commit()


def delete_secret(session: Session, user_id: uuid.UUID, key: str) -> None:
    """Remove `(user_id, key)`, if it exists. A no-op otherwise.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose secret this is.
    key
        The secret's lookup name, e.g. `"broker:ibkr"`.
    """
    session.query(UserSecret).filter_by(user_id=user_id, key=key).delete()
    session.commit()
