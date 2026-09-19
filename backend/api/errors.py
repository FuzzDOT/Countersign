"""Error envelope, canonical error codes, and the exception handlers.

Contract (01-BACKEND-BRIEF.md §3): every failure, without exception, returns

    {"error": {"code", "message", "status", "request_id", "details"}}

The frontend switches on `code` and never on `message` (02-FRONTEND-BRIEF.md
§4.3), which means `code` is a public API surface and renaming one is a
breaking change. `message` is for humans and may be reworded freely.

One code exists here that the brief does not list: `INTERNAL_ERROR`. An
unhandled exception has to return *something*, and returning a code the
frontend has never seen is better than returning a bare FastAPI
`{"detail": ...}` that breaks its error parser.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging import get_logger, get_request_id

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class ErrorCode(StrEnum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    REFRESH_REUSED = "REFRESH_REUSED"
    FORBIDDEN = "FORBIDDEN"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    NOT_FOUND = "NOT_FOUND"
    INSIGHT_NOT_FOUND = "INSIGHT_NOT_FOUND"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    PIPELINE_FAILED = "PIPELINE_FAILED"
    NEMOTRON_UNAVAILABLE = "NEMOTRON_UNAVAILABLE"
    VOICE_UNAVAILABLE = "VOICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# code -> (http status, default human message)
ERROR_META: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.VALIDATION_FAILED: (422, "The request body failed validation."),
    ErrorCode.UNAUTHENTICATED: (401, "Authentication is required."),
    ErrorCode.TOKEN_EXPIRED: (401, "The access token has expired."),
    ErrorCode.REFRESH_REUSED: (
        401,
        "This session was ended for security. Please sign in again.",
    ),
    ErrorCode.FORBIDDEN: (403, "You do not have permission to do that."),
    ErrorCode.ACCOUNT_LOCKED: (423, "Too many failed attempts. This account is locked."),
    ErrorCode.NOT_FOUND: (404, "Not found."),
    ErrorCode.INSIGHT_NOT_FOUND: (404, "No insight with that id in this organization."),
    ErrorCode.DOCUMENT_NOT_FOUND: (404, "No document with that id in this organization."),
    ErrorCode.CONFLICT: (409, "That resource already exists."),
    ErrorCode.RATE_LIMITED: (429, "Too many requests. Slow down."),
    ErrorCode.PAYLOAD_TOO_LARGE: (413, "The upload exceeds the size or count limit."),
    ErrorCode.UNSUPPORTED_MEDIA: (415, "That file type is not supported."),
    ErrorCode.PIPELINE_FAILED: (500, "The ingest pipeline failed."),
    ErrorCode.NEMOTRON_UNAVAILABLE: (503, "The Nemotron upstream is unavailable."),
    ErrorCode.VOICE_UNAVAILABLE: (503, "Voice synthesis is unavailable."),
    ErrorCode.INTERNAL_ERROR: (500, "Something went wrong on our side."),
}


# ── exception hierarchy ──────────────────────────────────────────────────────


class AppError(Exception):
    """Base class for every expected failure.

    Raise these from route handlers and services. Anything that escapes as a
    bare Exception is a bug, gets logged with a traceback, and returns
    INTERNAL_ERROR without leaking the message.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        status: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        default_status, default_message = ERROR_META[self.code]
        self.message = message or default_message
        self.status = status or default_status
        self.details: dict[str, Any] = details or {}
        self.headers = headers or {}
        super().__init__(self.message)

    def envelope(self) -> dict[str, Any]:
        return error_envelope(
            code=self.code,
            message=self.message,
            status=self.status,
            details=self.details,
        )


class ValidationFailed(AppError):
    code = ErrorCode.VALIDATION_FAILED


class Unauthenticated(AppError):
    code = ErrorCode.UNAUTHENTICATED


class TokenExpired(AppError):
    code = ErrorCode.TOKEN_EXPIRED


class RefreshReused(AppError):
    code = ErrorCode.REFRESH_REUSED


class Forbidden(AppError):
    code = ErrorCode.FORBIDDEN


class AccountLocked(AppError):
    code = ErrorCode.ACCOUNT_LOCKED

    def __init__(self, locked_until: str, **kw: Any) -> None:
        details = {"locked_until": locked_until, **(kw.pop("details", None) or {})}
        super().__init__(details=details, **kw)


class NotFound(AppError):
    code = ErrorCode.NOT_FOUND


class InsightNotFound(AppError):
    code = ErrorCode.INSIGHT_NOT_FOUND


class DocumentNotFound(AppError):
    code = ErrorCode.DOCUMENT_NOT_FOUND


class Conflict(AppError):
    code = ErrorCode.CONFLICT


class RateLimited(AppError):
    code = ErrorCode.RATE_LIMITED

    def __init__(self, retry_after_seconds: int, **kw: Any) -> None:
        details = {
            "retry_after_seconds": retry_after_seconds,
            **(kw.pop("details", None) or {}),
        }
        headers = {"Retry-After": str(retry_after_seconds), **(kw.pop("headers", None) or {})}
        super().__init__(details=details, headers=headers, **kw)


class PayloadTooLarge(AppError):
    code = ErrorCode.PAYLOAD_TOO_LARGE


class UnsupportedMedia(AppError):
    code = ErrorCode.UNSUPPORTED_MEDIA


class PipelineFailed(AppError):
    code = ErrorCode.PIPELINE_FAILED

    def __init__(self, stage: str, **kw: Any) -> None:
        details = {"stage": stage, **(kw.pop("details", None) or {})}
        super().__init__(details=details, **kw)


class NemotronUnavailable(AppError):
    code = ErrorCode.NEMOTRON_UNAVAILABLE


class VoiceUnavailable(AppError):
    code = ErrorCode.VOICE_UNAVAILABLE


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR


# ── envelope ─────────────────────────────────────────────────────────────────


def error_envelope(
    *,
    code: ErrorCode,
    message: str,
    status: int,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": str(code),
            "message": message,
            "status": status,
            "request_id": get_request_id() or "",
            "details": details or {},
        }
    }


def error_response(
    *,
    code: ErrorCode,
    message: str | None = None,
    status: int | None = None,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    default_status, default_message = ERROR_META[code]
    resolved_status = status or default_status
    payload = error_envelope(
        code=code,
        message=message or default_message,
        status=resolved_status,
        details=details,
    )
    merged = dict(headers or {})
    # Set explicitly rather than relying on middleware: the catch-all handler
    # for unhandled exceptions is installed on Starlette's ServerErrorMiddleware,
    # which sits *outside* our request-id middleware and so never passes back
    # through it.
    if rid := get_request_id():
        merged.setdefault(REQUEST_ID_HEADER, rid)
    return JSONResponse(status_code=resolved_status, content=payload, headers=merged)


# ── handlers ─────────────────────────────────────────────────────────────────

# Starlette raises bare HTTPExceptions from inside the framework (404 on an
# unmatched route, 405, 403 from some middleware). Map them onto our codes so
# the envelope is uniform even for failures we did not raise ourselves.
_HTTP_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA,
    422: ErrorCode.VALIDATION_FAILED,
    423: ErrorCode.ACCOUNT_LOCKED,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.NEMOTRON_UNAVAILABLE,
}


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    # 5xx is our fault and gets a stack; 4xx is the caller's and does not.
    if exc.status >= 500:
        log.error("app_error", code=str(exc.code), status=exc.status, exc_info=exc)
    else:
        log.info("app_error", code=str(exc.code), status=exc.status, details=exc.details)
    return error_response(
        code=exc.code,
        message=exc.message,
        status=exc.status,
        details=exc.details,
        headers=exc.headers,
    )


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Map Pydantic's error list to `details.fields` (field -> message).

    The frontend maps these onto specific form inputs, so the key has to be the
    field name the form knows about — hence dropping the leading `body`/`query`
    location segment.
    """
    assert isinstance(exc, RequestValidationError)
    fields: dict[str, str] = {}
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part not in ("body", "query", "path")]
        key = ".".join(loc) or "_root"
        # First error per field wins; a form shows one message per input.
        fields.setdefault(key, err.get("msg", "invalid value"))
    log.info("validation_failed", fields=sorted(fields))
    return error_response(
        code=ErrorCode.VALIDATION_FAILED,
        details={"fields": fields},
    )


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    detail = exc.detail if isinstance(exc.detail, str) else None
    headers = dict(getattr(exc, "headers", None) or {})
    return error_response(
        code=code,
        message=detail or ERROR_META[code][1],
        status=exc.status_code,
        headers=headers,
    )


async def rate_limit_handler(_request: Request, exc: Exception) -> JSONResponse:
    """slowapi's RateLimitExceeded -> our 429 envelope.

    slowapi describes the window in a string like "10 per 1 minute"; the
    frontend needs an integer to run a countdown on the button, so it is parsed
    into seconds here with a conservative fallback.
    """
    retry_after = 60
    limit = getattr(exc, "limit", None)
    window = getattr(getattr(limit, "limit", None), "get_expiry", None)
    if callable(window):
        try:
            retry_after = int(window())
        except Exception:  # noqa: BLE001 - never fail while reporting a failure
            retry_after = 60
    log.info("rate_limited", retry_after_seconds=retry_after)
    return error_response(
        code=ErrorCode.RATE_LIMITED,
        details={"retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Last resort. Logs the traceback, returns nothing about it.

    The message is deliberately generic — an exception string can contain a
    connection URL with a password in it.
    """
    log.error("unhandled_exception", exc_info=exc)
    return error_response(code=ErrorCode.INTERNAL_ERROR)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # slowapi is an optional import path during early stages; if it is not
    # installed the limiter is disabled and this handler is unnecessary.
    try:
        from slowapi.errors import RateLimitExceeded
    except ImportError:  # pragma: no cover
        return
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
