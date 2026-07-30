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
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from clerk_backend_api.security import verifytoken
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import Engine

import db.session as session_module
import trades.api as trades_api
from db.external_identities import link_identity
from db.models import User
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
    yield
    auth._options.cache_clear()


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

    def test_rejects_a_valid_session_for_a_clerk_account_with_no_linked_user(
        self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey], kid: str
    ) -> None:
        """A genuinely valid Clerk session, but no `users` row links to it yet (e.g. the webhook hasn't run)."""
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
