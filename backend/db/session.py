"""Engine and session management.

Sync SQLAlchemy, deliberately (docs/03-BACKEND-PLAN.md §1.1). Route handlers
are `def`, not `async def`, so FastAPI runs them in its threadpool — which is
what keeps a 400 ms torch forward pass from stalling the event loop. Async
SQLAlchemy would buy nothing here and would add greenlet-context bugs at
exactly the hours when debugging plumbing is least affordable.

Two ways in:

- `get_db()` — the FastAPI dependency. One session per request, rolled back on
  exception, always closed.
- `db_session()` — a context manager for background workers, scripts, and the
  websocket's `asyncio.to_thread` calls, which have no request to hang off.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)


def build_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # The compose stack restarts Postgres freely and a stale pooled
        # connection surfaces as a confusing OperationalError mid-demo.
        pool_pre_ping=True,
        pool_recycle=1_800,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_session_params(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
        # Belt and braces alongside connect_args: a runaway eval aggregation
        # should die rather than hold a connection until the demo ends.
        with dbapi_conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {settings.db_statement_timeout_ms}")

    return engine


engine: Engine = build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # response serialization happens after commit
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. One session per request.

    Commit is the handler's job — an implicit commit here would persist partial
    work from a handler that raised halfway through.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def db_session() -> Iterator[Session]:
    """Session for code outside the request cycle. Commits on clean exit."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database() -> bool:
    """Cheap liveness probe for /health. Never raises."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - health must not propagate
        log.warning("database_unreachable", error=str(exc))
        return False
