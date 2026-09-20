"""RBAC matrix, tenant scoping, and cursor pagination.

The RBAC test is written against the brief's table directly rather than against
the implementation, so a well-meaning "just give analysts recalibrate access"
edit fails the build instead of silently handing the demo finale to the wrong
role.
"""

from __future__ import annotations

import uuid

import pytest

from api.deps import (
    ALL_PERMISSIONS,
    PERM_ABLATION_RUN,
    PERM_CALIBRATION_RUN,
    PERM_DOCUMENTS_UPLOAD,
    PERM_EVALS_READ,
    PERM_GRAPH_READ,
    PERM_INSIGHTS_READ,
    PERM_USERS_MANAGE,
    PERM_VOICE_USE,
    Cursor,
    Scope,
    permissions_for,
    require_perm,
)
from api.errors import Forbidden, NotFound, ValidationFailed
from core.security import Principal
from db.models import Document, Insight, UserRole

# Brief §4 RBAC matrix, transcribed. (permission, viewer, analyst, owner)
MATRIX = [
    (PERM_INSIGHTS_READ, True, True, True),
    (PERM_GRAPH_READ, True, True, True),
    (PERM_VOICE_USE, True, True, True),
    (PERM_DOCUMENTS_UPLOAD, False, True, True),
    (PERM_ABLATION_RUN, False, True, True),
    (PERM_EVALS_READ, False, True, True),
    (PERM_CALIBRATION_RUN, False, False, True),
    (PERM_USERS_MANAGE, False, False, True),
]


@pytest.mark.parametrize(
    ("permission", "viewer", "analyst", "owner"), MATRIX, ids=[m[0] for m in MATRIX]
)
def test_rbac_matrix(permission: str, viewer: bool, analyst: bool, owner: bool) -> None:
    assert (permission in permissions_for(UserRole.viewer)) is viewer
    assert (permission in permissions_for(UserRole.analyst)) is analyst
    assert (permission in permissions_for(UserRole.owner)) is owner


def test_matrix_covers_every_permission() -> None:
    """A new permission string must be assigned to roles, not left unmapped."""
    assert {row[0] for row in MATRIX} == set(ALL_PERMISSIONS)


def test_permission_strings_match_the_frontend_contract() -> None:
    """Frontend brief §4.4 enumerates these exactly; they are a public contract."""
    assert set(ALL_PERMISSIONS) == {
        "insights:read",
        "graph:read",
        "documents:upload",
        "ablation:run",
        "voice:use",
        "evals:read",
        "calibration:run",
        "users:manage",
    }


def test_owner_has_everything() -> None:
    assert set(permissions_for(UserRole.owner)) == set(ALL_PERMISSIONS)


def test_require_perm_rejects_unknown_permission() -> None:
    """A typo in a route guard fails at import, not silently at request time."""
    with pytest.raises(AssertionError):
        require_perm("insights:reed")


def test_require_perm_allows_and_denies() -> None:
    guard = require_perm(PERM_CALIBRATION_RUN)

    owner = _principal(UserRole.owner)
    assert guard(owner) is owner

    with pytest.raises(Forbidden) as exc:
        guard(_principal(UserRole.analyst))
    assert exc.value.details["missing"] == [PERM_CALIBRATION_RUN]


# ── tenant scoping ───────────────────────────────────────────────────────────


def test_scope_query_filters_on_org() -> None:
    scope = Scope(db=None, principal=_principal(UserRole.analyst))  # type: ignore[arg-type]
    compiled = str(scope.query(Insight))
    assert "WHERE insights.org_id = " in compiled


def test_scope_query_filters_every_scoped_model() -> None:
    scope = Scope(db=None, principal=_principal(UserRole.owner))  # type: ignore[arg-type]
    for model in (Insight, Document):
        assert "org_id" in str(scope.query(model))


def test_scope_get_or_404_returns_not_found_for_a_miss() -> None:
    """Cross-org reads must 404, not 403.

    A 403 confirms the id exists somewhere, which is an information leak across
    tenants. Brief §16 asserts the same thing end-to-end in Stage 10 with two
    seeded orgs.
    """

    class _NoRowDb:
        @staticmethod
        def execute(_stmt: object) -> object:
            return type("R", (), {"scalar_one_or_none": staticmethod(lambda: None)})()

    scope = Scope(db=_NoRowDb(), principal=_principal(UserRole.owner))  # type: ignore[arg-type]
    with pytest.raises(NotFound):
        scope.get_or_404(Insight, uuid.uuid4())


def test_scope_require_raises_for_missing_permission() -> None:
    scope = Scope(db=None, principal=_principal(UserRole.viewer))  # type: ignore[arg-type]
    scope.require(PERM_INSIGHTS_READ)
    with pytest.raises(Forbidden):
        scope.require(PERM_ABLATION_RUN)


# ── cursor pagination ────────────────────────────────────────────────────────


def test_cursor_round_trip() -> None:
    record_id = uuid.uuid4()
    cursor = Cursor(sort_value="2026-09-19T21:06:02Z", record_id=record_id)
    decoded = Cursor.decode(cursor.encode())
    assert decoded.sort_value == "2026-09-19T21:06:02Z"
    assert decoded.record_id == record_id


def test_cursor_is_url_safe_and_unpadded() -> None:
    token = Cursor(sort_value="0.9412", record_id=uuid.uuid4()).encode()
    assert "=" not in token
    assert "+" not in token and "/" not in token


@pytest.mark.parametrize(
    "bad",
    ["", "!!!!", "YWJj", "e30", "not base64 at all", "eyJ2IjoiYSJ9"],
)
def test_malformed_cursor_is_a_validation_error(bad: str) -> None:
    """A garbage cursor must be a 422 with a field message, never a 500."""
    with pytest.raises(ValidationFailed) as exc:
        Cursor.decode(bad)
    assert "cursor" in exc.value.details["fields"]


# ── helpers ──────────────────────────────────────────────────────────────────


def _principal(role: UserRole) -> Principal:
    return Principal(
        user_id=uuid.UUID("00000000-0000-4000-8000-0000000000aa"),
        org_id=uuid.UUID("00000000-0000-4000-8000-0000000000bb"),
        role=str(role),
        permissions=frozenset(permissions_for(role)),
        jti=str(uuid.uuid4()),
    )
