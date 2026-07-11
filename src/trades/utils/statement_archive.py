"""Archive raw broker statements to Cloudflare R2 (S3-compatible), falling back to local disk.

Per `docs/architecture.md`'s caching rule, a raw statement is the one thing
under `data/` that isn't safely re-fetchable, so it gets archived verbatim
before anything else touches it. `StatementArchive` is where that archive
actually lives: Cloudflare R2 when `R2_ACCOUNT_ID` etc. are set in `.env`,
the same local `raw_statements/` directory as before otherwise — so a
fresh clone with no R2 bucket configured keeps working exactly as it
always has.

A deliberate duplicate of `accounting.utils.statement_archive`, not a
shared import from it — see `accounting.utils.io_utils`'s own docstring
for why this repo keeps separate copies of infra code like this between
the two packages.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterator

_REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_USER_ID = "8f14e45f-ceea-467e-bb1c-a2e2f7cbc09e"
"""Placeholder owner id prefixed onto every R2 object key (`statements/<user_id>/...`).

There is no auth/Postgres users table yet, so every statement in this
single-user deployment is archived under one fixed id. Once real users
exist, every call site that constructs a `StatementArchive` is the thing
that needs to start passing the authenticated request's actual
`current_user.id` here instead — the key layout already assumes that
shape, so nothing about where objects live in the bucket has to change.
"""


@dataclass(frozen=True)
class _ResolvedR2Credentials:
    """R2 credentials known to be fully present — see `R2Credentials.resolve`."""

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    endpoint_url: str


class R2Credentials(BaseSettings):
    """Cloudflare R2 credentials, read from `.env` or the environment.

    Every field is optional: their absence just means R2 isn't set up yet,
    not a startup error — `resolve()` is how a caller finds out which case
    it's in.
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    account_id: SecretStr | None = Field(default=None, validation_alias="R2_ACCOUNT_ID")
    access_key_id: SecretStr | None = Field(default=None, validation_alias="R2_ACCESS_KEY_ID")
    secret_access_key: SecretStr | None = Field(default=None, validation_alias="R2_SECRET_ACCESS_KEY")
    bucket_name: str | None = Field(default=None, validation_alias="R2_BUCKET_NAME")
    endpoint_url: str | None = Field(default=None, validation_alias="R2_ENDPOINT_URL")

    def resolve(self) -> _ResolvedR2Credentials | None:
        """All five fields, unwrapped, or `None` if any is missing.

        Returns
        -------
        _ResolvedR2Credentials or None
            `None` means "R2 isn't configured, use local disk" — never
            raises, since running without R2 set up is an expected,
            supported state, not an error.
        """
        account_id, access_key_id, secret_access_key = self.account_id, self.access_key_id, self.secret_access_key
        bucket_name, endpoint_url = self.bucket_name, self.endpoint_url
        if (
            account_id is None
            or access_key_id is None
            or secret_access_key is None
            or bucket_name is None
            or endpoint_url is None
        ):
            return None
        return _ResolvedR2Credentials(
            account_id=account_id.get_secret_value(),
            access_key_id=access_key_id.get_secret_value(),
            secret_access_key=secret_access_key.get_secret_value(),
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
        )


def get_r2_credentials() -> R2Credentials:
    """Read R2 credentials from `.env`/the environment.

    A thin wrapper around `R2Credentials()` so every `StatementArchive`
    resolves credentials through one function — tests intercept R2 by
    monkeypatching this, the same seam `trades.credentials.resolve_ibkr_credentials`
    is for IBKR credentials.

    Returns
    -------
    R2Credentials
    """
    return R2Credentials()


class StatementArchive:
    """Read/write access to one family of archived raw statements.

    Every file this reads or writes lives at the same relative path
    whether it's actually stored under `remote_prefix` in R2 or under
    `local_root` on disk — so a caller always identifies a file by that
    one relative path, never a full key or filesystem path, and never
    anything a client request could supply directly. The R2 key is always
    `f"{remote_prefix}/{relative_path}"`, derived here, server-side.
    `exists`/`write`/`read` all reject a `relative_path` that's absolute or
    contains a `..` segment, enforcing that invariant in code rather than
    just asserting it here — callers have gotten this wrong before (see
    `accounting.importers.ingest._archive_raw_statement`, which forwards
    unvalidated form fields into it).
    """

    def __init__(self, local_root: Path, remote_prefix: str, credentials: R2Credentials | None = None) -> None:
        """Bind this archive to one local directory and its mirrored R2 prefix.

        Parameters
        ----------
        local_root
            Where files live on disk when R2 isn't configured.
        remote_prefix
            The R2 key prefix files live under when it is (see
            `DEFAULT_USER_ID` — always starts with `statements/<user_id>`).
        credentials
            R2 credentials to use; defaults to `get_r2_credentials()`.
        """
        self.local_root = local_root
        self.remote_prefix = remote_prefix
        self._resolved = (credentials if credentials is not None else get_r2_credentials()).resolve()
        self._boto_client: Any | None = None

    @staticmethod
    def _validated(relative_path: str) -> str:
        """Reject a `relative_path` that could escape `local_root`/`remote_prefix`.

        Parameters
        ----------
        relative_path
            The path to check.

        Returns
        -------
        str
            `relative_path`, unchanged, once confirmed safe.

        Raises
        ------
        ValueError
            If `relative_path` is absolute or contains a `..` segment.
        """
        as_path = PurePosixPath(relative_path)
        if as_path.is_absolute() or ".." in as_path.parts:
            message = f"relative_path must stay within the archive, got {relative_path!r}"
            raise ValueError(message)
        return relative_path

    def _client(self, resolved: _ResolvedR2Credentials) -> Any:  # noqa: ANN401 — boto3 ships no typed client
        """Build this archive's boto3 S3 client on first use, then reuse it.

        Parameters
        ----------
        resolved
            This archive's resolved R2 credentials.

        Returns
        -------
        Any
            A boto3 S3 client pointed at `resolved.endpoint_url`.
        """
        if self._boto_client is None:
            self._boto_client = boto3.client(
                "s3",
                endpoint_url=resolved.endpoint_url,
                aws_access_key_id=resolved.access_key_id,
                aws_secret_access_key=resolved.secret_access_key,
                region_name="auto",
            )
        return self._boto_client

    def _key(self, relative_path: str) -> str:
        return f"{self.remote_prefix}/{relative_path}"

    def exists(self, relative_path: str) -> bool:
        """Whether a file is already archived at `relative_path`.

        Rejects (via `_validated`) a `relative_path` that's absolute or
        contains a `..` segment, raising `ValueError`.

        Returns
        -------
        bool

        Raises
        ------
        botocore.exceptions.ClientError
            If R2 is configured and the lookup fails for any reason other
            than the object not existing.
        """
        relative_path = self._validated(relative_path)
        resolved = self._resolved
        if resolved is None:
            return (self.local_root / relative_path).exists()
        try:
            self._client(resolved).head_object(Bucket=resolved.bucket_name, Key=self._key(relative_path))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise
        return True

    def write(self, relative_path: str, data: bytes) -> None:
        """Archive `data` at `relative_path`, creating parent directories/prefixes as needed.

        Rejects (via `_validated`) a `relative_path` that's absolute or
        contains a `..` segment, raising `ValueError`.
        """
        relative_path = self._validated(relative_path)
        resolved = self._resolved
        if resolved is None:
            path = self.local_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return
        self._client(resolved).put_object(Bucket=resolved.bucket_name, Key=self._key(relative_path), Body=data)

    def write_if_absent(self, relative_path: str, data: bytes) -> bool:
        """Archive `data` at `relative_path` only if nothing is there yet, the create itself atomic.

        Unlike `write`, this never overwrites an existing file: the create
        is atomic (`O_EXCL` locally, `IfNoneMatch` on R2), so two concurrent
        callers racing for the same `relative_path` can't clobber each
        other the way a separate `exists()` check followed by `write()`
        could.

        Rejects (via `_validated`) a `relative_path` that's absolute or
        contains a `..` segment, raising `ValueError`.

        Parameters
        ----------
        relative_path
            Where to archive `data`, if nothing is there yet.
        data
            The bytes to archive.

        Returns
        -------
        bool
            `True` if this call actually wrote the file; `False` if
            something was already there and nothing was changed.

        Raises
        ------
        botocore.exceptions.ClientError
            If R2 is configured and the write fails for any reason other
            than the object already existing.
        """
        relative_path = self._validated(relative_path)
        resolved = self._resolved
        if resolved is None:
            path = self.local_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as handle:
                    handle.write(data)
            except FileExistsError:
                return False
            return True
        try:
            self._client(resolved).put_object(
                Bucket=resolved.bucket_name, Key=self._key(relative_path), Body=data, IfNoneMatch="*"
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"}:
                return False
            raise
        return True

    def read(self, relative_path: str) -> bytes:
        """Read back the bytes archived at `relative_path`.

        Rejects (via `_validated`) a `relative_path` that's absolute or
        contains a `..` segment, raising `ValueError`.

        Returns
        -------
        bytes
        """
        relative_path = self._validated(relative_path)
        resolved = self._resolved
        if resolved is None:
            return (self.local_root / relative_path).read_bytes()
        response = self._client(resolved).get_object(Bucket=resolved.bucket_name, Key=self._key(relative_path))
        return response["Body"].read()

    def list_relative_paths(self, pattern: str = "*") -> list[str]:
        """Every archived file's path relative to this archive's root, matching `pattern`, sorted.

        Parameters
        ----------
        pattern
            An `fnmatch` pattern (e.g. `"*.xml"`, `"*/*/*.csv"`) matched
            against each file's path relative to `local_root`/`remote_prefix`
            — the same shape regardless of which one is actually in use.

        Returns
        -------
        list[str]
        """
        resolved = self._resolved
        if resolved is None:
            relative_paths = (
                [path.relative_to(self.local_root).as_posix() for path in self.local_root.rglob("*") if path.is_file()]
                if self.local_root.exists()
                else []
            )
        else:
            prefix = f"{self.remote_prefix}/"
            paginator = self._client(resolved).get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=resolved.bucket_name, Prefix=prefix)
            relative_paths = [obj["Key"][len(prefix) :] for page in pages for obj in page.get("Contents", [])]
        return sorted(fnmatch.filter(relative_paths, pattern))

    def read_all(self, pattern: str = "*") -> Iterator[tuple[str, bytes]]:
        """Yield `(relative_path, data)` for every archived file matching `pattern`, for a bulk export.

        Returns
        -------
        Iterator[tuple[str, bytes]]
        """
        return ((relative_path, self.read(relative_path)) for relative_path in self.list_relative_paths(pattern))
