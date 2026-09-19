"""The "Nemotron on everything" arm of the cascade baseline. Plan §1.9.

    make baseline
    docker compose exec backend python -m scripts.run_baseline --limit 100

`cascade_baseline.nemotron_on_everything_accuracy` needs Nemotron's opinion
on every case, not just the ones the gate flagged. That is the comparison the
cascade argument rests on — *we recover most of the accuracy for a fraction
of the calls* — and it cannot be computed from the cascade's own run.

Two things this deliberately does:

**It is capped at the labeled set** (plan §1.9), not run over all insights.
Accuracy on the unlabeled remainder is not a number, so calling the model on
them would spend budget to learn nothing.

**It is a separate script, run once.** ~100 calls is real money and real
rate limit. Results are cached by prompt digest like every other call, so a
re-run after a corpus change only pays for what changed.

With no `NEMOTRON_API_KEY` this exits non-zero having written nothing, and
`GET /evals/routing` continues to report the arm as not run rather than
inventing a number for it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import delete, select

from core import ids
from core.config import get_settings
from core.logging import configure_logging, get_logger
from db.models import Insight, RoutingBucket, RoutingEvalCase
from db.session import db_session
from ml.cascade.cascade import CascadeCandidate, neighbourhood
from ml.cascade.evalset import SPLIT_EVAL, SPLIT_NEMOTRON_ALL
from ml.cascade.nemotron import NemotronClient, NemotronRequest, NemotronUnavailable
from ml.cascade.prompt import PromptContext
from ml.cascade.routing import ClaimContext, GraphContext
from ml.cascade.routing import score as route_claim

log = get_logger(__name__)

# Plan §1.9: the other insights have no ground truth to be accurate against.
DEFAULT_LIMIT = 100


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Nemotron on every labeled case.")
    parser.add_argument("--org", default=None, help="Defaults to the demo tenant.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)
    org_id = uuid.UUID(args.org) if args.org else ids.DEMO_ORG_ID

    client = NemotronClient(settings)
    if not client.configured:
        print(
            "NEMOTRON_API_KEY is not configured, so the "
            "nemotron-on-everything arm cannot be run. GET /evals/routing will "
            "continue to report it as not run rather than invent a number.",
        )
        return 2

    with db_session() as db:
        cases = list(
            db.execute(
                select(RoutingEvalCase)
                .where(
                    RoutingEvalCase.org_id == org_id,
                    RoutingEvalCase.split == SPLIT_EVAL,
                )
                .order_by(RoutingEvalCase.created_at, RoutingEvalCase.id)
                .limit(args.limit)
            ).scalars()
        )
        if not cases:
            print("No labeled cases. Run `make eval` first.")
            return 2

        insights = {
            insight.id: insight
            for insight in db.execute(select(Insight).where(Insight.org_id == org_id)).scalars()
        }
        names = _entity_names(db, org_id)
        candidates = _candidates(cases, insights, names, settings)

        requests = [
            NemotronRequest(
                insight_id=candidate.insight_id,
                context=PromptContext(
                    relation=candidate.relation,
                    subject=candidate.subject_name,
                    object=candidate.object_name,
                    confidence=candidate.confidence,
                    vacuity=candidate.vacuity,
                    dissonance=candidate.dissonance,
                    classical_decision=candidate.classical.bucket.value,
                    classical_reasons=candidate.classical.reasons,
                    citation=candidate.citation,
                    neighbourhood=neighbourhood(candidates, candidate.subject_id),
                ),
            )
            for candidate in candidates
        ]

        results = asyncio.run(client.decide_many(requests))

        db.execute(
            delete(RoutingEvalCase).where(
                RoutingEvalCase.org_id == org_id,
                RoutingEvalCase.split == SPLIT_NEMOTRON_ALL,
            )
        )

        succeeded = failed = 0
        for case, result in zip(cases, results, strict=True):
            if isinstance(result, NemotronUnavailable):
                # A case the upstream could not answer is excluded rather
                # than counted as a miss. This arm is meant to measure the
                # model's ceiling, and scoring its outages against it would
                # understate the thing the cascade is being compared to.
                failed += 1
                log.warning("baseline_call_failed", case=str(case.id), reason=result.reason)
                continue
            succeeded += 1
            db.add(
                RoutingEvalCase(
                    org_id=org_id,
                    insight_id=case.insight_id,
                    ground_truth=case.ground_truth,
                    predicted=RoutingBucket(result.decision.decision),
                    resolved_by="nemotron",
                    split=SPLIT_NEMOTRON_ALL,
                    gate_bypassed=True,
                    is_failure=result.decision.decision != case.ground_truth.value,
                    failure_note=None,
                )
            )
        db.commit()

        from api.v1.evals import build_routing_eval

        report = build_routing_eval(db, org_id)

    print(
        json.dumps(
            {
                "cases": len(cases),
                "succeeded": succeeded,
                "failed": failed,
                "cascade_baseline": report.cascade_baseline.model_dump(),
            },
            indent=2,
        )
    )
    return 0 if succeeded else 1


def _entity_names(db, org_id: uuid.UUID) -> dict[uuid.UUID, str]:  # type: ignore[no-untyped-def]
    from db.models import Entity

    return dict(
        db.execute(select(Entity.id, Entity.canonical).where(Entity.org_id == org_id))
        .tuples()
        .all()
    )


def _candidates(cases, insights, names, settings) -> list[CascadeCandidate]:  # type: ignore[no-untyped-def]
    claims = [
        ClaimContext(
            insights[case.insight_id].subject_id,
            insights[case.insight_id].object_id,
            insights[case.insight_id].relation,
            insights[case.insight_id].confidence,
        )
        for case in cases
        if case.insight_id in insights
    ]
    context = GraphContext.build(claims)

    out: list[CascadeCandidate] = []
    for case in cases:
        insight = insights.get(case.insight_id)
        if insight is None:
            continue
        claim = ClaimContext(
            insight.subject_id, insight.object_id, insight.relation, insight.confidence
        )
        out.append(
            CascadeCandidate(
                insight_id=insight.id,
                subject_id=insight.subject_id,
                object_id=insight.object_id,
                subject_name=names.get(insight.subject_id, "?"),
                object_name=names.get(insight.object_id, "?"),
                relation=insight.relation,
                confidence=insight.confidence,
                vacuity=insight.vacuity,
                dissonance=insight.dissonance,
                citation=insight.sentence_text,
                classical=route_claim(claim, context, settings),
            )
        )
    return out


if __name__ == "__main__":
    raise SystemExit(main())
