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
from ml.cascade.nemotron import NemotronClient, NemotronRequest, NemotronResult, NemotronUnavailable
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

    # `nemotron_max_concurrency` (6) is tuned for the live cascade, which
    # escalates a handful of insights per ingest — nowhere near a rate limit.
    # This script fires up to a hundred requests in one run, and against a
    # trial-tier key that burst rate hits the limit even with the client's
    # existing retry+backoff: a real run here got 17/57 permanent 429s. A
    # copy of settings with much lower concurrency, scoped to this script
    # only, so the live cascade's own traffic pattern is untouched.
    baseline_settings = settings.model_copy(update={"nemotron_max_concurrency": 2})
    client = NemotronClient(baseline_settings)
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

        # A `routing_eval_case` row survives a re-seed of the same org; the
        # insight it points at does not — `make seed` on an already-seeded
        # tenant produces fresh insight rows with fresh ids, and
        # `build_routing_eval` has to be re-run to catch up. Filtering here,
        # once, is what keeps `cases`, the candidates built from them, and
        # the Nemotron results all the same length in the same order — the
        # alternative is a `zip(cases, results, strict=True)` that blows up
        # the moment any one case is stale, which is what actually happened
        # the first time this ran against a database with a live key: the
        # crash looked like a Nemotron problem and was a stale-row problem.
        stale = [case for case in cases if case.insight_id not in insights]
        if stale:
            log.warning(
                "stale_eval_cases_skipped",
                count=len(stale),
                hint="run `make eval` (scripts.build_routing_eval) to refresh routing_eval_cases",
            )
        cases = [case for case in cases if case.insight_id in insights]
        if not cases:
            print(
                "Every labeled case is stale (points at an insight that no longer "
                "exists). Run `make eval` to rebuild routing_eval_cases against the "
                "current insights, then retry."
            )
            return 2

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

        results = asyncio.run(_decide_in_batches(client, requests))

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


# Chunk size and pause are deliberately conservative rather than tuned against
# an unknown rate-limit window. A trial-tier key's limit is usually per-minute,
# and the client's own retry backoff tops out at 2 seconds — nowhere near
# enough headroom if the whole batch lands in the same window. Halving the
# concurrency (settings.nemotron_max_concurrency=2, set by the caller) and
# adding a real pause between chunks costs a few minutes on a ~60-call run;
# a permanently-failed 30% of the baseline costs the number the cascade
# argument rests on.
BASELINE_CHUNK_SIZE = 10
BASELINE_CHUNK_PAUSE_SECONDS = 15.0


async def _decide_in_batches(
    client: NemotronClient, requests: list[NemotronRequest]
) -> list[NemotronResult | NemotronUnavailable]:
    """`decide_many`, paced in chunks instead of fired all at once.

    Each chunk still runs concurrently (up to the client's own semaphore) —
    only the *chunks* are sequential, with a pause between them so a
    rolling-window rate limit has time to clear before the next burst.
    """
    results: list[NemotronResult | NemotronUnavailable] = []
    for start in range(0, len(requests), BASELINE_CHUNK_SIZE):
        chunk = requests[start : start + BASELINE_CHUNK_SIZE]
        results.extend(await client.decide_many(chunk))
        remaining = len(requests) - (start + len(chunk))
        if remaining > 0:
            log.info(
                "baseline_pacing",
                completed=start + len(chunk),
                remaining=remaining,
                pause_seconds=BASELINE_CHUNK_PAUSE_SECONDS,
            )
            await asyncio.sleep(BASELINE_CHUNK_PAUSE_SECONDS)
    return results


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
