"""Mock mode and stage ownership.

One decorator does two jobs, because they are the same job: declaring which
build stage owns a handler, and declaring which fixture stands in for it until
that stage lands.

    @router.get("/insights", response_model=Paginated[InsightOut])
    @contract("insights.list.json", stage=4)
    def list_insights(...): ...

With `MOCK_MODE=1` the fixture is returned after a randomized delay, so the
frontend builds real loading states from its first commit rather than
retrofitting them (frontend brief §4.1). With `MOCK_MODE=0` the real handler
runs. Either way FastAPI validates the result against `response_model`, which
is what makes "a fixture cannot disagree with the real response" structural
rather than aspirational — a stale fixture fails validation loudly instead of
lying to the frontend.

`pending=True` marks a handler whose body is not written yet.
tests/test_no_stubs.py fails the build if any route owned by a stage at or
below `BUILD_STAGE` is still pending, so a forgotten stub cannot survive to the
demo. That test is the entire reason stubs are acceptable here at all.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from api.errors import (
    AppError,
    ErrorCode,
    Forbidden,
    NemotronUnavailable,
    RateLimited,
    TokenExpired,
    ValidationFailed,
)
from core.config import Settings, get_settings
from core.logging import get_logger

log = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

# The highest build stage whose handlers are expected to be implemented.
# tests/test_no_stubs.py fails if any route owned by a stage at or below this
# number is still `pending=True`. Bump it as each stage lands — it is the one
# line that turns "I think stage 4 is done" into something the suite checks.
#
# Deliberately a source constant rather than a setting. As an environment
# variable it would be a tripwire that deployment config could switch off, and
# the failure mode — a green suite that has quietly stopped checking anything —
# is exactly what this is here to prevent.
BUILD_STAGE = 4


@dataclass(frozen=True, slots=True)
class Contract:
    """Metadata attached to every route handler.

    Read by tests/test_no_stubs.py and by scripts/gen_fixtures.py, which uses
    the fixture names declared here as its worklist — so adding a route with a
    fixture that nobody generates is caught by the fixture test.
    """

    fixture: str | None
    stage: int
    pending: bool

    @property
    def description_suffix(self) -> str:
        if self.pending:
            return f"\n\n*Implemented in build stage {self.stage}.*"
        return ""


class NotImplementedYet(AppError):
    """Raised by a handler whose stage has not landed.

    501 rather than 500: it is not a crash, it is an honest statement that the
    route exists in the contract and not yet in the implementation. The
    frontend never sees this — it runs against MOCK_MODE until the stage lands.
    """

    code = ErrorCode.INTERNAL_ERROR

    def __init__(self, stage: int) -> None:
        super().__init__(
            f"This endpoint lands in build stage {stage}. "
            "Run the server with MOCK_MODE=1 to develop against its fixture.",
            status=501,
            details={"stage": stage, "hint": "MOCK_MODE=1"},
        )


# ── fixtures ─────────────────────────────────────────────────────────────────

_FIXTURE_CACHE: dict[Path, tuple[float, Any]] = {}


def fixture_path(name: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    # `name` comes only from decorator literals in this codebase, never from a
    # request, but it is still constrained: a path separator here would be a
    # traversal out of the fixtures directory.
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"fixture name must be a bare filename, got {name!r}")
    return settings.path(settings.fixtures_dir) / name


def load_fixture(name: str, settings: Settings | None = None) -> Any:
    """Load a fixture, re-reading it when the file changes on disk.

    The mtime check matters in practice: `make fixtures` regenerates them while
    the frontend has a dev server running against `--reload`, and a cached
    payload would silently serve the old contract.
    """
    path = fixture_path(name, settings)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        raise FileNotFoundError(
            f"fixture {name!r} is missing. Run `python -m scripts.gen_fixtures` "
            f"(or `make fixtures`) to regenerate {path.parent}."
        ) from None

    cached = _FIXTURE_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    payload = json.loads(path.read_text(encoding="utf-8"))
    _FIXTURE_CACHE[path] = (mtime, payload)
    return payload


def clear_fixture_cache() -> None:
    _FIXTURE_CACHE.clear()


# ── simulated failure ────────────────────────────────────────────────────────

# One factory per file in data/fixtures/errors/. These are raised through the
# real exception handlers rather than returned as canned JSON, so the injected
# error is byte-identical to a genuine one.
ERROR_INJECTORS: tuple[Callable[[], AppError], ...] = (
    lambda: TokenExpired(),
    lambda: Forbidden(details={"required": ["calibration:run"], "missing": ["calibration:run"]}),
    lambda: ValidationFailed(details={"fields": {"scenario": "not a known scenario"}}),
    lambda: RateLimited(retry_after_seconds=17),
    lambda: NemotronUnavailable(),
)


def _simulated_latency(settings: Settings) -> float:
    return random.uniform(  # noqa: S311 - demo jitter, not cryptography
        settings.mock_latency_min_ms / 1000.0,
        settings.mock_latency_max_ms / 1000.0,
    )


def _maybe_fail(settings: Settings) -> None:
    if settings.mock_error_rate <= 0:
        return
    if random.random() < settings.mock_error_rate:  # noqa: S311
        raise random.choice(ERROR_INJECTORS)()  # noqa: S311


# ── the decorator ────────────────────────────────────────────────────────────


def contract(
    fixture: str | None = None,
    *,
    stage: int,
    pending: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Declare stage ownership and wire mock-mode fixture serving.

    `functools.wraps` sets `__wrapped__`, which `inspect.signature` follows, so
    FastAPI still sees the real handler signature — dependencies, query params
    and response models all behave normally through the wrapper.
    """
    meta = Contract(fixture=fixture, stage=stage, pending=pending)

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                settings = get_settings()
                if settings.mock_mode and fixture is not None:
                    await asyncio.sleep(_simulated_latency(settings))
                    _maybe_fail(settings)
                    return load_fixture(fixture, settings)
                return await fn(*args, **kwargs)

            wrapper: Callable[..., Any] = async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                settings = get_settings()
                if settings.mock_mode and fixture is not None:
                    # Sync handlers run in FastAPI's threadpool, so sleeping
                    # here does not block the event loop.
                    time.sleep(_simulated_latency(settings))
                    _maybe_fail(settings)
                    return load_fixture(fixture, settings)
                return fn(*args, **kwargs)

            wrapper = sync_wrapper

        # THE important line in this module.
        #
        # Every router uses `from __future__ import annotations`, so parameter
        # annotations are strings. FastAPI resolves them against
        # `endpoint.__globals__` — and for this wrapper that is *api/mock.py's*
        # namespace, not the router's, because `functools.wraps` cannot copy
        # `__globals__` (it is read-only on function objects). `DbDep`,
        # `ScopeDep` and the request models are not defined here.
        #
        # FastAPI does not raise on an unresolvable annotation; it silently
        # demotes the parameter to an untyped required field. So every
        # dependency turned into a mandatory query parameter and every request
        # became `422 {"db": "Field required", ...}` — a failure that looks
        # nothing like its cause.
        #
        # `eval_str=True` resolves the annotations using the *original*
        # function's globals, and a non-string annotation makes FastAPI skip
        # ForwardRef evaluation entirely. No try/except: if a router grows an
        # annotation that cannot be resolved, that must fail at import, loudly,
        # rather than degrade into a 422 at request time.
        wrapper.__signature__ = inspect.signature(fn, eval_str=True)  # type: ignore[attr-defined]
        wrapper.__cs_contract__ = meta  # type: ignore[attr-defined]
        return wrapper

    return decorate


def contract_of(endpoint: Callable[..., Any]) -> Contract | None:
    return getattr(endpoint, "__cs_contract__", None)


# Fixtures served by something other than a decorated HTTP handler — the job
# progress websocket streams four of them and cannot go through `@contract`,
# because it pushes a sequence of frames rather than returning one payload.
# Registered here so `declared_fixtures` stays a complete answer to "what does
# this app serve", which is what the generator's coverage check depends on.
_STREAMED_FIXTURES: set[str] = set()


def register_streamed_fixtures(*names: str) -> None:
    _STREAMED_FIXTURES.update(names)


def declared_fixtures(app: Any) -> set[str]:
    """Every fixture name this app can serve. The generator's worklist.

    Covers decorated handlers plus anything registered via
    `register_streamed_fixtures`. `scripts/gen_fixtures.py` asserts this set
    matches what the builder produces in both directions, so a fixture nobody
    serves and a route whose fixture nobody builds are both build failures.
    """
    names: set[str] = set(_STREAMED_FIXTURES)
    for route in getattr(app, "routes", []):
        endpoint = getattr(route, "endpoint", None)
        meta = contract_of(endpoint) if endpoint else None
        if meta and meta.fixture:
            names.add(meta.fixture)
    return names


async def mock_delay() -> None:
    """Latency for endpoints that stream rather than return a payload (the
    job websocket), which cannot go through the decorator."""
    settings = get_settings()
    if settings.mock_mode:
        await asyncio.sleep(_simulated_latency(settings))


AsyncHandler = Callable[..., Awaitable[Any]]
