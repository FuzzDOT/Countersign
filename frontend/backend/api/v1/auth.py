"""Authentication. Brief §4, implemented in full.

The three things here that are easy to get subtly wrong, and how they are
handled:

**User enumeration.** Every login branch — unknown email, wrong password,
locked account, success — burns comparable argon2 CPU and returns after the
same wall-clock duration. The padding uses an absolute deadline rather than a
fixed sleep added at the end, because a fixed addition still leaks the variable
part underneath it.

**Refresh token reuse.** Tokens rotate on every refresh and carry a
`family_id`. Presenting a token that has already been revoked means the token
leaked and is being replayed, so the entire family is revoked and the client is
forced to re-authenticate. This is the mechanism that makes a stolen refresh
token a bounded incident.

**Cookie scope.** The refresh cookie is HttpOnly, SameSite=Strict and pathed to
`/api/v1/auth`, so it is not attached to any other request in the application.
The frontend cannot read it and must not try.
"""

from __future__ import annotations

import ipaddress
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import CurrentUserDep, DbDep, SettingsDep, permissions_for
from api.errors import AccountLocked, Conflict, RefreshReused, Unauthenticated
from api.mock import contract
from api.v1.schemas import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    RegisterRequest,
    UserOut,
)
from core import ids
from core.config import Settings
from core.logging import get_logger
from core.ratelimit import (
    LIMIT_LOGIN,
    LIMIT_REFRESH,
    LIMIT_REGISTER,
    limiter,
)
from core.security import (
    burn_password_cpu,
    hash_password,
    hash_refresh_token,
    mint_access_token,
    new_refresh_token,
    pad_to_target,
    validate_password,
    verify_password,
)
from db.models import Organization, RefreshToken, User, UserRole

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── helpers ──────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _issue_session(
    db: Session,
    user: User,
    request: Request,
    response: Response,
    settings: Settings,
    *,
    family_id: uuid.UUID | None = None,
) -> AuthResponse:
    """Mint an access token and set a fresh refresh cookie.

    `family_id` continues an existing rotation family (on refresh) or starts a
    new one (on login/register).
    """
    permissions = permissions_for(user.role)
    access_token, expires_in = mint_access_token(
        user_id=user.id,
        org_id=user.org_id,
        role=str(user.role),
        permissions=permissions,
        settings=settings,
    )

    plaintext, token_hash = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            family_id=family_id or uuid.uuid4(),
            expires_at=_now() + timedelta(seconds=settings.refresh_token_ttl_seconds),
            user_agent=(request.headers.get("user-agent") or "")[:512] or None,
            ip=_client_ip(request),
        )
    )
    db.commit()

    _set_refresh_cookie(response, plaintext, settings)
    log.info("session_issued", user_id=str(user.id), org_id=str(user.org_id), role=str(user.role))

    return AuthResponse(
        user=UserOut(id=user.id, email=user.email, role=user.role, org_id=user.org_id),
        access_token=access_token,
        expires_in=expires_in,
    )


def _client_ip(request: Request) -> str | None:
    # Deliberately the peer address, not X-Forwarded-For: the header is
    # attacker-controlled and this value is written to an audit column.
    #
    # The column is INET, so anything that is not a literal address has to be
    # dropped rather than handed to Postgres: an ASGI transport is free to put
    # a non-address there (starlette's TestClient uses the host "testclient",
    # a unix-socket peer has no address at all), and passing that through
    # turns every registration into a 500 on a DataError.
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _set_refresh_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=value,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="strict",
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="strict",
        path=settings.refresh_cookie_path,
    )


def _revoke_family(db: Session, family_id: uuid.UUID) -> int:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    return int(result.rowcount or 0)


def _mock_session(response: Response, settings: Settings) -> AuthResponse:
    """A working session with no database.

    Mints a *real* signed token for the deterministic demo owner, rather than
    serving a canned string from a fixture. That matters: the frontend's
    `Authorization` header, token refresh, and permission gating all exercise
    the real code paths in mock mode, so none of it is discovered to be broken
    at hour 12. Ids match `auth.me.json` and the seeded scenario (core/ids.py).
    """
    access_token, expires_in = mint_access_token(
        user_id=ids.DEMO_OWNER_ID,
        org_id=ids.DEMO_ORG_ID,
        role=str(UserRole.owner),
        permissions=permissions_for(UserRole.owner),
        settings=settings,
    )
    _set_refresh_cookie(response, "mock-refresh-token-not-a-real-credential", settings)
    return AuthResponse(
        user=UserOut(
            id=ids.DEMO_OWNER_ID,
            email=ids.DEMO_OWNER_EMAIL,
            role=UserRole.owner,
            org_id=ids.DEMO_ORG_ID,
        ),
        access_token=access_token,
        expires_in=expires_in,
    )


# ── register ─────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization and its owner",
)
@limiter.limit(LIMIT_REGISTER)
@contract(stage=1)
def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    db: DbDep,
    settings: SettingsDep,
) -> AuthResponse:
    """The first user of an organization is always its `owner`.

    There is no email verification (brief §13 shortcut ledger — fine for a
    24-hour demo, and stated openly rather than hidden).
    """
    if settings.mock_mode:
        return _mock_session(response, settings)

    validate_password(payload.password, settings)

    org = Organization(name=payload.org_name)
    db.add(org)
    db.flush()

    user = User(
        org_id=org.id,
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        role=UserRole.owner,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # The unique index on users.email is the authority; a pre-check SELECT
        # would still race two simultaneous registrations.
        db.rollback()
        raise Conflict(
            "That email is already registered.",
            details={"fields": {"email": "already registered"}},
        ) from None

    return _issue_session(db, user, request, response, settings)


# ── login ────────────────────────────────────────────────────────────────────


@router.post("/login", response_model=AuthResponse, summary="Exchange credentials for a session")
@limiter.limit(LIMIT_LOGIN)
@contract(stage=1)
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: DbDep,
    settings: SettingsDep,
) -> AuthResponse:
    started = time.monotonic()
    try:
        if settings.mock_mode:
            return _mock_session(response, settings)

        user = db.execute(select(User).where(User.email == str(payload.email))).scalar_one_or_none()

        if user is None:
            # Spend the CPU a real verification would, so "no such account" is
            # not measurably faster than "wrong password".
            burn_password_cpu()
            raise Unauthenticated("Those credentials are not valid.")

        now = _now()
        if user.locked_until is not None and user.locked_until > now:
            burn_password_cpu()
            raise AccountLocked(locked_until=user.locked_until.isoformat())

        is_valid, needs_rehash = verify_password(payload.password, user.password_hash)

        if not is_valid:
            _record_failure(user, now, settings)
            db.commit()
            if user.locked_until is not None and user.locked_until > now:
                raise AccountLocked(locked_until=user.locked_until.isoformat())
            raise Unauthenticated("Those credentials are not valid.")

        if not user.is_active:
            raise Unauthenticated("This account is no longer active.")

        # Successful login clears the failure window, and opportunistically
        # upgrades the hash if the argon2 cost has been raised since signup.
        user.failed_logins = 0
        user.first_failed_login_at = None
        user.locked_until = None
        if needs_rehash:
            user.password_hash = hash_password(payload.password)

        return _issue_session(db, user, request, response, settings)
    finally:
        # Every branch above — including the exceptions — leaves through here.
        pad_to_target(started, settings.login_timing_target_ms)


def _record_failure(user: User, now: datetime, settings: Settings) -> None:
    """Sliding window: N failures within M minutes, not N failures ever."""
    window = timedelta(minutes=settings.login_lockout_minutes)
    if user.first_failed_login_at is None or (now - user.first_failed_login_at) > window:
        user.first_failed_login_at = now
        user.failed_logins = 1
    else:
        user.failed_logins += 1

    if user.failed_logins >= settings.login_max_attempts:
        user.locked_until = now + window
        log.warning(
            "account_locked",
            user_id=str(user.id),
            failed_logins=user.failed_logins,
            locked_until=user.locked_until.isoformat(),
        )


# ── refresh ──────────────────────────────────────────────────────────────────


@router.post("/refresh", response_model=AuthResponse, summary="Rotate the session")
@limiter.limit(LIMIT_REFRESH)
@contract(stage=1)
def refresh(
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> AuthResponse:
    """Rotating refresh with family-based reuse detection.

    Every refresh revokes the presented token and issues a new one in the same
    family. Presenting an already-revoked token means it leaked, so the family
    is revoked wholesale and `REFRESH_REUSED` tells the frontend to hard-logout
    (frontend brief §4.3).
    """
    if settings.mock_mode:
        return _mock_session(response, settings)

    presented = request.cookies.get(settings.refresh_cookie_name)
    if not presented:
        raise Unauthenticated("No refresh cookie was presented.")

    token = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(presented))
    ).scalar_one_or_none()

    if token is None:
        _clear_refresh_cookie(response, settings)
        raise Unauthenticated("That refresh token is not recognized.")

    if token.revoked_at is not None:
        revoked = _revoke_family(db, token.family_id)
        db.commit()
        _clear_refresh_cookie(response, settings)
        log.warning(
            "refresh_token_reuse_detected",
            user_id=str(token.user_id),
            family_id=str(token.family_id),
            sessions_revoked=revoked,
        )
        raise RefreshReused()

    if token.expires_at <= _now():
        token.revoked_at = _now()
        db.commit()
        _clear_refresh_cookie(response, settings)
        raise Unauthenticated("That session has expired.")

    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        _revoke_family(db, token.family_id)
        db.commit()
        _clear_refresh_cookie(response, settings)
        raise Unauthenticated("This account is no longer active.")

    token.revoked_at = _now()
    return _issue_session(db, user, request, response, settings, family_id=token.family_id)


# ── logout ───────────────────────────────────────────────────────────────────


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    # Both explicit, and both load-bearing. This module uses
    # `from __future__ import annotations`, so `-> Response` is the *string*
    # "Response" at runtime. FastAPI infers `response_model` from the return
    # annotation when none is given, and it resolves that string against
    # `endpoint.__globals__` — which is api/mock.py's namespace, because
    # @contract wraps the handler and functools.wraps cannot copy __globals__.
    # The string therefore never resolves to the class, inference yields a
    # truthy str, and a 204 is not allowed to declare a body. Declaring both
    # here skips inference entirely.
    response_class=Response,
    response_model=None,
    summary="Revoke the current session family",
)
@contract(stage=1)
def logout(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
) -> Response:
    """Idempotent by design: logging out twice, or with no cookie, is a 204.

    An error here would strand a client that already lost its cookie.
    """
    presented = request.cookies.get(settings.refresh_cookie_name)
    if presented and not settings.mock_mode:
        token = db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(presented))
        ).scalar_one_or_none()
        if token is not None:
            _revoke_family(db, token.family_id)
            db.commit()

    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(out, settings)
    return out


# ── me ───────────────────────────────────────────────────────────────────────


@router.get("/me", response_model=MeResponse, summary="The authenticated user and permissions")
@contract("auth.me.json", stage=1)
def me(user: CurrentUserDep, db: DbDep) -> MeResponse:
    """`permissions` is the contract the frontend gates on.

    Roles may change shape; the permission list is stable, so the UI never does
    `role === 'owner'` (frontend brief §4.4).
    """
    org = db.get(Organization, user.org_id)
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        org_id=user.org_id,
        org_name=org.name if org else "",
        permissions=permissions_for(user.role),
    )
