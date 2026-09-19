"""Request dependencies: authentication, RBAC, tenant scoping, pagination.

The important thing in this file is `Scope`. Brief §13 is right that a
cross-org read is the one bug class that would actually be embarrassing, and
the defense is a helper you cannot forget to call rather than a `.where()` you
have to remember in forty places. `Scope.query()` is typed to accept only
models inheriting `OrgScoped`, so a missing tenant filter is a type error at
lint time, not a data breach at demo time.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from fastapi import Depends, Query, Request
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from api.errors import (
    AppError,
    Forbidden,
    NotFound,
    Unauthenticated,
    ValidationFailed,
)
from core import ids
from core.config import Settings, get_settings
from core.security import Principal, decode_access_token
from db.models import Base, OrgScoped, User, UserRole
from db.session import get_db

# ── permissions (frontend brief §4.4 — these strings are a public contract) ──

PERM_INSIGHTS_READ = "insights:read"
PERM_GRAPH_READ = "graph:read"
PERM_DOCUMENTS_UPLOAD = "documents:upload"
PERM_ABLATION_RUN = "ablation:run"
PERM_VOICE_USE = "voice:use"
PERM_EVALS_READ = "evals:read"
PERM_CALIBRATION_RUN = "calibration:run"
PERM_USERS_MANAGE = "users:manage"

ALL_PERMISSIONS: tuple[str, ...] = (
    PERM_INSIGHTS_READ,
    PERM_GRAPH_READ,
    PERM_DOCUMENTS_UPLOAD,
    PERM_ABLATION_RUN,
    PERM_VOICE_USE,
    PERM_EVALS_READ,
    PERM_CALIBRATION_RUN,
    PERM_USERS_MANAGE,
)

# Brief §4 RBAC matrix. The frontend gates affordances on the permission list
# from /auth/me, never on the role string, so roles can change shape without
# a frontend release.
ROLE_PERMISSIONS: dict[UserRole, tuple[str, ...]] = {
    UserRole.viewer: (
        PERM_INSIGHTS_READ,
        PERM_GRAPH_READ,
        PERM_VOICE_USE,
    ),
    UserRole.analyst: (
        PERM_INSIGHTS_READ,
        PERM_GRAPH_READ,
        PERM_VOICE_USE,
        PERM_DOCUMENTS_UPLOAD,
        PERM_ABLATION_RUN,
        PERM_EVALS_READ,
    ),
    UserRole.owner: ALL_PERMISSIONS,
}


def permissions_for(role: UserRole | str) -> list[str]:
    resolved = role if isinstance(role, UserRole) else UserRole(role)
    return list(ROLE_PERMISSIONS[resolved])


# ── authentication ───────────────────────────────────────────────────────────


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header:
        raise Unauthenticated("Authorization header is missing.")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthenticated("Expected an `Authorization: Bearer <token>` header.")
    return token.strip()


def get_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """Decode and verify the access token. No database access.

    Permissions ride in the token, so authorization costs nothing per request.
    The trade: deactivating a user or demoting their role does not take effect
    until their current access token expires — at most 15 minutes. Immediate
    revocation goes through the refresh-token family, which is the mechanism
    that actually matters for a stolen credential.
    """
    return decode_access_token(bearer_token(request), settings)


def get_current_user(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    """Load the live user row. Use where freshness matters (e.g. /auth/me).

    Also the enforcement point for `is_active`: a token remains
    cryptographically valid after deactivation, so any endpoint that should
    stop working immediately must depend on this rather than on the principal.
    """
    if settings.mock_mode:
        # Mock mode promises the frontend a working API with no Postgres at
        # all (brief §12). This dependency was the one thing that broke that:
        # a token minted by mock login is valid, but the user row it points at
        # does not exist, so /auth/me returned 401 and the frontend's session
        # bootstrap would have failed on their first commit.
        #
        # The object is synthesized from the verified token and never added to
        # a session. The handler is fixture-backed in mock mode and does not
        # read it — what matters is that a valid token resolves rather than
        # 401s. Signature verification and permission checks are unaffected
        # and still real.
        return User(
            id=principal.user_id,
            org_id=principal.org_id,
            email=ids.DEMO_OWNER_EMAIL,
            password_hash="",
            role=UserRole(principal.role),
            is_active=True,
        )

    user = db.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise Unauthenticated("This account is no longer active.")
    if user.org_id != principal.org_id:
        # Token org and row org disagree: either a stale token after an org
        # move, or someone is holding a token they should not have.
        raise Unauthenticated("The access token does not match this account.")
    return user


def require_perm(*required: str) -> Callable[[Principal], Principal]:
    """Dependency factory enforcing the RBAC matrix.

    A 403 reaching the frontend is treated there as a *frontend* bug — the
    affordance should never have rendered (frontend brief §4.3) — so this is
    the backstop, not the primary gate.
    """
    for perm in required:
        if perm not in ALL_PERMISSIONS:
            raise AssertionError(f"unknown permission {perm!r}")

    def _guard(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        missing = [p for p in required if not principal.can(p)]
        if missing:
            raise Forbidden(
                "You do not have permission to do that.",
                details={"required": list(required), "missing": missing},
            )
        return principal

    return _guard


# ── tenant scoping ───────────────────────────────────────────────────────────

ScopedModel = TypeVar("ScopedModel", bound=OrgScoped)


@dataclass(slots=True)
class Scope:
    """A database session already bound to one organization.

    Every read of tenant data goes through here. `query()` returns a Select
    pre-filtered on `org_id`; `get_or_404()` returns 404 rather than 403 for a
    row belonging to another org, so the API does not confirm that an id exists
    elsewhere.
    """

    db: Session
    principal: Principal

    @property
    def org_id(self) -> uuid.UUID:
        return self.principal.org_id

    @property
    def user_id(self) -> uuid.UUID:
        return self.principal.user_id

    def can(self, permission: str) -> bool:
        return self.principal.can(permission)

    def require(self, *permissions: str) -> None:
        missing = [p for p in permissions if not self.principal.can(p)]
        if missing:
            raise Forbidden(details={"required": list(permissions), "missing": missing})

    def query(self, model: type[ScopedModel]) -> Select[tuple[ScopedModel]]:
        return select(model).where(model.org_id == self.org_id)

    def get_or_404(
        self,
        model: type[ScopedModel],
        record_id: uuid.UUID,
        *,
        error: type[AppError] = NotFound,
    ) -> ScopedModel:
        stmt = self.query(model).where(model.id == record_id)
        found = self.db.execute(stmt).scalar_one_or_none()
        if found is None:
            raise error(details={"id": str(record_id)})
        return found

    def child_query(
        self,
        model: type[Base],
        parent: type[ScopedModel],
        join_condition: Any,
    ) -> Select[Any]:
        """Scoped read for a table with no `org_id` of its own.

        Mentions, ablation runs, fragility trials and Nemotron runs are reachable
        only through an org-scoped parent. Forcing the join through this method
        means those tables cannot be read unfiltered by accident.
        """
        return select(model).join(parent, join_condition).where(parent.org_id == self.org_id)


def get_scope(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> Generator[Scope, None, None]:
    yield Scope(db=db, principal=principal)


# ── pagination (brief §3 success envelope) ───────────────────────────────────

MAX_LIMIT = 100
DEFAULT_LIMIT = 25


@dataclass(slots=True)
class Cursor:
    """Opaque keyset cursor.

    Keyset, not OFFSET: the feed is sorted by `created_at DESC` and new insights
    arrive during the demo, so an offset-paginated second page would silently
    skip or repeat rows. The payload is base64 for opacity only — it is not
    signed, and it carries no data the caller cannot already see.
    """

    sort_value: str
    record_id: uuid.UUID

    def encode(self) -> str:
        raw = json.dumps({"v": self.sort_value, "id": str(self.record_id)}, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Cursor:
        padded = token + "=" * (-len(token) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return cls(sort_value=str(payload["v"]), record_id=uuid.UUID(str(payload["id"])))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError):
            raise ValidationFailed(
                "The pagination cursor is not valid.",
                details={"fields": {"cursor": "malformed or expired cursor"}},
            ) from None


@dataclass(slots=True)
class Pagination:
    limit: int
    cursor: Cursor | None

    def envelope(self, rows: Sequence[Any], next_cursor: str | None) -> dict[str, Any]:
        return {
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "limit": self.limit,
        }


def get_pagination(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Pagination:
    return Pagination(limit=limit, cursor=Cursor.decode(cursor) if cursor else None)


# ── annotated aliases (keeps route signatures readable) ──────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]
ScopeDep = Annotated[Scope, Depends(get_scope)]
PaginationDep = Annotated[Pagination, Depends(get_pagination)]


# ── keyset pagination helper ─────────────────────────────────────────────────


@dataclass(slots=True)
class Page:
    """One page of rows plus the envelope the response model expects."""

    rows: list[Any]
    next_cursor: str | None
    cursor: str | None
    limit: int

    def envelope(self) -> dict[str, Any]:
        return {
            "cursor": self.cursor,
            "next_cursor": self.next_cursor,
            "has_more": self.next_cursor is not None,
            "limit": self.limit,
        }


def keyset_page(
    db: Session,
    stmt: Select[Any],
    pagination: Pagination,
    *,
    sort_column: Any,
    id_column: Any,
    sort_value: Callable[[Any], str],
    parse_sort_value: Callable[[str], Any],
    descending: bool = True,
    value_of: Callable[[Any], Any] | None = None,
) -> Page:
    """Fetch one keyset page, ordered by `(sort_column, id_column)`.

    Keyset rather than OFFSET for the reason in `Cursor`: rows arrive during
    the demo, and an offset second page would skip or repeat them. The id is
    part of the key so a tie on the sort column — two documents received in
    the same second, which the generator produces on purpose — cannot make a
    row invisible.

    One row past the limit is fetched to decide `has_more` without a second
    COUNT query over the same predicate.
    """
    from sqlalchemy import literal, tuple_

    if pagination.cursor is not None:
        boundary = tuple_(sort_column, id_column)
        pivot = tuple_(
            literal(parse_sort_value(pagination.cursor.sort_value)),
            literal(pagination.cursor.record_id),
        )
        stmt = stmt.where(boundary < pivot if descending else boundary > pivot)

    order = (
        (sort_column.desc(), id_column.desc())
        if descending
        else (sort_column.asc(), id_column.asc())
    )
    rows = list(db.execute(stmt.order_by(*order).limit(pagination.limit + 1)).scalars())

    next_cursor: str | None = None
    if len(rows) > pagination.limit:
        rows = rows[: pagination.limit]
        last = rows[-1]
        # The sort value is read off the row through the column's own key,
        # so a caller cannot pass a formatter and a column that disagree.
        # `value_of` overrides that for a sort over an expression rather than
        # a plain column — `coalesce(fragility, 0)` has no `.key`.
        raw = value_of(last) if value_of is not None else getattr(last, sort_column.key)
        next_cursor = Cursor(sort_value=sort_value(raw), record_id=last.id).encode()

    return Page(
        rows=rows,
        next_cursor=next_cursor,
        cursor=pagination.cursor.encode() if pagination.cursor else None,
        limit=pagination.limit,
    )


def iso_cursor_value(value: Any) -> str:
    return value.isoformat()


def parse_iso_cursor_value(raw: str) -> Any:
    from datetime import datetime

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise ValidationFailed(
            "The pagination cursor is not valid.",
            details={"fields": {"cursor": "unparseable sort value"}},
        ) from None
