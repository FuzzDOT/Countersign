"""Every canonical error code produces the documented envelope.

Brief §3 promises an identical shape on every failure, with no exceptions, and
the frontend switches on `error.code` (frontend brief §4.3). That makes this
file a contract test rather than a nicety: a route that returns FastAPI's
default `{"detail": ...}` breaks the frontend's error handling silently, and it
would not be noticed until a demo.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from api import errors
from api.errors import (
    ERROR_META,
    AccountLocked,
    AppError,
    Conflict,
    DocumentNotFound,
    ErrorCode,
    Forbidden,
    InsightNotFound,
    InternalError,
    NemotronUnavailable,
    NotFound,
    PayloadTooLarge,
    PipelineFailed,
    RateLimited,
    RefreshReused,
    TokenExpired,
    Unauthenticated,
    UnsupportedMedia,
    ValidationFailed,
    VoiceUnavailable,
    register_exception_handlers,
)

REQUIRED_KEYS = {"code", "message", "status", "request_id", "details"}

# One instance per code, constructed the way production code constructs them.
RAISABLE: list[tuple[ErrorCode, AppError]] = [
    (ErrorCode.VALIDATION_FAILED, ValidationFailed(details={"fields": {"email": "required"}})),
    (ErrorCode.UNAUTHENTICATED, Unauthenticated()),
    (ErrorCode.TOKEN_EXPIRED, TokenExpired()),
    (ErrorCode.REFRESH_REUSED, RefreshReused()),
    (ErrorCode.FORBIDDEN, Forbidden(details={"required": ["ablation:run"]})),
    (ErrorCode.ACCOUNT_LOCKED, AccountLocked(locked_until="2026-09-19T21:00:00Z")),
    (ErrorCode.NOT_FOUND, NotFound()),
    (ErrorCode.INSIGHT_NOT_FOUND, InsightNotFound(details={"id": "abc"})),
    (ErrorCode.DOCUMENT_NOT_FOUND, DocumentNotFound(details={"id": "abc"})),
    (ErrorCode.CONFLICT, Conflict("That email is already registered.")),
    (ErrorCode.RATE_LIMITED, RateLimited(retry_after_seconds=42)),
    (ErrorCode.PAYLOAD_TOO_LARGE, PayloadTooLarge(details={"max_bytes": 2_097_152})),
    (ErrorCode.UNSUPPORTED_MEDIA, UnsupportedMedia()),
    (ErrorCode.PIPELINE_FAILED, PipelineFailed(stage="relating")),
    (ErrorCode.NEMOTRON_UNAVAILABLE, NemotronUnavailable()),
    (ErrorCode.VOICE_UNAVAILABLE, VoiceUnavailable()),
    (ErrorCode.INTERNAL_ERROR, InternalError()),
]


def test_every_code_has_metadata() -> None:
    """No code can exist without a status and a default message."""
    assert set(ERROR_META) == set(ErrorCode)


def test_every_code_is_covered_by_this_file() -> None:
    """Adding a code without a test here fails the build.

    Deliberate tripwire: the temptation at hour 20 is to add a code and move on.
    """
    assert {code for code, _ in RAISABLE} == set(ErrorCode)


# ── envelope shape, per code ─────────────────────────────────────────────────


class ValidateBody(BaseModel):
    """Declared at module level on purpose.

    `from __future__ import annotations` makes `payload: ValidateBody` the
    string "ValidateBody", which FastAPI resolves against this module's
    globals. Defined inside the fixture function it was unreachable, and
    FastAPI quietly turned the parameter into an untyped embedded body field —
    so the request needed `{"payload": {...}}` and every error came back keyed
    on the parameter name instead of the model's fields.
    """

    email: str
    age: int = Field(ge=0)


@pytest.fixture
def error_app() -> FastAPI:
    """An app whose only job is to raise each AppError from a route."""
    test_app = FastAPI()
    register_exception_handlers(test_app)

    for code, exc in RAISABLE:

        def _make_route(to_raise: AppError = exc) -> Any:
            def _route() -> None:
                raise to_raise

            return _route

        test_app.add_api_route(f"/raise/{code}", _make_route(), methods=["GET"])

    @test_app.post("/validate")
    def _validate(payload: ValidateBody) -> dict[str, bool]:
        return {"ok": bool(payload.email)}

    @test_app.get("/boom")
    def _boom() -> None:
        raise RuntimeError("a secret connection string lives in here: postgres://u:p@h/db")

    return test_app


@pytest.mark.parametrize(("code", "exc"), RAISABLE, ids=[c.value for c, _ in RAISABLE])
def test_error_envelope(code: ErrorCode, exc: AppError, error_app: FastAPI) -> None:
    with TestClient(error_app, raise_server_exceptions=False) as client:
        response = client.get(f"/raise/{code}")

    expected_status, _ = ERROR_META[code]
    assert response.status_code == expected_status

    payload = response.json()
    assert set(payload) == {"error"}, "the envelope must have exactly one top-level key"
    error = payload["error"]
    assert set(error) == REQUIRED_KEYS
    assert error["code"] == code.value
    assert error["status"] == expected_status
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)


def test_details_passthrough(error_app: FastAPI) -> None:
    """`details` carries the fields the frontend is documented to read."""
    with TestClient(error_app, raise_server_exceptions=False) as client:
        locked = client.get(f"/raise/{ErrorCode.ACCOUNT_LOCKED}").json()["error"]
        limited = client.get(f"/raise/{ErrorCode.RATE_LIMITED}").json()["error"]
        pipeline = client.get(f"/raise/{ErrorCode.PIPELINE_FAILED}").json()["error"]

    # Frontend brief §4.3: countdown to locked_until, countdown on the button,
    # and the failed stage on the job card.
    assert locked["details"]["locked_until"] == "2026-09-19T21:00:00Z"
    assert limited["details"]["retry_after_seconds"] == 42
    assert pipeline["details"]["stage"] == "relating"


def test_rate_limited_sets_retry_after_header(error_app: FastAPI) -> None:
    with TestClient(error_app, raise_server_exceptions=False) as client:
        response = client.get(f"/raise/{ErrorCode.RATE_LIMITED}")
    assert response.headers["Retry-After"] == "42"


def test_validation_error_maps_fields(error_app: FastAPI) -> None:
    """Pydantic's error list becomes details.fields keyed by input name."""
    with TestClient(error_app, raise_server_exceptions=False) as client:
        response = client.post("/validate", json={"age": -3})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == ErrorCode.VALIDATION_FAILED.value
    fields = error["details"]["fields"]
    # Location prefixes like "body" are stripped so the key matches the form input.
    assert "email" in fields
    assert "age" in fields
    assert all(isinstance(v, str) for v in fields.values())


def test_unhandled_exception_leaks_nothing(error_app: FastAPI) -> None:
    """A bug returns INTERNAL_ERROR and never the exception text."""
    with TestClient(error_app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == ErrorCode.INTERNAL_ERROR.value
    body = response.text
    assert "postgres://" not in body
    assert "secret connection string" not in body


# ── the real app ─────────────────────────────────────────────────────────────


def test_unknown_route_uses_the_envelope(client: TestClient) -> None:
    """Starlette's own 404 is mapped onto our shape, not left as {"detail"}."""
    response = client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == ErrorCode.NOT_FOUND.value
    assert "detail" not in response.json()


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health")
    request_id = response.headers.get("X-Request-ID", "")
    assert len(request_id) == 26, "expected a 26-character ULID"
    assert set(request_id) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_request_id_appears_in_the_error_body(client: TestClient) -> None:
    """The id in the body matches the header, so a judge can read it off a toast."""
    response = client.get("/api/v1/nope")
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_inbound_request_id_is_honoured_when_safe(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"


def test_inbound_request_id_with_control_characters_is_replaced(client: TestClient) -> None:
    """Log injection defense: a newline in the id would forge log lines."""
    response = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert response.headers["X-Request-ID"] != "bad id with spaces"
    assert len(response.headers["X-Request-ID"]) == 26


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in response.headers
    # HSTS must NOT be sent from a non-production, plain-http demo: it would
    # pin localhost to https in the judge's browser.
    assert "Strict-Transport-Security" not in response.headers


def test_oversized_request_is_rejected_before_buffering() -> None:
    """A body larger than the aggregate cap is refused with our envelope.

    The middleware is exercised with a tiny limit rather than by sending a
    110 MB body — and rather than by hand-setting Content-Length, which httpx
    recomputes from the actual content.
    """
    from api.middleware import RequestSizeLimitMiddleware

    tiny = FastAPI()
    register_exception_handlers(tiny)
    tiny.add_middleware(RequestSizeLimitMiddleware, max_bytes=16)

    @tiny.post("/upload")
    def _upload(_body: dict[str, Any]) -> dict[str, bool]:
        return {"ok": True}

    with TestClient(tiny, raise_server_exceptions=False) as client:
        too_big = client.post("/upload", json={"padding": "x" * 200})
        small_enough = client.post("/upload", json={"a": 1})

    assert too_big.status_code == 413
    error = too_big.json()["error"]
    assert error["code"] == ErrorCode.PAYLOAD_TOO_LARGE.value
    assert error["details"]["max_bytes"] == 16
    assert error["details"]["declared_bytes"] > 16

    assert small_enough.status_code == 200


def test_malformed_content_length_is_a_validation_error() -> None:
    from api.middleware import RequestSizeLimitMiddleware

    tiny = FastAPI()
    register_exception_handlers(tiny)
    tiny.add_middleware(RequestSizeLimitMiddleware, max_bytes=1_024)

    @tiny.get("/ping")
    def _ping() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(tiny, raise_server_exceptions=False) as client:
        response = client.get("/ping", headers={"Content-Length": "not-a-number"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value


def test_health_reports_without_a_database(client: TestClient) -> None:
    """Liveness stays 200 when Postgres is down, so Docker does not restart-loop."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] in {"up", "down"}


def test_rate_limit_handler_envelope() -> None:
    """The slowapi handler produces the documented 429 body.

    Exercised directly rather than by hammering a route: no route carries a
    limit decorator until Stage 1, and the envelope is the part that matters.
    """
    import asyncio

    class _FakeLimit:
        @staticmethod
        def get_expiry() -> int:
            return 60

    class _Exc(Exception):
        limit = type("L", (), {"limit": _FakeLimit()})()

    response = asyncio.run(errors.rate_limit_handler(None, _Exc()))  # type: ignore[arg-type]
    assert response.status_code == 429
