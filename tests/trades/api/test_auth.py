"""`trades.api.auth` — Clerk session verification, in isolation from the rest of the app.

Builds its own throwaway RSA keypair per test rather than talking to a real
Clerk instance: `clerk_backend_api`'s own JWKS fetch is monkeypatched to
return a JWKS built from that keypair's public half, so signing a token
with the private half is indistinguishable, to `require_clerk_session`,
from a real Clerk session token — without any network access or real
Clerk credentials. Each test uses its own unique `kid` so the SDK's
internal (time-based, unclearable-from-outside) key cache never leaks a
key from one test into another.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any, Self

import httpx
import jwt
import pytest
from clerk_backend_api.models import SDKError
from clerk_backend_api.security import verifytoken
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import db.session as session_module
import trades.api as trades_api
from db.external_identities import link_identity, lookup_user_id
from db.models import ExternalIdentity, User
from trades.api import auth


@dataclass
class _FakeRequest:
    """The bare minimum `clerk_backend_api`'s `Requestish` protocol needs: a `.headers` mapping."""

    headers: dict[str, str]


@pytest.fixture
def keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def _clerk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_not_a_real_key")
    auth._options.cache_clear()
    auth._clerk_settings.cache_clear()
    yield
    auth._options.cache_clear()
    auth._clerk_settings.cache_clear()


@pytest.fixture
def kid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _mock_jwks(monkeypatch: pytest.MonkeyPatch, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str) -> None:
    _, public_key = keypair
    jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk.update(kid=kid, use="sig", alg="RS256")
    monkeypatch.setattr(verifytoken, "_fetch_jwks", lambda options: {"keys": [jwk]})


def _sign(private_key: rsa.RSAPrivateKey, claims: dict[str, Any], kid: str) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _valid_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {"sub": "user_123", "iat": now, "exp": now + 300}
    claims.update(overrides)
    return claims


class TestRequireClerkSession:
    def test_rejects_a_request_with_no_session_token(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            auth.require_clerk_session(_FakeRequest(headers={}))
        assert exc_info.value.status_code == 401

    def test_rejects_a_non_bearer_authorization_header(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            auth.require_clerk_session(_FakeRequest(headers={"Authorization": "Basic dXNlcjpwYXNz"}))
        assert exc_info.value.status_code == 401

    def test_accepts_a_validly_signed_unexpired_token(
        self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str
    ) -> None:
        private_key, _ = keypair
        token = _sign(private_key, _valid_claims(), kid)
        auth.require_clerk_session(_FakeRequest(headers={"Authorization": f"Bearer {token}"}))

    def test_accepts_a_token_carried_in_the_clerk_session_cookie(
        self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str
    ) -> None:
        private_key, _ = keypair
        token = _sign(private_key, _valid_claims(), kid)
        auth.require_clerk_session(_FakeRequest(headers={"cookie": f"__session={token}"}))

    def test_rejects_an_expired_token(self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str) -> None:
        private_key, _ = keypair
        now = int(time.time())
        token = _sign(private_key, _valid_claims(iat=now - 600, exp=now - 300), kid)
        with pytest.raises(HTTPException) as exc_info:
            auth.require_clerk_session(_FakeRequest(headers={"Authorization": f"Bearer {token}"}))
        assert exc_info.value.status_code == 401

    def test_rejects_a_token_signed_by_an_unknown_key(self, kid: str) -> None:
        forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _sign(forged_key, _valid_claims(), kid)
        with pytest.raises(HTTPException) as exc_info:
            auth.require_clerk_session(_FakeRequest(headers={"Authorization": f"Bearer {token}"}))
        assert exc_info.value.status_code == 401

    def test_rejects_a_token_whose_kid_matches_no_known_key(
        self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
    ) -> None:
        private_key, _ = keypair
        token = _sign(private_key, _valid_claims(), kid=str(uuid.uuid4()))
        with pytest.raises(HTTPException) as exc_info:
            auth.require_clerk_session(_FakeRequest(headers={"Authorization": f"Bearer {token}"}))
        assert exc_info.value.status_code == 401


class TestResolveCurrentUserId:
    """`resolve_current_user_id` now does a real database lookup (`db.external_identities`), not a formula.

    So, unlike `TestRequireClerkSession` above, these tests need a real
    (test) database — `_use_test_engine` points `db.session.get_engine` at
    it, the same fix `tests/db/test_session.py` established. Setup writes
    go through `session_scope` (genuinely committed), not the `db_session`
    fixture (savepoint-scoped, invisible to `resolve_current_user_id`'s
    own separately-opened session) — cleaned up explicitly afterward
    instead of relying on an automatic rollback.
    """

    @pytest.fixture(autouse=True)
    def _use_test_engine(self, monkeypatch: pytest.MonkeyPatch, _db_engine: Engine) -> None:
        monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)

    @staticmethod
    def _provision(user_id: uuid.UUID, clerk_user_id: str) -> None:
        with session_module.session_scope(user_id) as session:
            session.add(User(id=user_id, email=f"{user_id}@example.com"))
            link_identity(session, user_id, "clerk", clerk_user_id)
            session.commit()

    @staticmethod
    def _deprovision(user_id: uuid.UUID) -> None:
        with session_module.session_scope(user_id) as session:
            session.query(User).filter_by(id=user_id).delete()
            session.commit()

    def test_resolves_to_the_linked_user_for_the_token_s_sub_claim(
        self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str
    ) -> None:
        private_key, _ = keypair
        user_id = uuid.uuid4()
        self._provision(user_id, "user_some_invited_person")
        try:
            token = _sign(private_key, _valid_claims(sub="user_some_invited_person"), kid)
            resolved = auth.resolve_current_user_id(_FakeRequest(headers={"Authorization": f"Bearer {token}"}))
            assert resolved == user_id
        finally:
            self._deprovision(user_id)

    def test_two_different_clerk_users_resolve_to_two_different_ids(
        self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str
    ) -> None:
        private_key, _ = keypair
        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        self._provision(user_a, "user_a")
        self._provision(user_b, "user_b")
        try:
            token_a = _sign(private_key, _valid_claims(sub="user_a"), kid)
            token_b = _sign(private_key, _valid_claims(sub="user_b"), kid)
            resolved_a = auth.resolve_current_user_id(_FakeRequest(headers={"Authorization": f"Bearer {token_a}"}))
            resolved_b = auth.resolve_current_user_id(_FakeRequest(headers={"Authorization": f"Bearer {token_b}"}))
            assert resolved_a == user_a
            assert resolved_b == user_b
        finally:
            self._deprovision(user_a)
            self._deprovision(user_b)

    def test_rejects_an_invalid_token_the_same_way_as_require_clerk_session(self, kid: str) -> None:
        forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _sign(forged_key, _valid_claims(), kid)
        with pytest.raises(HTTPException) as exc_info:
            auth.resolve_current_user_id(_FakeRequest(headers={"Authorization": f"Bearer {token}"}))
        assert exc_info.value.status_code == 401

    def test_rejects_a_valid_session_clerk_will_not_confirm(
        self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuinely valid Clerk session with no linked row, and Clerk does not know the user either."""
        monkeypatch.setattr(auth, "_confirmed_primary_email", _clerk_refuses)
        private_key, _ = keypair
        token = _sign(private_key, _valid_claims(sub="user_never_provisioned"), kid)
        with pytest.raises(HTTPException) as exc_info:
            auth.resolve_current_user_id(_FakeRequest(headers={"Authorization": f"Bearer {token}"}))
        assert exc_info.value.status_code == 401


def test_an_unauthenticated_request_against_the_real_app_is_rejected() -> None:
    """Confirms the dependency is actually wired onto every router in `trades.api.api`.

    `tests/conftest.py`'s `_bypass_clerk_auth_by_default` overrides
    `require_clerk_session` for every other test in the suite — this test
    removes that override for its own duration, so it exercises the real
    dependency exactly as a genuinely unauthenticated browser request would.
    """
    trades_api.app.dependency_overrides.pop(auth.require_clerk_session, None)
    try:
        response = TestClient(trades_api.app).get("/api/v1/trades/overview", params={"as_of": "2026-01-03"})
    finally:
        trades_api.app.dependency_overrides[auth.require_clerk_session] = lambda: None
    assert response.status_code == 401


@dataclass
class _FakeEmailAddress:
    """The two fields `auth._primary_email` reads off a Clerk email address."""

    id: str
    email_address: str


@dataclass
class _FakeClerkUser:
    """A Clerk `User` stand-in carrying only what `_confirmed_primary_email` inspects."""

    id: str = "user_never_provisioned"
    banned: bool = False
    locked: bool = False
    deprovisioned: bool = False
    primary_email_address_id: str | None = "idn_primary"
    email_addresses: list[_FakeEmailAddress] | None = None

    def __post_init__(self) -> None:
        if self.email_addresses is None:
            self.email_addresses = [_FakeEmailAddress(id="idn_primary", email_address="invitee@example.test")]


class _FakeUsers:
    """`clerk.users`, recording how each call was bounded so the test can assert on it."""

    def __init__(self, outcome: _FakeClerkUser | Exception, calls: list[dict[str, Any]]) -> None:
        self._outcome = outcome
        self._calls = calls

    def get(self, **kwargs: Any) -> _FakeClerkUser:
        self._calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeClerk:
    """A stand-in for `clerk_backend_api.Clerk`, used as the context manager `auth` uses."""

    def __init__(self, outcome: _FakeClerkUser | Exception, calls: list[dict[str, Any]]) -> None:
        self.users = _FakeUsers(outcome, calls)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _install_fake_clerk(monkeypatch: pytest.MonkeyPatch, outcome: _FakeClerkUser | Exception) -> list[dict[str, Any]]:
    """Replace `auth.Clerk` with a stand-in and hand back the list its calls are recorded into.

    Returns
    -------
    list[dict[str, Any]]
        One entry per `users.get` call, holding that call's keyword arguments.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(auth, "Clerk", lambda **_kwargs: _FakeClerk(outcome, calls))
    return calls


def _sdk_error(status_code: int) -> SDKError:
    """Build the SDK's own error for a non-2XX Clerk response, which needs a real response to render.

    Returns
    -------
    SDKError
    """
    response = httpx.Response(status_code, request=httpx.Request("GET", "https://api.clerk.test/v1/users/x"))
    return SDKError("Unexpected error occurred", response)


@contextmanager
def _records_from_the_auth_logger() -> Iterator[list[logging.LogRecord]]:
    """Capture `trades.api.auth`'s own records, by attaching a handler to that logger directly.

    Deliberately not `caplog`, which collects through a handler on the *root*
    logger: that makes what this test sees depend on propagation and on the
    global level, both of which anything else in a full-suite run can change,
    and it would have counted an unrelated module's error as this one's.
    Listening to the one logger under test is both narrower and stable.

    Yields
    ------
    list[logging.LogRecord]
        Filled as records are emitted.
    """
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect()
    previous_level = auth.logger.level
    auth.logger.addHandler(handler)
    auth.logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        auth.logger.removeHandler(handler)
        auth.logger.setLevel(previous_level)


def _clerk_refuses(_clerk_user_id: str) -> str:
    """Stand in for `_confirmed_primary_email` when Clerk will not confirm the account.

    Returns
    -------
    str
        Never — it always raises.

    Raises
    ------
    HTTPException
        401, exactly as the real function does.
    """
    raise HTTPException(status_code=401, detail=auth._NO_ACCOUNT_DETAIL)


class TestJustInTimeProvisioning:
    """`resolve_current_user_id` provisions on a lookup miss instead of locking the user out (D1).

    Needs a real database for the same reason `TestResolveCurrentUserId`
    does, and for one more: the whole point is that a row gets written, so
    the assertions read it back through a separately-opened session rather
    than trusting the return value alone.
    """

    @pytest.fixture(autouse=True)
    def _use_test_engine(self, monkeypatch: pytest.MonkeyPatch, _db_engine: Engine) -> None:
        monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)

    @pytest.fixture
    def clerk_user_id(self) -> str:
        return f"user_jit_{uuid.uuid4().hex}"

    @pytest.fixture(autouse=True)
    def _cleanup(self, _db_engine: Engine, clerk_user_id: str) -> None:
        yield
        with session_module.session_scope(uuid.uuid4()) as teardown:
            provisioned = lookup_user_id(teardown, "clerk", clerk_user_id)
            teardown.query(ExternalIdentity).filter_by(provider="clerk", external_id=clerk_user_id).delete()
            if provisioned is not None:
                teardown.query(User).filter_by(id=provisioned).delete()
            teardown.commit()

    @staticmethod
    def _request(private_key: rsa.RSAPrivateKey, kid: str, clerk_user_id: str) -> _FakeRequest:
        token = _sign(private_key, _valid_claims(sub=clerk_user_id), kid)
        return _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    def test_a_valid_session_with_no_row_yet_provisions_one(
        self,
        keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
        kid: str,
        clerk_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
        _db_engine: Engine,  # noqa: PT019 — needs the fixture's returned Engine, not just its setup side effect
    ) -> None:
        """The lockout itself: the webhook never arrived, and the request succeeds anyway."""
        _install_fake_clerk(monkeypatch, _FakeClerkUser(id=clerk_user_id))
        private_key, _ = keypair

        resolved = auth.resolve_current_user_id(self._request(private_key, kid, clerk_user_id))

        with Session(_db_engine) as reader:
            assert lookup_user_id(reader, "clerk", clerk_user_id) == resolved
            user = reader.get(User, resolved)
            assert user is not None
            assert user.email == "invitee@example.test"

    def test_the_second_request_reuses_the_row_and_never_calls_clerk_again(
        self,
        keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
        kid: str,
        clerk_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ "Only on a lookup miss" — an already-provisioned user must not pay for an external call.

        Asserted by counting calls rather than by timing anything: the first
        request provisions and calls Clerk once, and every request after it
        resolves through the same indexed lookup every request already did.
        """
        calls = _install_fake_clerk(monkeypatch, _FakeClerkUser(id=clerk_user_id))
        private_key, _ = keypair

        first = auth.resolve_current_user_id(self._request(private_key, kid, clerk_user_id))
        second = auth.resolve_current_user_id(self._request(private_key, kid, clerk_user_id))
        third = auth.resolve_current_user_id(self._request(private_key, kid, clerk_user_id))

        assert second == first
        assert third == first
        assert len(calls) == 1, f"Clerk was called on the hot path: {len(calls)} times for three requests"

    def test_the_clerk_call_is_bounded_and_does_not_retry(
        self,
        keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
        kid: str,
        clerk_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The SDK's default is a backoff with a one-hour `max_elapsed_time`, which inside a request is a hang.

        Both arguments are asserted because leaving either unset re-inherits
        that default: `timeout_ms` bounds the single attempt, `retries=None`
        is what stops there being several.
        """
        calls = _install_fake_clerk(monkeypatch, _FakeClerkUser(id=clerk_user_id))
        private_key, _ = keypair

        auth.resolve_current_user_id(self._request(private_key, kid, clerk_user_id))

        assert calls == [{"user_id": clerk_user_id, "timeout_ms": auth.PROVISIONING_TIMEOUT_MS, "retries": None}]


class TestJustInTimeFailsClosed:
    """Every way provisioning can fail answers the same 401 as an unprovisioned session, and writes nothing.

    A provisioning path that failed *open* would be a genuine security hole
    where there is currently only an inconvenience: anyone holding a session
    token for a deleted or banned Clerk account would have an internal
    account minted for them. These call `_confirmed_primary_email` directly
    — the database is not reached at all, which is itself the assertion for
    the write half.
    """

    @pytest.fixture(autouse=True)
    def _no_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail loudly if any of these reaches the database, rather than provisioning quietly."""

        def forbidden() -> Engine:
            message = "a fail-closed path opened a database session"
            raise AssertionError(message)

        monkeypatch.setattr(session_module, "get_engine", forbidden)

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(_sdk_error(404), id="clerk-has-no-such-user"),
            pytest.param(_sdk_error(503), id="clerk-is-erroring"),
            pytest.param(httpx.ConnectError("connection refused"), id="clerk-is-unreachable"),
            pytest.param(httpx.ReadTimeout("timed out"), id="clerk-times-out"),
        ],
    )
    def test_a_clerk_that_does_not_answer_is_a_401(self, monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
        """Including the two transport failures, which are not `ClerkBaseError` and were the fail-open risk.

        `BaseSDK.do_request` re-raises whatever `httpx` raised when no retry
        config is set, so an outage or a timeout arrives as a bare
        `httpx` exception. Catching only the SDK's own hierarchy would turn
        exactly the outage `PROVISIONING_TIMEOUT_MS` exists for into a 500.
        """
        _install_fake_clerk(monkeypatch, failure)

        with pytest.raises(HTTPException) as exc_info:
            auth._confirmed_primary_email("user_whoever")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == auth._NO_ACCOUNT_DETAIL

    @pytest.mark.parametrize("unusable", ["banned", "locked", "deprovisioned"])
    def test_an_unusable_clerk_account_is_a_401(self, monkeypatch: pytest.MonkeyPatch, unusable: str) -> None:
        """A session token can outlive a ban, so "Clerk knows this id" is not enough to provision on."""
        _install_fake_clerk(monkeypatch, _FakeClerkUser(**{unusable: True}))

        with pytest.raises(HTTPException) as exc_info:
            auth._confirmed_primary_email("user_whoever")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == auth._NO_ACCOUNT_DETAIL

    def test_an_account_with_no_primary_email_is_a_401_that_says_so_in_the_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one permanent failure, and the only one the log has to distinguish.

        Every other refusal here is transient — the next request retries and
        may well succeed — so a single indistinguishable 401 reads as "the
        webhook is still coming". This one never resolves on its own, and an
        operator looking at a user who cannot sign in has no other way to
        tell the two apart, because the client is deliberately told nothing.
        """
        _install_fake_clerk(monkeypatch, _FakeClerkUser(primary_email_address_id="idn_missing"))

        with _records_from_the_auth_logger() as records, pytest.raises(HTTPException) as exc_info:
            auth._confirmed_primary_email("user_no_email")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == auth._NO_ACCOUNT_DETAIL
        permanent = [record for record in records if record.levelno >= logging.ERROR]
        assert len(permanent) == 1, "a permanent lockout was logged at the same level as a transient one"
        assert "no primary email address" in permanent[0].getMessage()

    def test_the_401_body_is_the_same_one_an_unprovisioned_session_already_got(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The client must not be able to tell "banned" from "webhook is late" from "Clerk is down".

        Otherwise the 401 becomes an oracle for which Clerk accounts exist
        and what state they are in, to anyone who can obtain any valid
        session token.
        """
        details = set()
        for outcome in (
            httpx.ConnectError("connection refused"),
            _sdk_error(404),
            _FakeClerkUser(banned=True),
            _FakeClerkUser(primary_email_address_id="idn_missing"),
        ):
            _install_fake_clerk(monkeypatch, outcome)
            with pytest.raises(HTTPException) as exc_info:
                auth._confirmed_primary_email("user_whoever")
            details.add((exc_info.value.status_code, exc_info.value.detail))

        assert details == {(401, auth._NO_ACCOUNT_DETAIL)}
