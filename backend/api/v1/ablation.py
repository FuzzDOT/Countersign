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

from fastapi import APIRouter, Depends, Request

from api.deps import PERM_ABLATION_RUN, ScopeDep, require_perm
from api.mock import NotImplementedYet, contract
from api.v1.schemas import AblationRequest, AblationResponse
from core.ratelimit import LIMIT_ABLATION, limiter

router = APIRouter(prefix="/ablation", tags=["ablation"])


@router.post(
    "/insights/{insight_id}",
    response_model=AblationResponse,
    dependencies=[Depends(require_perm(PERM_ABLATION_RUN))],
    summary="Zero attention edges and re-run inference",
)
@limiter.limit(LIMIT_ABLATION)
@contract("ablation.run.json", stage=7, pending=True)
def ablate_insight(
    request: Request,
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
    """
    raise NotImplementedYet(stage=7)
