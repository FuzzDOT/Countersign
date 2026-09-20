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
# FORCED, not setdefault. `setdefault` defers to whatever is already in the
# environment, and the container inherits the developer's .env — so a local
# `MOCK_MODE=1` silently put the entire suite into fixture-serving mode. Every
# integration test then asserted against canned data instead of the database
# and failed in ways that looked like application bugs.
#
# MOCK_ERROR_RATE is worse: a local 0.1 injects random failures into 10% of
# mock-mode requests, which makes the suite *intermittently* red. Tests that
# want mock mode opt in through the `mock_settings` fixture.
os.environ["MOCK_MODE"] = "0"
os.environ["MOCK_ERROR_RATE"] = "0.0"
os.environ.setdefault("COMMON_PASSWORDS_PATH", "data/common_passwords.txt")
# Keep argon2 at production cost in tests: the timing-padding and lockout tests
# are about real behaviour, and lowering the cost would hide a regression in
# the enumeration defense.

# ── interpreter preflight ────────────────────────────────────────────────────
# Runs before the application imports below, so a wrong-interpreter run fails
# with a sentence instead of an ImportError several frames deep in api.deps.
#
# The trigger is running `pytest` on a system or Anaconda Python instead of
# inside the container. Versions there are whatever happens to be installed,
# not what requirements.txt pins, and the failure modes are obscure —
# pydantic v1 in particular breaks in ways that look like application bugs.


def _preflight() -> None:
    import importlib

    # (module, attribute that only exists at the version we need, why)
    probes = (
        ("pydantic", "VERSION", 2, "pydantic v2 is required; v1 fails in confusing ways"),
        ("sqlalchemy", "__version__", 2, "SQLAlchemy 2.x typed ORM syntax is used throughout"),
    )
    problems: list[str] = []
    for name, attr, major, why in probes:
        try:
            module = importlib.import_module(name)
        except ImportError:
            problems.append(f"{name} is not installed")
            continue
        version = str(getattr(module, attr, "0"))
        if not version.split(".")[0].isdigit() or int(version.split(".")[0]) < major:
            problems.append(f"{name} {version} is too old — need >={major}.x ({why})")

    if problems:
        raise RuntimeError(
            "The test suite is running against an interpreter that does not match "
            "backend/requirements.txt:\n  - "
            + "\n  - ".join(problems)
            + f"\n\nInterpreter: {sys.executable}\n\n"
            "Run the tests inside the container instead:\n"
            "    make test-be\n"
            "or, for a host run, create a venv from the pinned requirements:\n"
            "    python -m venv .venv && . .venv/bin/activate\n"
            "    pip install -r requirements.txt -r requirements-dev.txt"
        )


import sys  # noqa: E402

_preflight()

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.deps import permissions_for  # noqa: E402
from core.config import Settings, get_settings  # noqa: E402
from core.security import mint_access_token  # noqa: E402
from db.models import UserRole  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """Empty the limiter's buckets around every test.

    `core.ratelimit.limiter` is a module-level singleton — slowapi's decorators
    run at import time on the router modules, so a fresh `app` fixture does not
    get a fresh limiter. Without this, the buckets accumulate across the whole
    session and the tightest limits are spent long before the tests that need
    them: register is 5/hour, so the sixth test that registers a user gets a
    429 instead of a 201 and fails for a reason that has nothing to do with
    what it asserts.

    Resetting rather than disabling keeps a test free to exhaust a limit on
    purpose and assert the 429.
    """
    from core.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()


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


# ── integration tenants ──────────────────────────────────────────────────────


@pytest.fixture
def tenant(db_session):  # type: ignore[no-untyped-def]
    """A throwaway organization with an owner, cleaned up afterwards.

    Random ids rather than the demo tenant's deterministic ones: the seeded
    demo org is shared state that `make seed` also writes to, and a test that
    deleted its documents would quietly break the next manual demo run.
    """
    import uuid as _uuid

    from sqlalchemy import delete

    from db.models import (
        Document,
        Entity,
        IngestJob,
        Insight,
        Organization,
        RefreshToken,
        User,
        UserRole,
    )

    org_id = _uuid.uuid4()
    user_id = _uuid.uuid4()
    db_session.add(Organization(id=org_id, name=f"Test Org {org_id.hex[:8]}"))
    db_session.flush()
    db_session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"owner-{user_id.hex[:12]}@example.com",
            password_hash="x",
            role=UserRole.owner,
        )
    )
    db_session.commit()

    yield {"org_id": org_id, "user_id": user_id}

    # Ordered by dependency rather than trusting CASCADE, so a failure here
    # names the table that would not drop.
    for model in (Insight, Entity, Document, IngestJob, RefreshToken):
        column = model.org_id if hasattr(model, "org_id") else None
        if column is not None:
            db_session.execute(delete(model).where(column == org_id))
    db_session.execute(delete(User).where(User.org_id == org_id))
    db_session.execute(delete(Organization).where(Organization.id == org_id))
    db_session.commit()


@pytest.fixture
def tenant_header(tenant, auth_header):  # type: ignore[no-untyped-def]
    return auth_header(user_id=tenant["user_id"], org=tenant["org_id"], role=UserRole.owner)
