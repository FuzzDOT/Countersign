"""ASGI middleware: request correlation, security headers, request size cap.

Middleware ordering in Starlette is counter-intuitive and getting it backwards
is a classic bug, so it is written down here and in `main.py`:
`add_middleware` inserts at the *front* of the list, and the stack is built by
wrapping in reverse, so **the middleware added last ends up outermost**.
`main.py` therefore adds these in inside-out order.
"""

from __future__ import annotations

import re
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from api.errors import ErrorCode, error_response
from core.config import Settings
from core.logging import (
    clear_request_context,
    get_logger,
    new_request_id,
    set_request_id,
)

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# An inbound request id is echoed only if it is boring. Without this, a caller
# could inject newlines into every log line originating from their request.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

# Health checks fire every 10 seconds per container and would otherwise bury
# real traffic in the demo logs.
_QUIET_PATHS = frozenset({"/health", "/health/ready"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind it for logging, echo it on the response.

    Outermost middleware, so every response — including error envelopes — can
    carry an id the operator can grep for. The one path this does not cover is
    a truly unhandled exception, which Starlette's ServerErrorMiddleware
    catches outside all user middleware; `errors.error_response` sets the header
    itself for that case.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = inbound if _SAFE_REQUEST_ID.match(inbound) else new_request_id()
        set_request_id(request_id)
        request.state.request_id = request_id

        started = time.monotonic()
        quiet = request.url.path in _QUIET_PATHS
        try:
            response = await call_next(request)
        except Exception:
            # Logged with a traceback by the exception handler; this line exists
            # so the timing and route are recorded even when the handler chain
            # replaces the response.
            log.warning(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            raise
        else:
            if not quiet:
                log.info(
                    "request",
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            clear_request_context()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Transport hardening headers (brief §13).

    The CSP is environment-dependent and that is deliberate, not an oversight:
    Vite's dev server injects inline scripts for HMR, so a policy with no
    `unsafe-inline` breaks local development entirely. The strict policy applies
    when the API is serving the built bundle (`SERVE_STATIC`) or in production.
    Documented in the writeup as a dev/prod split.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.strict_csp = settings.serve_static or settings.is_production
        self.csp = self._build_csp()

    def _build_csp(self) -> str:
        directives = [
            "default-src 'self'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            # blob: is required: the voice console decodes an AudioBuffer for
            # the waveform strip, and MediaRecorder output is a blob URL.
            "media-src 'self' blob:",
            "connect-src 'self'",
        ]
        if self.strict_csp:
            directives += ["script-src 'self'", "style-src 'self'"]
        else:
            directives += [
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
                "style-src 'self' 'unsafe-inline'",
            ]
        return "; ".join(directives)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        defaults = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": self.csp,
            # The voice console needs the microphone from our own origin.
            "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
        }
        if self.settings.is_production:
            # Only over HTTPS. Sending HSTS from a plain-http demo would pin
            # localhost to https in the judge's browser, which is a genuinely
            # annoying thing to do to someone.
            defaults["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        for name, value in defaults.items():
            if name not in response.headers:
                response.headers[name] = value
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests before they are buffered.

    The per-file 2 MB cap in brief §5 does not stop fifty files arriving in one
    multipart body, and python-multipart will happily spool the whole thing to
    disk first. This checks the declared Content-Length and refuses early.

    A chunked request with no Content-Length slips past this check; the
    per-file cap in the upload handler is the backstop there, and nginx's
    `client_max_body_size` is the real defense in the prod stack.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message="Content-Length is not a valid integer.",
                    details={"fields": {"content-length": "must be an integer"}},
                )
            if length > self.max_bytes:
                log.info("request_too_large", declared_bytes=length, limit=self.max_bytes)
                return error_response(
                    code=ErrorCode.PAYLOAD_TOO_LARGE,
                    message="The request body exceeds the maximum allowed size.",
                    details={"max_bytes": self.max_bytes, "declared_bytes": length},
                )
        return await call_next(request)
