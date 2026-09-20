"""Rate limiting.

Brief §13 specifies per-user limits where authenticated and per-IP otherwise,
so the key function verifies the bearer token's signature and keys on `sub`,
falling back to the client IP. Verification matters: keying on an unverified
`sub` would let a caller forge a fresh bucket per request and evade the limit
entirely, which is worse than no limit because it looks like a limit.

Storage is in-process memory. That resets on reload and is not shared across
the two prod uvicorn workers, which means the effective limit is up to 2x the
configured value. Correct trade at demo volume; noted in the debt ledger
(docs/03-BACKEND-PLAN.md §6) and would be Redis in production.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from core.config import get_settings
from core.security import peek_token_org, peek_token_subject

# ── limit table (brief §13) ──────────────────────────────────────────────────
# Kept as named constants rather than inline strings so that a limit appears
# once and the decorator sites cannot drift from the documented table.

LIMIT_DEFAULT = "300/minute"
LIMIT_LOGIN = "10/minute"
LIMIT_REGISTER = "5/hour"
LIMIT_REFRESH = "60/minute"
LIMIT_UPLOAD = "20/minute"
LIMIT_SEED = "10/minute"
LIMIT_ABLATION = "30/minute"
LIMIT_VOICE = "20/minute"
# Per **org**, not per user — see `org_rate_limit_key`. The decorator on
# api/v1/calibration.py passes that key function explicitly.
LIMIT_RECALIBRATE = "5/hour"
LIMIT_FUZZER_RUN = "3/hour"


def rate_limit_key(request: Request) -> str:
    """`user:<uuid>` when a valid bearer token is present, else `ip:<addr>`.

    Login and register are unauthenticated by definition and therefore always
    land in an IP bucket, which is exactly what credential-stuffing protection
    needs.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token and (subject := peek_token_subject(token)):
            return f"user:{subject}"

    # X-Forwarded-For is not trusted here. Behind the nginx in
    # docker-compose.prod.yml the peer address is the proxy, so every user
    # shares one bucket; trusting the header instead would let a caller spoof a
    # new bucket per request. If this ever runs behind a real load balancer,
    # configure uvicorn's --forwarded-allow-ips and revisit.
    return f"ip:{get_remote_address(request)}"


def org_rate_limit_key(request: Request) -> str:
    """`org:<uuid>` when a valid bearer token is present, else `ip:<addr>`.

    Brief §13 gives one endpoint a per-**org** limit rather than a per-user
    one: `/calibration/recalibrate` at 5/hour/org. It refits the org's
    temperature and writes snapshot rows, so the budget belongs to the tenant
    — keying it on `sub` like everything else gave an org with three owners
    fifteen refits an hour against a documented five.

    Same verification discipline as `rate_limit_key`: the token's signature
    is checked, so the bucket cannot be forged.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token and (org := peek_token_org(token)):
            return f"org:{org}"
    return f"ip:{get_remote_address(request)}"


def build_limiter() -> Limiter:
    settings = get_settings()
    return Limiter(
        key_func=rate_limit_key,
        default_limits=[LIMIT_DEFAULT],
        headers_enabled=True,
        # In MOCK_MODE the frontend polls fixtures hard while building loading
        # states, and a 429 there is noise that teaches nothing. The limits are
        # exercised against the real server in the Stage 10 drill.
        enabled=not settings.mock_mode,
    )


# Module-level singleton: slowapi's decorators are applied at import time on
# the router modules, so this cannot be constructed lazily inside the app
# factory.
limiter = build_limiter()


def exempt_paths() -> frozenset[str]:
    """Paths the limiter must never reject.

    /health is polled by the Docker healthcheck every 10s and by compose's
    dependency gate; a 429 there would mark the container unhealthy and trigger
    a restart loop in the middle of a demo.
    """
    return frozenset({"/health", "/health/ready", "/api/v1/openapi.json", "/api/v1/docs"})
