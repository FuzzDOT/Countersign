"""The tripwire that makes contract-first scaffolding safe.

Stage 1 publishes every endpoint's signature with a `pending=True` body so the
frontend can generate types and build against fixtures from hour 3. That is
only defensible if a forgotten stub cannot survive to the demo. This file is
the mechanism: bump `BUILD_STAGE` when a stage lands, and every route that
stage owns must be implemented or the suite goes red.

Without this test, "I'll fill that in later" is a promise. With it, it is a
build failure.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from api.mock import BUILD_STAGE, Contract, contract_of

# Paths the app serves that are not part of the versioned contract.
EXEMPT_PATHS = frozenset(
    {"/health", "/health/ready", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
)

MAX_STAGE = 10

# Stages that deliberately own no HTTP route. Stage 3 is the relation model
# and the evidential head: it changes what an insight *contains*, and the
# endpoints that serve insights belong to stage 4. Listing it here keeps
# `test_landed_stage_has_routes` a real tripwire for an unwired router
# instead of something that has to be switched off.
ROUTELESS_STAGES = frozenset({3})


def api_routes(app: FastAPI) -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path not in EXEMPT_PATHS
    ]


def contracts(app: FastAPI) -> list[tuple[str, str, Contract]]:
    found: list[tuple[str, str, Contract]] = []
    for route in api_routes(app):
        meta = contract_of(route.endpoint)
        method = sorted(route.methods or {"GET"})[0]
        assert meta is not None, (
            f"{method} {route.path} has no @contract(...). Every route must declare the "
            "build stage that owns it, or this test cannot tell whether it is finished."
        )
        found.append((method, route.path, meta))
    return found


def test_every_route_declares_a_contract(app: FastAPI) -> None:
    assert contracts(app), "no API routes found — the router wiring is broken"


def test_no_landed_stage_is_still_pending(app: FastAPI) -> None:
    """The actual tripwire."""
    outstanding = [
        f"{method} {path} (stage {meta.stage})"
        for method, path, meta in contracts(app)
        if meta.pending and meta.stage <= BUILD_STAGE
    ]
    assert not outstanding, (
        f"BUILD_STAGE is {BUILD_STAGE}, so these routes should be implemented but are "
        "still raising NotImplementedYet:\n  " + "\n  ".join(outstanding)
    )


def test_stage_numbers_are_plausible(app: FastAPI) -> None:
    for method, path, meta in contracts(app):
        assert 0 <= meta.stage <= MAX_STAGE, f"{method} {path} claims stage {meta.stage}"


def test_implemented_routes_are_not_marked_pending(app: FastAPI) -> None:
    """Catches the opposite mistake: a stage flag bumped past its work.

    A route whose stage is above BUILD_STAGE but which is *not* pending is
    fine (it landed early). A route at or below BUILD_STAGE that is pending is
    caught above. This checks the flag is at least internally coherent.
    """
    for method, path, meta in contracts(app):
        if not meta.pending:
            assert meta.stage <= MAX_STAGE, f"{method} {path}"


@pytest.mark.parametrize("stage", range(1, BUILD_STAGE + 1))
def test_landed_stage_has_routes(app: FastAPI, stage: int) -> None:
    """A landed stage that owns no routes usually means a router was not wired
    into api/v1/__init__.py — which fails silently otherwise."""
    if stage in ROUTELESS_STAGES:
        pytest.skip(f"stage {stage} owns no HTTP route by design")
    owned = [path for _, path, meta in contracts(app) if meta.stage == stage]
    assert owned, f"stage {stage} is marked landed but owns no routes"


def test_fixture_backed_routes_name_a_real_file(app: FastAPI) -> None:
    """Every declared fixture must exist on disk.

    Mock mode is the frontend's entire development environment until Stage 4.
    A route whose fixture is missing fails at request time with a 500, which
    would read to a frontend dev as "the backend is broken".
    """
    from api.mock import fixture_path
    from core.config import get_settings

    settings = get_settings()
    missing = [
        f"{method} {path} -> {meta.fixture}"
        for method, path, meta in contracts(app)
        if meta.fixture and not fixture_path(meta.fixture, settings).exists()
    ]
    assert not missing, (
        "fixtures referenced by routes are missing from data/fixtures. "
        "Run `make fixtures`:\n  " + "\n  ".join(missing)
    )


# ── response_model hygiene ───────────────────────────────────────────────────


def test_no_route_has_a_string_response_model(app: FastAPI) -> None:
    """Catches annotation inference leaking a forward-reference string.

    Every router module uses `from __future__ import annotations`, so return
    annotations are strings at runtime. When a route omits `response_model`,
    FastAPI infers it from that annotation and resolves it against
    `endpoint.__globals__` — which is api/mock.py's namespace, because
    @contract wraps the handler and `functools.wraps` cannot copy
    `__globals__`. The string never resolves, and FastAPI ends up with a
    truthy `str` as the response model.

    That fails at *import* time, which takes the whole application down rather
    than one endpoint, so it is worth a dedicated assertion.
    """
    offenders = [
        f"{sorted(route.methods or {'GET'})[0]} {route.path} -> {route.response_model!r}"
        for route in api_routes(app)
        if isinstance(route.response_model, str)
    ]
    assert not offenders, (
        "these routes have a string response_model, which means annotation "
        "inference misfired. Pass `response_model=` explicitly:\n  " + "\n  ".join(offenders)
    )


def test_every_route_declares_response_model_explicitly(app: FastAPI) -> None:
    """Belt and braces: never rely on inference in this codebase.

    A route with no body (204) must say `response_model=None`; everything else
    names its model. Relying on inference works until a decorator is added to
    the handler, at which point it breaks at import.
    """
    import inspect

    from api.v1 import (
        ablation,
        auth,
        calibration,
        documents,
        evals,
        graph,
        ingest,
        insights,
        routing,
        voice,
    )

    modules = (
        ablation,
        auth,
        calibration,
        documents,
        evals,
        graph,
        ingest,
        insights,
        routing,
        voice,
    )
    missing: list[str] = []
    for module in modules:
        source = inspect.getsource(module)
        # Each decorator block opens with `@router.<verb>(`; every one of them
        # must mention response_model before its closing paren.
        for block in source.split("@router.")[1:]:
            header = block.split(")\n@")[0]
            verb = header.split("(")[0]
            if verb not in ("get", "post", "put", "patch", "delete"):
                continue
            if "response_model" not in header:
                snippet = header.split("\n")[1].strip() if "\n" in header else header
                missing.append(f"{module.__name__}: @router.{verb}({snippet}")
    assert not missing, (
        "these route decorators do not pass response_model explicitly:\n  " + "\n  ".join(missing)
    )


def test_no_body_routes_declare_no_response_model(app: FastAPI) -> None:
    """A 204 with a declared body model is an import-time assertion in FastAPI."""
    for route in api_routes(app):
        if route.status_code == 204:
            assert (
                route.response_model is None
            ), f"{route.path} returns 204 but declares a response model"


# ── dependency resolution ────────────────────────────────────────────────────

# Parameter names that are always dependencies or request bodies in this
# codebase. If FastAPI ever exposes one of these as a query parameter, its
# annotation failed to resolve and the dependency silently stopped working.
DEPENDENCY_PARAM_NAMES = frozenset(
    {"db", "settings", "scope", "user", "principal", "pagination", "payload", "files"}
)


def test_no_route_endpoint_has_unresolved_string_annotations(app: FastAPI) -> None:
    """The general form of the bug that broke every authenticated endpoint.

    Routers use `from __future__ import annotations`, so annotations are
    strings until something resolves them. FastAPI resolves them against
    `endpoint.__globals__` — which, for a handler wrapped by a decorator
    defined in another module, is the *decorator's* module. It does not raise
    on failure; it quietly demotes the parameter to an untyped required field,
    so `db: DbDep` became a mandatory query parameter and every request
    returned `422 {"db": "Field required"}`.

    `api/mock.py` fixes this by setting `__signature__` to a pre-resolved
    signature. This asserts it stays fixed: a non-string annotation is proof
    that resolution already happened.
    """
    offenders: list[str] = []
    for route in api_routes(app):
        import inspect

        signature = inspect.signature(route.endpoint)
        unresolved = [
            name
            for name, param in signature.parameters.items()
            if isinstance(param.annotation, str)
        ]
        if unresolved:
            method = sorted(route.methods or {"GET"})[0]
            offenders.append(f"{method} {route.path}: {', '.join(unresolved)}")

    assert not offenders, (
        "these route handlers expose unresolved string annotations, so FastAPI "
        "will treat their dependencies as plain query parameters:\n  " + "\n  ".join(offenders)
    )


def test_dependencies_are_not_exposed_as_query_parameters(app: FastAPI) -> None:
    """The symptom, asserted directly.

    Checks the shape FastAPI actually built rather than the annotations it
    started from, so it catches the failure even if the mechanism changes.
    """
    offenders: list[str] = []
    for route in api_routes(app):
        query_names = {param.name for param in route.dependant.query_params}
        leaked = query_names & DEPENDENCY_PARAM_NAMES
        if leaked:
            method = sorted(route.methods or {"GET"})[0]
            offenders.append(f"{method} {route.path}: {', '.join(sorted(leaked))}")

    assert not offenders, (
        "these routes expose a dependency or request body as a query parameter, "
        "which means the dependency is not being injected:\n  " + "\n  ".join(offenders)
    )


def test_authenticated_routes_actually_have_dependencies(app: FastAPI) -> None:
    """A route that lost its dependencies would otherwise look fine.

    If annotation resolution breaks, `dependant.dependencies` comes back empty
    and the endpoint becomes unauthenticated while still returning 422s that
    look like client errors. Every route outside /auth must carry at least one.
    """
    naked = [
        f"{sorted(route.methods or {'GET'})[0]} {route.path}"
        for route in api_routes(app)
        if not route.path.startswith("/api/v1/auth/") and not route.dependant.dependencies
    ]
    assert not naked, (
        "these routes have no dependencies at all — auth and scoping are not "
        "being applied:\n  " + "\n  ".join(naked)
    )
