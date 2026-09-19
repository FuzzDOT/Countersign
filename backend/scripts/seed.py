"""Seed a scenario and run the ingest pipeline over it, synchronously.

    make seed s=meridian_shell_ring
    docker compose exec backend python -m scripts.seed --scenario clean_baseline --reset

Same code path as the demo button (`POST /api/v1/documents/seed`), minus the
HTTP layer and the background task — this runs the job in the foreground and
prints what it produced, which is what makes it useful from a Makefile and in
the Stage 10 clean-boot drill.

The organization and its owner are created if absent, with the deterministic
ids from `core.ids`, so the seeded data belongs to the same tenant the
fixtures and the prerecorded briefing describe.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core import ids
from core.config import get_settings
from core.logging import configure_logging, get_logger
from core.security import hash_password
from data.synth.scenarios import SERVABLE_SCENARIOS
from db.models import Document, Entity, IngestJob, Insight, Organization, User, UserRole
from db.session import db_session
from workers.pipeline import IngestPipeline, create_job
from workers.seed import seed_documents

log = get_logger(__name__)

# Development-only credentials for the seeded owner. Overridable, and never
# used outside a seeded demo tenant — `check_production_safety` refuses to
# start a production process with a placeholder secret, and this script is not
# something production runs.
DEMO_PASSWORD = "countersign-demo-2026"  # noqa: S105 - dev seed credential, not a secret  # noqa: S105 - dev seed credential, not a secret


def ensure_org(
    db: Session, org_id: uuid.UUID, name: str, owner_email: str, owner_id: uuid.UUID
) -> None:
    org = db.get(Organization, org_id)
    if org is None:
        db.add(Organization(id=org_id, name=name))
        db.flush()
        log.info("org_created", org_id=str(org_id), name=name)

    owner = db.get(User, owner_id)
    if owner is None:
        existing = db.execute(select(User).where(User.email == owner_email)).scalar_one_or_none()
        if existing is None:
            db.add(
                User(
                    id=owner_id,
                    org_id=org_id,
                    email=owner_email,
                    password_hash=hash_password(DEMO_PASSWORD),
                    role=UserRole.owner,
                )
            )
            db.flush()
            log.info("owner_created", email=owner_email)


def reset_org(db: Session, org_id: uuid.UUID) -> None:
    """Drop this org's corpus and everything derived from it.

    Ordered by dependency rather than relying on CASCADE, so the counts
    printed afterwards are unambiguous about what went.
    """
    db.execute(delete(Insight).where(Insight.org_id == org_id))
    db.execute(delete(Entity).where(Entity.org_id == org_id))
    db.execute(delete(Document).where(Document.org_id == org_id))
    db.execute(delete(IngestJob).where(IngestJob.org_id == org_id))
    db.flush()
    log.info("org_reset", org_id=str(org_id))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a synthetic scenario and ingest it.")
    parser.add_argument("--scenario", default="meridian_shell_ring")
    parser.add_argument(
        "--reset", action="store_true", help="Delete the org's existing corpus first."
    )
    parser.add_argument(
        "--org", default=None, help="Organization UUID. Defaults to the demo tenant."
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)

    if args.scenario not in SERVABLE_SCENARIOS:
        print(
            f"{args.scenario!r} is not servable. Choose one of {list(SERVABLE_SCENARIOS)}.\n"
            "train_corpus is the model training split and is deliberately not seedable.",
            file=sys.stderr,
        )
        return 2

    org_id = uuid.UUID(args.org) if args.org else ids.DEMO_ORG_ID

    with db_session() as db:
        ensure_org(db, org_id, ids.DEMO_ORG_NAME, ids.DEMO_OWNER_EMAIL, ids.DEMO_OWNER_ID)
        # The control tenant exists so the cross-org isolation drill has
        # somewhere to read from. It is never seeded with demo data.
        ensure_org(
            db,
            ids.CONTROL_ORG_ID,
            ids.CONTROL_ORG_NAME,
            ids.CONTROL_OWNER_EMAIL,
            ids.CONTROL_OWNER_ID,
        )
        if args.reset:
            reset_org(db, org_id)

        result = seed_documents(db, org_id=org_id, scenario=args.scenario, settings=settings)
        document_ids = result.document_ids or list(
            db.execute(
                select(Document.id)
                .where(Document.org_id == org_id)
                .where(Document.meta["scenario"].astext == args.scenario)
            ).scalars()
        )
        job = create_job(db, org_id=org_id, document_ids=document_ids, scenario=args.scenario)
        db.commit()

        outcome = IngestPipeline(db, job, settings).run()

    print(
        "\n".join(
            (
                f"scenario    {args.scenario}",
                f"org         {org_id}",
                f"job         {outcome.job_id} ({outcome.state.value})",
                f"documents   {outcome.documents} "
                f"({result.duplicates_skipped} duplicate(s) skipped)",
                f"mentions    {outcome.mentions}",
                f"entities    {outcome.entities}",
                f"insights    {outcome.insights}",
                f"elapsed     {outcome.elapsed_ms} ms",
            )
        )
    )
    return 0 if outcome.state.value == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
