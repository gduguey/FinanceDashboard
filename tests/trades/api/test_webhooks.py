"""`trades.api.webhooks` — auto-provisioning a `users` row when Clerk sends `user.created`.

Builds real, validly-signed webhook deliveries with `svix`'s own `Webhook.sign`
(the same class `trades.api.webhooks` verifies with) against a throwaway test
secret — no real Clerk credentials or network access needed.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from svix.webhooks import Webhook

import db.session as session_module
import trades.api as trades_api
from sqlalchemy.orm import Session

from db.external_identities import lookup_user_id
from db.models import ExternalIdentity, User
from db.session import get_db
from trades.api import webhooks as webhooks_module
from trades.api.auth import require_clerk_session

_TEST_SIGNING_SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"  # noqa: S105 — a fake test-only secret, not a real one


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLERK_WEBHOOK_SIGNING_SECRET", _TEST_SIGNING_SECRET)
    webhooks_module._webhook_settings.cache_clear()
    yield
    webhooks_module._webhook_settings.cache_clear()


@pytest.fixture(autouse=True)
def _webhook_uses_the_test_engine(monkeypatch: pytest.MonkeyPatch, _db_engine: Engine) -> None:
    """Point `session_scope` (what the webhook handler itself commits through) at the test database.

    Unlike every other API test, `trades.api.webhooks` never goes through
    the `get_db` FastAPI dependency at all — it calls `db.session.session_scope`
    directly, the same convention cron jobs use (see that function's own
    docstring). `get_engine`'s process-wide `@lru_cache` would otherwise
    silently reuse whatever engine an earlier test call already cached —
    see `tests/db/test_session.py` for the same fix applied to `session_scope`
    directly.
    """
    monkeypatch.setattr(session_module, "get_engine", lambda: _db_engine)


@pytest.fixture(autouse=True)
def _clean_up_what_the_webhook_really_committed(_db_engine: Engine):
    """Delete the rows these tests commit, since nothing rolls them back.

    The handler provisions through `db.session.session_scope`, which opens
    its own session and commits for real — it has to, it is a webhook and
    there is no request transaction to join. So unlike almost every other
    test in this repo these leave rows behind in the shared scratch
    database, and the clerk ids they use are fixed strings rather than
    per-run uuids.

    That was a live defect, not a hypothetical one: `user_redelivered`
    survived into `tests/db/test_external_identities.py`, whose own test of
    the same id then read the leaked row and failed. Invisible in the
    default run only because `testpaths` collects `tests/db` *before*
    `tests/trades`, so the leak lands after the test it breaks — running the
    two directories in the other order failed on `v1.9.0` too.
    """
    with Session(_db_engine) as before:
        existing = {(row.provider, row.external_id) for row in before.query(ExternalIdentity).all()}
    yield
    with Session(_db_engine) as after:
        leaked = [row for row in after.query(ExternalIdentity).all() if (row.provider, row.external_id) not in existing]
        user_ids = [row.user_id for row in leaked]
        for row in leaked:
            after.delete(row)
        after.flush()
        for user_id in user_ids:
            after.query(User).filter_by(id=user_id).delete()
        after.commit()


def _signed_headers_and_body(payload: dict[str, object]) -> tuple[dict[str, str], str]:
    body = json.dumps(payload)
    msg_id = f"msg_{uuid.uuid4().hex}"
    timestamp = datetime.now(tz=UTC)
    signature = Webhook(_TEST_SIGNING_SECRET).sign(msg_id, timestamp, body)
    headers = {
        "svix-id": msg_id,
        "svix-timestamp": str(int(timestamp.timestamp())),
        "svix-signature": signature,
    }
    return headers, body


def _user_created_payload(clerk_user_id: str, email: str) -> dict[str, object]:
    return {
        "type": "user.created",
        "data": {
            "id": clerk_user_id,
            "primary_email_address_id": "idn_primary",
            "email_addresses": [{"id": "idn_primary", "email_address": email}],
        },
    }


def _user_deleted_payload(clerk_user_id: str) -> dict[str, object]:
    return {"type": "user.deleted", "data": {"id": clerk_user_id, "object": "user", "deleted": True}}


@pytest.fixture
def client(db_session) -> TestClient:
    def _override_get_db():
        yield db_session

    trades_api.app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(trades_api.app)
    trades_api.app.dependency_overrides.pop(get_db, None)


class TestClerkWebhook:
    def test_user_created_provisions_a_matching_users_row(self, client: TestClient, db_session) -> None:
        headers, body = _signed_headers_and_body(_user_created_payload("user_new_invitee", "invitee@example.com"))
        response = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert response.status_code == 200

        # external_identities has no RLS (see db.external_identities) — this lookup itself needs
        # no prior scoping, and is exactly how a real request resolves this same row.
        linked_id = lookup_user_id(db_session, "clerk", "user_new_invitee")
        assert linked_id is not None

        db_session.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(linked_id)})
        row = db_session.execute(text("SELECT email FROM users WHERE id = :id"), {"id": str(linked_id)}).first()
        assert row is not None
        assert row.email == "invitee@example.com"

    def test_redelivery_of_the_same_event_does_not_error_or_duplicate(self, client: TestClient, db_session) -> None:
        headers, body = _signed_headers_and_body(_user_created_payload("user_redelivered", "redelivered@example.com"))
        first = client.post("/api/webhooks/clerk", content=body, headers=headers)
        second = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200

        count = db_session.execute(
            text("SELECT count(*) FROM external_identities WHERE provider = 'clerk' AND external_id = :ext"),
            {"ext": "user_redelivered"},
        ).scalar()
        assert count == 1

    def test_ignores_event_types_other_than_user_created(self, client: TestClient) -> None:
        headers, body = _signed_headers_and_body({"type": "user.updated", "data": {"id": "user_x"}})
        response = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert response.status_code == 200

    def test_rejects_a_request_signed_with_the_wrong_secret(self, client: TestClient) -> None:
        payload = _user_created_payload("user_forged", "forged@example.com")
        body = json.dumps(payload)
        msg_id = "msg_forged"
        timestamp = datetime.now(tz=UTC)
        wrong_secret_signature = Webhook("whsec_" + "a" * 32).sign(msg_id, timestamp, body)
        headers = {
            "svix-id": msg_id,
            "svix-timestamp": str(int(timestamp.timestamp())),
            "svix-signature": wrong_secret_signature,
        }
        response = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert response.status_code == 400

    def test_rejects_a_request_with_a_malformed_signature_header(self, client: TestClient) -> None:
        body = json.dumps(_user_created_payload("user_malformed", "malformed@example.com"))
        headers = {
            "svix-id": "msg_malformed",
            "svix-timestamp": str(int(datetime.now(tz=UTC).timestamp())),
            "svix-signature": "not-even-base64!!",
        }
        response = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert response.status_code == 400

    def test_user_deleted_marks_the_matching_users_row_inactive(self, client: TestClient, db_session) -> None:
        created_headers, created_body = _signed_headers_and_body(
            _user_created_payload("user_to_delete", "to-delete@example.com")
        )
        client.post("/api/webhooks/clerk", content=created_body, headers=created_headers)
        linked_id = lookup_user_id(db_session, "clerk", "user_to_delete")
        assert linked_id is not None

        deleted_headers, deleted_body = _signed_headers_and_body(_user_deleted_payload("user_to_delete"))
        response = client.post("/api/webhooks/clerk", content=deleted_body, headers=deleted_headers)
        assert response.status_code == 200

        db_session.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(linked_id)})
        row = db_session.execute(text("SELECT is_active FROM users WHERE id = :id"), {"id": str(linked_id)}).first()
        assert row is not None
        assert row.is_active is False

    def test_user_deleted_leaves_the_identity_link_in_place(self, client: TestClient, db_session) -> None:
        """The soft delete must stay soft, and this is what makes it so now that provisioning is just-in-time.

        `resolve_current_user_id` provisions whenever the
        `external_identities` lookup misses (D1). So if this handler removed
        the link along with the activation flag, the deactivated user's very
        next request would miss, provision a brand-new `users` row and sign
        them straight back in under it — an undelete nobody asked for, and
        one that would look like a working app rather than like a bug.

        Because the link survives, the lookup still hits and provisioning
        never runs for a deactivated account at all. Re-inviting that person
        in Clerk mints a *new* Clerk id, which misses and provisions a second
        unrelated user — which is exactly what `db.models.User` documents
        should happen, and is not a resurrection of the first.
        """
        created_headers, created_body = _signed_headers_and_body(
            _user_created_payload("user_soft_deleted", "soft-deleted@example.com")
        )
        client.post("/api/webhooks/clerk", content=created_body, headers=created_headers)
        linked_id = lookup_user_id(db_session, "clerk", "user_soft_deleted")

        deleted_headers, deleted_body = _signed_headers_and_body(_user_deleted_payload("user_soft_deleted"))
        client.post("/api/webhooks/clerk", content=deleted_body, headers=deleted_headers)

        assert lookup_user_id(db_session, "clerk", "user_soft_deleted") == linked_id, (
            "the identity link was removed by the soft delete, so the next request would silently re-provision"
        )

    def test_user_deleted_for_an_unknown_clerk_id_is_a_noop(self, client: TestClient) -> None:
        headers, body = _signed_headers_and_body(_user_deleted_payload("user_never_provisioned"))
        response = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert response.status_code == 200

    def test_does_not_require_a_clerk_session(self, client: TestClient) -> None:
        """The webhook is called by Clerk's own servers, never a signed-in browser.

        `tests/conftest.py`'s `_bypass_clerk_auth_by_default` already
        overrides `require_clerk_session` for every test in the suite — this
        test removes that override for its own duration, so it proves the
        webhook route itself was never gated by it in the first place,
        rather than merely benefiting from the same bypass every other test does.
        """
        trades_api.app.dependency_overrides.pop(require_clerk_session, None)
        try:
            headers, body = _signed_headers_and_body(_user_created_payload("user_no_session", "no-session@example.com"))
            response = client.post("/api/webhooks/clerk", content=body, headers=headers)
        finally:
            trades_api.app.dependency_overrides[require_clerk_session] = lambda: None
        assert response.status_code == 200
