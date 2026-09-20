"""Causal explainability. Brief §8.

One endpoint, and it is the demo's strongest thirty seconds: mask a specific
attention edge, re-infer, report what actually changed. Attention heat maps are
the generic move and attention alone is a known-unreliable explanation — this
runs the counterfactual instead.

The handler never learns which relation model is behind it. Both the GAT and
the rule-based fallback implement `RelationModel.infer(graph, edge_mask)`
(plan §1.3), so the hour-9 decision gate cannot take this claim away.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from api.deps import PERM_ABLATION_RUN, ScopeDep, require_perm
from api.errors import InsightNotFound
from api.mock import contract
from api.v1.schemas import (
    AblationDelta,
    AblationRequest,
    AblationResponse,
    AblationState,
)
from core.ratelimit import LIMIT_ABLATION, limiter
from db.models import AblationRun, Insight
from ml.ablation.engine import ablate

router = APIRouter(prefix="/ablation", tags=["ablation"])


@router.post(
    "/insights/{insight_id}",
    response_model=AblationResponse,
    dependencies=[Depends(require_perm(PERM_ABLATION_RUN))],
    summary="Zero attention edges and re-run inference",
)
@limiter.limit(LIMIT_ABLATION)
@contract("ablation.run.json", stage=7)
def ablate_insight(
    request: Request,
    # slowapi writes its `X-RateLimit-*` headers onto this parameter, and
    # raises at call time if the handler doesn't declare it — see the same
    # note on `documents.upload_documents`. Not unused: I called this dead
    # code last turn without checking what the decorator above it needed,
    # and it broke every real call to this endpoint (caught by
    # test_the_endpoint_returns_before_after_and_delta, which is exactly
    # what that test is for).
    response: Response,
    scope: ScopeDep,
    insight_id: uuid.UUID,
    payload: AblationRequest,
) -> AblationResponse:
    """Rate limited to 30/min/user because each call is a real forward pass.

    `interpretation` is template-filled, never generated — the templates live
    in `ml/ablation/templates.py` and the voice layer reads this field verbatim,
    so it is load-bearing for the no-hallucination guarantee.

    `load_bearing` is true iff `|delta.confidence| > 0.10` OR the routing bucket
    changed. When it comes back false, that is a result worth reporting: the
    pretty heat map was lying and we say so.

    The sentence graph is rebuilt from `documents.raw_text` rather than
    cached, which costs a parse and a tagger pass. That is the only way the
    thing being ablated is provably the thing that produced the citation
    (plan §6 records the cost as debt).
    """
    insight = scope.db.execute(
        scope.query(Insight).where(Insight.id == insight_id)
    ).scalar_one_or_none()
    if insight is None:
        raise InsightNotFound(details={"id": str(insight_id)})

    result = ablate(scope.db, insight, payload.masked_edges, mode=payload.mode)
    run = scope.db.execute(
        select(AblationRun)
        .where(AblationRun.insight_id == insight.id)
        .order_by(AblationRun.created_at.desc())
        .limit(1)
    ).scalar_one()
    scope.db.commit()

    return AblationResponse(
        run_id=run.id,
        insight_id=insight.id,
        masked_edges=result.masked_edges,
        before=AblationState(
            confidence=result.before.confidence,
            vacuity=result.before.vacuity,
            routing=result.routing_before,
            relation=result.before.relation,
        ),
        after=AblationState(
            confidence=result.after.confidence,
            vacuity=result.after.vacuity,
            routing=result.routing_after,
            relation=result.after.relation,
        ),
        delta=AblationDelta(
            confidence=result.delta_confidence,
            vacuity=result.delta_vacuity,
            routing_changed=result.routing_changed,
        ),
        load_bearing=result.load_bearing,
        interpretation=result.interpretation,
        latency_ms=result.latency_ms,
    )
