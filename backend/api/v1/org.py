"""Organization membership. Frontend brief §3: `/app/settings` shows
"Profile, org, members (owner only)".

The settings screen has always called `GET /org/members`; nothing served it,
so the only owner-visible panel on that page rendered an error envelope. One
read, scoped to the caller's org, gated on `users:manage` so the permission
string the UI already checks and the permission the server enforces are the
same one.

Read-only on purpose. Invitations and role changes are a mutation surface with
an email side effect and a privilege-escalation path, and neither brief asks
for them — the UI's "Invite member" button is disabled for the same reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from api.deps import PERM_USERS_MANAGE, ScopeDep, require_perm
from api.mock import contract
from api.v1.schemas import MemberOut
from db.models import User

router = APIRouter(prefix="/org", tags=["org"])


@router.get(
    "/members",
    response_model=list[MemberOut],
    dependencies=[Depends(require_perm(PERM_USERS_MANAGE))],
    summary="Everyone in the caller's organization",
)
@contract("org.members.json", stage=1)
def list_members(scope: ScopeDep) -> list[MemberOut]:
    """Ordered oldest first, so the founding owner is row one and the list is
    stable across reloads rather than reshuffling on every fetch."""
    rows = scope.db.execute(
        select(User).where(User.org_id == scope.org_id).order_by(User.created_at, User.id)
    ).scalars()
    return [
        MemberOut(id=user.id, email=user.email, role=user.role, created_at=user.created_at)
        for user in rows
    ]
