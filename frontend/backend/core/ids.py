"""Deterministic identifiers.

Seeded scenarios use UUID5 over a fixed namespace rather than
`gen_random_uuid()`, for three reasons that all show up on stage:

1. The hand-written fallback briefing transcript carries `insight_id` values
   (plan §1.10). Those have to stay valid across a `make nuke && make seed`,
   or the wifi-failure path silently highlights nothing.
2. The demo script says "click the Meridian insight". That has to be the same
   row after a database reset at hour 23.
3. Fixtures and mock-mode auth must agree on ids, so the frontend's mock
   session and its mock `/auth/me` describe the same organization.

Ids are a pure function of the seed key, so two machines seeding the same
scenario produce identical ids. That is also what lets the demo be rehearsed
on one laptop and run on another.
"""

from __future__ import annotations

import uuid

# Fixed namespace. Never regenerate this value — every seeded id in every
# fixture and every prerecorded transcript derives from it.
CS_NAMESPACE = uuid.UUID("c0de516e-0000-5000-8000-636f756e7465")


def stable_uuid(kind: str, *parts: str | int) -> uuid.UUID:
    """A reproducible UUID5 for a logical object.

    `kind` namespaces the key so an entity and a document with the same name
    do not collide.
    """
    key = f"{kind}:" + "|".join(str(p) for p in parts)
    return uuid.uuid5(CS_NAMESPACE, key)


# ── the demo organization ────────────────────────────────────────────────────
# Used by the synthetic generator, by scripts/gen_fixtures.py, and by mock-mode
# auth, so all three describe one consistent tenant.

DEMO_ORG_NAME = "Meridian Ops"
DEMO_ORG_ID = stable_uuid("org", DEMO_ORG_NAME)

DEMO_OWNER_EMAIL = "ops@meridian.example"
DEMO_OWNER_ID = stable_uuid("user", DEMO_OWNER_EMAIL)

# Second tenant, existing only so the cross-org isolation test has somewhere to
# read from. Never seeded with demo data.
CONTROL_ORG_NAME = "Kestrel Audit"
CONTROL_ORG_ID = stable_uuid("org", CONTROL_ORG_NAME)
CONTROL_OWNER_EMAIL = "audit@kestrel.example"
CONTROL_OWNER_ID = stable_uuid("user", CONTROL_OWNER_EMAIL)


def entity_id(org_id: uuid.UUID, canonical: str, entity_type: str) -> uuid.UUID:
    return stable_uuid("entity", str(org_id), entity_type, canonical.casefold())


def document_id(org_id: uuid.UUID, scenario: str, index: int) -> uuid.UUID:
    return stable_uuid("document", str(org_id), scenario, index)


def insight_id(
    document: uuid.UUID,
    char_start: int,
    relation: str,
    subject: uuid.UUID,
    object_: uuid.UUID,
) -> uuid.UUID:
    """Keyed on the citation span and the pair it relates.

    Re-extracting the same claim from the same sentence yields the same id,
    which is what keeps the prerecorded briefing's `insight_id` values valid
    across `make nuke` (plan §1.11).

    The two entity ids are part of the key because one sentence can assert
    the same relation about two different pairs — "A and B both wired funds
    to C" is one sentence and two insights, and keying on the span alone
    would collapse them into one row whose citation is right and whose
    subject is a coin flip.
    """
    return stable_uuid("insight", str(document), char_start, relation, str(subject), str(object_))
