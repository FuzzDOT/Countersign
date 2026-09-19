"""Shared pytest fixtures.

Environment is set at import time, before any application module is imported,
because `core.config.get_settings` is an `lru_cache` singleton and a module
that reads settings during import would otherwise capture the developer's real
`.env`.

Tests in this file's tree are split by marker:
  (unmarked)     pure unit, no database, no model weights — runs anywhere
  integration    needs a live Postgres (`make test-be` inside compose)
  ml             needs checkpoints in ml/checkpoints
  slow           fuzzer / training runs
"""

from __future__ import annotations

import os
import uuid

# ── must precede application imports ─────────────────────────────────────────
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("MOCK_MODE", "0")
os.environ.setdefault("MOCK_ERROR_RATE", "0.0")
os.environ.setdefault("COMMON_PASSWORDS_PATH", "data/common_passwords.txt")
# Keep argon2 at production cost in tests: the timing-padding and lockout tests
# are about real behaviour, and lowering the cost would hide a regression in
# the enumeration defense.

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.deps import permissions_for  # noqa: E402
from core.config import Settings, get_settings  # noqa: E402
from core.security import mint_access_token  # noqa: E402
from db.models import UserRole  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """A fresh app instance per test.

    Built through the factory rather than importing `api.main.app`, so a test
    that mutates app state cannot leak into the next one.
    """
    from api.main import create_app

    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # `with` runs the lifespan, which is where logging is configured and the
    # database reachability probe happens. The probe tolerates an absent DB.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-4000-8000-000000000001")


@pytest.fixture
def other_org_id() -> uuid.UUID:
    """A second tenant, for the cross-org isolation tests."""
    return uuid.UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture
def token_factory(settings: Settings):  # type: ignore[no-untyped-def]
    """Mint a valid access token for an arbitrary role and org."""

    def _make(
        *,
        user_id: uuid.UUID | None = None,
        org: uuid.UUID | None = None,
        role: UserRole = UserRole.analyst,
    ) -> str:
        token, _ = mint_access_token(
            user_id=user_id or uuid.uuid4(),
            org_id=org or uuid.UUID("00000000-0000-4000-8000-000000000001"),
            role=str(role),
            permissions=permissions_for(role),
            settings=settings,
        )
        return token

    return _make


@pytest.fixture
def auth_header(token_factory):  # type: ignore[no-untyped-def]
    def _make(**kw: object) -> dict[str, str]:
        return {"Authorization": f"Bearer {token_factory(**kw)}"}  # type: ignore[arg-type]

    return _make


# ── mock mode ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings with MOCK_MODE on.

    `get_settings` is an `lru_cache` singleton, so the cache is cleared on the
    way in and on the way out — otherwise a mock-mode Settings would leak into
    every subsequent test in the session and they would start serving fixtures.
    """
    monkeypatch.setenv("MOCK_MODE", "1")
    monkeypatch.setenv("MOCK_LATENCY_MIN_MS", "0")
    monkeypatch.setenv("MOCK_LATENCY_MAX_MS", "1")
    monkeypatch.setenv("MOCK_ERROR_RATE", "0.0")
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def mock_client(mock_settings: Settings) -> Iterator[TestClient]:
    from api.main import create_app

    from api.mock import clear_fixture_cache

    clear_fixture_cache()
    with TestClient(create_app(mock_settings), raise_server_exceptions=False) as test_client:
        yield test_client


# ── database (integration only) ──────────────────────────────────────────────


@pytest.fixture
def db_session() -> Iterator[object]:
    """A real session for tests that need to inspect rows directly.

    Only used by `integration`-marked tests. Deliberately NOT wrapped in a
    rolled-back transaction: the auth tests assert on state the request
    handlers committed, so an outer rollback would hide the thing under test.
    Rows accumulate in the test database, which is acceptable — `make nuke`
    resets it and the demo database is separate.
    """
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
