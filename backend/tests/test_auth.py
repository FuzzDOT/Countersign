"""Auth, end to end. Requires a live Postgres.

Marked `integration` because these exercise real rows: lockout counters,
refresh-token families and the unique index that turns a duplicate
registration into a 409. Mocking the session would test the mock.

The three properties worth the most attention are the ones that are wrong by
default in most implementations: user enumeration, refresh-token replay, and
cross-tenant reads.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from core.config import Settings
from db.models import RefreshToken, User

pytestmark = pytest.mark.integration

STRONG_PASSWORD = "correct-horse-battery-staple-9471"


def register(client: TestClient, email: str, org: str = "Acme Audit") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": STRONG_PASSWORD, "org_name": org},
    )
    assert response.status_code == 201, response.text
    return response.json()


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.test"


# ── register ─────────────────────────────────────────────────────────────────


def test_register_creates_an_owner_and_sets_a_refresh_cookie(
    client: TestClient, settings: Settings
) -> None:
    payload = register(client, unique_email())
    assert payload["user"]["role"] == "owner"
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] > 0
    # The refresh token must never appear in the body.
    assert "refresh_token" not in payload

    cookie = client.cookies.get(settings.refresh_cookie_name)
    assert cookie, "no refresh cookie was set"


def test_register_rejects_a_weak_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": "password1234", "org_name": "Weak Co"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] in ("VALIDATION_FAILED", "WEAK_PASSWORD")


def test_duplicate_email_is_a_conflict(client: TestClient) -> None:
    email = unique_email()
    register(client, email)
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": STRONG_PASSWORD, "org_name": "Second Co"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


# ── login ────────────────────────────────────────────────────────────────────


def test_login_succeeds_with_correct_credentials(client: TestClient) -> None:
    email = unique_email()
    register(client, email)
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == email


def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: TestClient,
) -> None:
    """The enumeration defense.

    Same status, same code, same message for "no such account" and "wrong
    password". The timing side of this is covered in tests/test_security.py,
    which exercises `pad_to_target` directly — asserting wall-clock equality
    through the client would be flaky on shared CI.
    """
    email = unique_email()
    register(client, email)

    wrong = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "not-the-password-at-all"}
    )
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": unique_email("ghost"), "password": "not-the-password-at-all"},
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_repeated_failures_lock_the_account(
    client: TestClient, settings: Settings
) -> None:
    email = unique_email()
    register(client, email)

    codes: list[str] = []
    for _ in range(settings.login_max_attempts):
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password-here"}
        )
        codes.append(response.json()["error"]["code"])

    assert codes[-1] == "ACCOUNT_LOCKED"

    # The correct password must not open a locked account.
    locked = client.post(
        "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "ACCOUNT_LOCKED"
    assert "locked_until" in locked.json()["error"]["details"]


def test_successful_login_clears_the_failure_counter(
    client: TestClient, db_session
) -> None:  # type: ignore[no-untyped-def]
    email = unique_email()
    register(client, email)

    client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-once"})
    client.post("/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD})

    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    assert user.failed_logins == 0
    assert user.first_failed_login_at is None
    assert user.locked_until is None


# ── refresh ──────────────────────────────────────────────────────────────────


def test_refresh_rotates_the_token(client: TestClient, settings: Settings) -> None:
    register(client, unique_email())
    first_cookie = client.cookies.get(settings.refresh_cookie_name)

    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    second_cookie = client.cookies.get(settings.refresh_cookie_name)

    assert second_cookie != first_cookie, "the refresh token was not rotated"
    assert response.json()["access_token"]


def test_replaying_a_used_refresh_token_revokes_the_whole_family(
    client: TestClient, settings: Settings, db_session
) -> None:  # type: ignore[no-untyped-def]
    """The property that makes a stolen refresh token a bounded incident.

    An already-rotated token being presented again means it leaked, so every
    token in that family is revoked and the client is forced to re-authenticate
    rather than the attacker quietly continuing to refresh.
    """
    payload = register(client, unique_email())
    user_id = uuid.UUID(payload["user"]["id"])
    stolen = client.cookies.get(settings.refresh_cookie_name)

    assert client.post("/api/v1/auth/refresh").status_code == 200

    # Replay the pre-rotation token.
    client.cookies.set(settings.refresh_cookie_name, stolen)
    replay = client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "REFRESH_REUSED"

    live = db_session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all()
    assert not live, "the token family survived a detected replay"


def test_refresh_without_a_cookie_is_unauthenticated(client: TestClient) -> None:
    client.cookies.clear()
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


# ── logout ───────────────────────────────────────────────────────────────────


def test_logout_is_idempotent(client: TestClient) -> None:
    """Erroring here would strand a client that already lost its cookie."""
    register(client, unique_email())
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.clear()
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_logout_prevents_further_refresh(client: TestClient) -> None:
    register(client, unique_email())
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/refresh").status_code == 401


# ── me ───────────────────────────────────────────────────────────────────────


def test_me_returns_permissions_not_just_a_role(client: TestClient) -> None:
    """The frontend gates affordances on the permission list; roles may change
    shape, the permission strings are the stable contract."""
    payload = register(client, unique_email(), org="Permission Co")
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["org_name"] == "Permission Co"
    assert "calibration:run" in body["permissions"]
    assert "insights:read" in body["permissions"]


def test_me_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_rejects_a_tampered_token(client: TestClient) -> None:
    payload = register(client, unique_email())
    tampered = payload["access_token"][:-4] + "AAAA"
    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert response.status_code == 401
