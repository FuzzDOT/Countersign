"""Insights. Brief §6.

The filter surface here is the whole feed, and it is final at Stage 1 because
the frontend builds its filter panel and TanStack query keys against it
(frontend brief §7.4). Every parameter maps onto an index that exists
(brief §2) rather than onto a sequential scan discovered later.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import PERM_INSIGHTS_READ, PaginationDep, ScopeDep, require_perm
from api.mock import NotImplementedYet, contract
from api.v1.schemas import InsightDetail, InsightOut, InsightStats, Paginated
from db.models import Resolver, RoutingBucket

router = APIRouter(prefix="/insights", tags=["insights"])

SORT_PATTERN = r"^-?(created_at|confidence|vacuity|fragility)$"


@router.get(
    "",
    response_model=Paginated[InsightOut],
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="The insight feed",
)
@contract("insights.list.json", stage=4, pending=True)
def list_insights(
    scope: ScopeDep,
    pagination: PaginationDep,
    routing: Annotated[
        list[RoutingBucket] | None,
        Query(description="Repeatable. Hits ix_insights_org_id_routing_created_at."),
    ] = None,
    resolved_by: Annotated[Resolver | None, Query()] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    max_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    min_vacuity: Annotated[
        float | None,
        Query(ge=0.0, le=1.0, description="Hits ix_insights_org_id_vacuity."),
    ] = None,
    relation: Annotated[list[str] | None, Query(max_length=6)] = None,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
    document_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Substring over sentence_text. Uses the trgm GIN index."),
    ] = None,
    sort: Annotated[str, Query(pattern=SORT_PATTERN)] = "-created_at",
) -> Paginated[InsightOut]:
    """Keyset pagination, not OFFSET.

    The default sort is `created_at DESC` and new insights arrive during the
    demo, so an offset-paginated second page would silently skip or duplicate
    rows in front of a judge.

    `entity_id` matches either side of the relation (subject or object), which
    is what the graph's "show insights for this node" interaction needs.
    """
    raise NotImplementedYet(stage=4)


@router.get(
    "/stats",
    response_model=InsightStats,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Feed header counters",
)
@contract("insights.stats.json", stage=4, pending=True)
def insight_stats(scope: ScopeDep) -> InsightStats:
    """Single aggregate query, cheap enough to refetch on every feed change.

    Declared before `/{insight_id}` so the literal path wins the route match —
    otherwise "stats" would be parsed as a UUID and 422.
    """
    raise NotImplementedYet(stage=4)


@router.get(
    "/{insight_id}",
    response_model=InsightDetail,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="One insight with attention, ablation history and fragility trials",
)
@contract("insights.detail.json", stage=4, pending=True)
def get_insight(scope: ScopeDep, insight_id: uuid.UUID) -> InsightDetail:
    """Everything the detail sheet and the ablation panel need in one round trip.

    `attention` and `tokens` are index-parallel: an edge's `src_idx` and
    `dst_idx` point into `tokens`, which is how the panel renders the sentence
    with the candidate edges overlaid. `INSIGHT_NOT_FOUND` for another org's
    id, never another org's data.
    """
    raise NotImplementedYet(stage=4)
