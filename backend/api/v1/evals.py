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

from fastapi import APIRouter, Depends, Request, status

from api.deps import PERM_CALIBRATION_RUN, PERM_EVALS_READ, ScopeDep, require_perm
from api.mock import NotImplementedYet, contract
from api.v1.schemas import (
    CalibrationEval,
    FragilityEval,
    FuzzerRunResponse,
    RoutingEval,
)
from core.ratelimit import LIMIT_FUZZER_RUN, limiter

router = APIRouter(prefix="/evals", tags=["evals"])


@router.get(
    "/fragility",
    response_model=FragilityEval,
    dependencies=[Depends(require_perm(PERM_EVALS_READ))],
    summary="Vacuity vs. measured adversarial fragility",
)
@contract("evals.fragility.json", stage=5, pending=True)
def fragility_eval(scope: ScopeDep) -> FragilityEval:
    """Spearman rather than Pearson as the headline: the relationship need not
    be linear, only monotonic, and claiming linearity we did not test would be
    the same sin as an unvalidated confidence score.

    `quartile_table` is the cleanest single statement of the thesis — the
    bottom-versus-top quartile flip rate contrast. `interpretation` is the
    sentence we want a judge to read if they read nothing else.
    """
    raise NotImplementedYet(stage=5)


@router.post(
    "/fragility/run",
    response_model=FuzzerRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_perm(PERM_CALIBRATION_RUN))],
    summary="Start the adversarial fuzzer as a background job",
)
@limiter.limit(LIMIT_FUZZER_RUN)
@contract("evals.fragility.run.json", stage=5, pending=True)
def run_fuzzer(request: Request, scope: ScopeDep) -> FuzzerRunResponse:
    """Endpoint addition, not in brief §10 — see plan §1.8 for why.

    Ingest must finish in under 30 seconds for the demo; fuzzing 214 insights
    across 5 perturbation families is 1,070 full pipeline passes. Those cannot
    be the same code path, so this is a separate owner-only job run once before
    the demo. The frontend never calls it (`make fuzz` does) and
    `GET /evals/fragility` is unchanged.
    """
    raise NotImplementedYet(stage=5)


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
