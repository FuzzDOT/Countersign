"""The differentiator endpoints. Brief §10.

`/evals/fragility` is the single most important response object in the project:
it reports whether our uncertainty score predicts real adversarial fragility,
which is the one claim nobody else in that room is equipped to make.

Whatever the correlation comes out as is what this returns. There is no branch
in the fuzzer or in this handler that improves a number. A middling coefficient
with a diagnosis is the research-credible outcome; a suspicious 0.97 gets taken
apart in the Q&A by whoever has fit a calibration curve before.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import PERM_CALIBRATION_RUN, PERM_EVALS_READ, ScopeDep, require_perm
from api.mock import NotImplementedYet, contract
from api.v1.schemas import (
    CalibrationEval,
    Correlation,
    FragilityEval,
    FuzzerRunResponse,
    PerturbationStat,
    QuartileRow,
    RoutingEval,
    ScatterPoint,
)
from core.logging import get_logger
from core.ratelimit import LIMIT_FUZZER_RUN, limiter
from db.models import FragilityTrial, Insight
from ml.fuzzer import fragility as scoring
from ml.fuzzer.base import FAMILIES
from ml.fuzzer.runner import run_fuzzer_job

log = get_logger(__name__)

router = APIRouter(prefix="/evals", tags=["evals"])


def _fragility_of(insight: Insight, trials: list[scoring.Trial]) -> float:
    stored = insight.fragility
    return float(stored) if stored is not None else scoring.fragility_of(trials)


def build_fragility_eval(db: Session, org_id: uuid.UUID) -> FragilityEval:
    """Assemble the report from persisted trials.

    A free function rather than handler-only code because `scripts/run_fuzzer.py`
    prints exactly this — the number on the slide and the number the API serves
    have to come from one implementation or they will eventually disagree.
    """
    rows = (
        db.execute(
            select(Insight, FragilityTrial)
            .join(FragilityTrial, FragilityTrial.insight_id == Insight.id)
            .where(Insight.org_id == org_id)
        )
        .tuples()
        .all()
    )

    trials_by_insight: dict[uuid.UUID, list[scoring.Trial]] = defaultdict(list)
    insights: dict[uuid.UUID, Insight] = {}
    for insight, trial in rows:
        insights[insight.id] = insight
        trials_by_insight[insight.id].append(
            scoring.Trial(
                insight_id=insight.id,
                perturbation=str(trial.perturbation),
                variant=trial.variant,
                label_flipped=trial.label_flipped,
                conf_delta=trial.conf_delta,
                relation_lost=trial.relation_lost,
            )
        )

    points = [
        scoring.ScatterPoint(
            insight_id=insight_id,
            vacuity=insights[insight_id].vacuity,
            # The stored value, not a recomputation: the fuzzer wrote it and
            # a second derivation here could drift from what the feed shows.
            # The stored value when the fuzzer wrote one; recomputed only if
            # trials exist without a score, which means a run died partway.
            fragility=_fragility_of(insights[insight_id], trials),
            routing=insights[insight_id].routing.value,
        )
        for insight_id, trials in sorted(trials_by_insight.items(), key=lambda kv: str(kv[0]))
    ]

    every_trial = [trial for trials in trials_by_insight.values() for trial in trials]
    stats = scoring.correlation(points)
    families = scoring.by_perturbation(every_trial)
    table = scoring.quartile_table(points, trials_by_insight)

    return FragilityEval(
        n_insights=len(points),
        n_trials=len(every_trial),
        perturbations=list(FAMILIES),
        correlation=Correlation(**stats.as_dict()),
        scatter=[
            ScatterPoint(
                insight_id=point.insight_id,
                vacuity=point.vacuity,
                fragility=point.fragility,
                routing=point.routing,
            )
            for point in points
        ],
        by_perturbation=[PerturbationStat(**row) for row in families],
        quartile_table=[QuartileRow(**row) for row in table],
        interpretation=scoring.interpretation(stats, table, families),
    )


@router.get(
    "/fragility",
    response_model=FragilityEval,
    dependencies=[Depends(require_perm(PERM_EVALS_READ))],
    summary="Vacuity vs. measured adversarial fragility",
)
@contract("evals.fragility.json", stage=5)
def fragility_eval(scope: ScopeDep) -> FragilityEval:
    """Spearman rather than Pearson as the headline: the relationship need not
    be linear, only monotonic, and claiming linearity we did not test would be
    the same sin as an unvalidated confidence score.

    `quartile_table` is the cleanest single statement of the thesis — the
    bottom-versus-top quartile flip rate contrast. `interpretation` is the
    sentence we want a judge to read if they read nothing else.

    Reads finished rows. The fuzzer is a separate owner-only job (plan §1.8)
    because ingest has a 30-second budget and this does not fit in it.
    """
    return build_fragility_eval(scope.db, scope.org_id)


@router.post(
    "/fragility/run",
    response_model=FuzzerRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_perm(PERM_CALIBRATION_RUN))],
    summary="Start the adversarial fuzzer as a background job",
)
@limiter.limit(LIMIT_FUZZER_RUN)
@contract("evals.fragility.run.json", stage=5)
def run_fuzzer(
    request: Request,
    response: Response,
    scope: ScopeDep,
    background: BackgroundTasks,
) -> FuzzerRunResponse:
    """Endpoint addition, not in brief §10 — see plan §1.8 for why.

    Ingest must finish in under 30 seconds for the demo; fuzzing 214 insights
    across 5 perturbation families is 1,070 full pipeline passes. Those cannot
    be the same code path, so this is a separate owner-only job run once before
    the demo. The frontend never calls it (`make fuzz` does) and
    `GET /evals/fragility` is unchanged.
    """
    n_insights = len(
        list(scope.db.execute(select(Insight.id).where(Insight.org_id == scope.org_id)).scalars())
    )
    # The job id is this request's handle, not a row: the fuzzer writes
    # `fragility_trials` and `insights.fragility` rather than an
    # `ingest_jobs` row, because it is not an ingest and reusing that table
    # would put a card on the frontend's job list that it cannot retry.
    job_id = uuid.uuid4()
    background.add_task(run_fuzzer_job, scope.org_id)
    log.info(
        "fuzzer_queued",
        job_id=str(job_id),
        org_id=str(scope.org_id),
        insights=n_insights,
    )

    return FuzzerRunResponse(
        job_id=job_id,
        n_insights=n_insights,
        n_trials_planned=n_insights * len(FAMILIES),
        started_at=datetime.now(UTC),
    )


@router.get(
    "/routing",
    response_model=RoutingEval,
    dependencies=[Depends(require_perm(PERM_EVALS_READ))],
    summary="Routing confusion matrix, cascade baselines, documented failures",
)
@contract("evals.routing.json", stage=6, pending=True)
def routing_eval(scope: ScopeDep) -> RoutingEval:
    """`documented_failures` never ships empty.

    The track criteria explicitly reward finding your own failure, and the
    hand-written `note` on each one is worth more than every clean metric above
    it. Ground truth is generator-authored — we wrote the fraud, so we know the
    answer — which is a real limitation and is stated in the writeup rather
    than dressed up as human adjudication.
    """
    raise NotImplementedYet(stage=6)


@router.get(
    "/calibration",
    response_model=CalibrationEval,
    dependencies=[Depends(require_perm(PERM_EVALS_READ))],
    summary="Calibration snapshots and reliability-diagram bins",
)
@contract("evals.calibration.json", stage=9, pending=True)
def calibration_eval(scope: ScopeDep) -> CalibrationEval:
    """Ten equal-width bins. `bins` drives the reliability diagram: `avg_conf`
    on x, `accuracy` on y, with the y=x diagonal as the perfect-calibration
    reference and `count` as point size. Bin counts sum to the total case count,
    which is asserted in the Stage 9 exit tests.
    """
    raise NotImplementedYet(stage=9)
