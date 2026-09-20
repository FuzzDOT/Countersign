"""Insights. Brief §6.

The filter surface here is the whole feed, and it is final at Stage 1 because
the frontend builds its filter panel and TanStack query keys against it
(frontend brief §7.4). Every parameter maps onto an index that exists
(brief §2) rather than onto a sequential scan discovered later.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from api.deps import (
    PERM_INSIGHTS_READ,
    PaginationDep,
    Scope,
    ScopeDep,
    iso_cursor_value,
    keyset_page,
    parse_iso_cursor_value,
    require_perm,
)
from api.errors import InsightNotFound
from api.mock import contract
from api.v1.schemas import (
    AblationHistoryItem,
    AttentionEdge,
    Citation,
    EntityRef,
    FragilityTrialOut,
    GraphNeighborhood,
    InsightDetail,
    InsightOut,
    InsightStats,
    NemotronInfo,
    Paginated,
    TrustScores,
)
from db.models import (
    AblationRun,
    Document,
    Entity,
    FragilityTrial,
    Insight,
    Resolver,
    RoutingBucket,
)

router = APIRouter(prefix="/insights", tags=["insights"])

SORT_PATTERN = r"^-?(created_at|confidence|vacuity|fragility)$"

# `fragility` is NULL until the fuzzer job has run, and NULL sorts
# unpredictably against a keyset cursor. Coalescing to 0 makes it a plain
# float column for ordering purposes, which puts untested insights at the
# bottom of a descending sort — where they belong, since "not yet tested" is
# not evidence of robustness.
SORT_COLUMNS: dict[str, Any] = {
    "created_at": Insight.created_at,
    "confidence": Insight.confidence,
    "vacuity": Insight.vacuity,
    "fragility": func.coalesce(Insight.fragility, 0.0),
}

# The neighbourhood the detail sheet highlights on the graph canvas.
NEIGHBORHOOD_DEPTH = 2


def _entity_ref(entity: Entity) -> EntityRef:
    return EntityRef(id=entity.id, canonical=entity.canonical, entity_type=entity.entity_type)


def insight_out(insight: Insight, document_title: str) -> InsightOut:
    """One row into the feed's response object.

    `attention_available` rather than the attention itself: the feed renders
    a hundred of these and the attention payload is the bulk of the row.
    """
    return InsightOut(
        id=insight.id,
        relation=insight.relation,
        subject=_entity_ref(insight.subject),
        object=_entity_ref(insight.object),
        citation=Citation(
            document_id=insight.document_id,
            document_title=document_title,
            char_start=insight.char_start,
            char_end=insight.char_end,
            sentence_text=insight.sentence_text,
        ),
        trust=TrustScores(
            confidence=insight.confidence,
            vacuity=insight.vacuity,
            dissonance=insight.dissonance,
            fragility=insight.fragility,
        ),
        routing=insight.routing,
        resolved_by=insight.resolved_by,
        nemotron=(
            NemotronInfo(
                run_id=insight.nemotron_run.id,
                rationale=insight.nemotron_run.rationale,
                latency_ms=insight.nemotron_run.latency_ms,
            )
            if insight.nemotron_run is not None
            else None
        ),
        degraded=insight.degraded,
        attention_available=bool(insight.attention),
        created_at=insight.created_at,
    )


def _loaded(stmt: Select[Any]) -> Select[Any]:
    """Eager-load the joins every response needs.

    Without this the feed is one query plus three per row — the N+1 that
    turns a 25-item page into 76 round trips.
    """
    return stmt.options(
        selectinload(Insight.subject),
        selectinload(Insight.object),
        selectinload(Insight.nemotron_run),
    )


def _titles(scope: Scope, insights: Sequence[Insight]) -> dict[uuid.UUID, str]:
    ids = {insight.document_id for insight in insights}
    if not ids:
        return {}
    rows = (
        scope.db.execute(
            select(Document.id, Document.title).where(
                Document.org_id == scope.org_id, Document.id.in_(ids)
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


@router.get(
    "",
    response_model=Paginated[InsightOut],
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="The insight feed",
)
@contract("insights.list.json", stage=4)
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
    stmt = scope.query(Insight)

    if routing:
        stmt = stmt.where(Insight.routing.in_(routing))
    if resolved_by is not None:
        stmt = stmt.where(Insight.resolved_by == resolved_by)
    if min_confidence is not None:
        stmt = stmt.where(Insight.confidence >= min_confidence)
    if max_confidence is not None:
        stmt = stmt.where(Insight.confidence <= max_confidence)
    if min_vacuity is not None:
        stmt = stmt.where(Insight.vacuity >= min_vacuity)
    if relation:
        stmt = stmt.where(Insight.relation.in_(relation))
    if entity_id is not None:
        stmt = stmt.where((Insight.subject_id == entity_id) | (Insight.object_id == entity_id))
    if document_id is not None:
        stmt = stmt.where(Insight.document_id == document_id)
    if q:
        # `ilike` over the pg_trgm GIN index (brief §2). Escaped so a `%` in
        # a search box is a literal percent rather than a full scan.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Insight.sentence_text.ilike(f"%{escaped}%", escape="\\"))

    descending = sort.startswith("-")
    field = sort.lstrip("-")
    column = SORT_COLUMNS[field]

    page = keyset_page(
        scope.db,
        _loaded(stmt),
        pagination,
        sort_column=column,
        id_column=Insight.id,
        sort_value=iso_cursor_value if field == "created_at" else repr,
        parse_sort_value=parse_iso_cursor_value if field == "created_at" else float,
        descending=descending,
        value_of=None if field == "created_at" else _numeric_sort_value(field),
    )

    titles = _titles(scope, page.rows)
    return Paginated[InsightOut](
        data=[insight_out(row, titles.get(row.document_id, "")) for row in page.rows],
        pagination=page.envelope(),
    )


def _numeric_sort_value(field: str):  # type: ignore[no-untyped-def]
    def read(row: Insight) -> float:
        value = getattr(row, field)
        return float(value) if value is not None else 0.0

    return read


@router.get(
    "/stats",
    response_model=InsightStats,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Feed header counters",
)
@contract("insights.stats.json", stage=4)
def insight_stats(scope: ScopeDep) -> InsightStats:
    """Single aggregate query, cheap enough to refetch on every feed change.

    Declared before `/{insight_id}` so the literal path wins the route match —
    otherwise "stats" would be parsed as a UUID and 422.
    """
    # "High vacuity" is the same line the cascade gate uses, so the feed's
    # counter and the gate cannot drift apart.
    threshold = _vacuity_threshold()

    row = scope.db.execute(
        select(
            func.count(Insight.id),
            func.coalesce(func.avg(Insight.confidence), 0.0),
            func.count(Insight.id).filter(Insight.vacuity >= threshold),
        ).where(Insight.org_id == scope.org_id)
    ).one()
    total, mean_confidence, high_vacuity = row

    by_routing = dict(
        scope.db.execute(
            select(Insight.routing, func.count(Insight.id))
            .where(Insight.org_id == scope.org_id)
            .group_by(Insight.routing)
        )
        .tuples()
        .all()
    )
    by_resolver = dict(
        scope.db.execute(
            select(Insight.resolved_by, func.count(Insight.id))
            .where(Insight.org_id == scope.org_id)
            .group_by(Insight.resolved_by)
        )
        .tuples()
        .all()
    )
    documents = scope.db.execute(
        select(func.count(Document.id)).where(Document.org_id == scope.org_id)
    ).scalar_one()

    return InsightStats(
        total=int(total),
        # Every bucket present even at zero: a missing key makes the frontend
        # render "undefined" where a 0 belongs.
        by_routing={bucket.value: int(by_routing.get(bucket, 0)) for bucket in RoutingBucket},
        by_resolver={resolver.value: int(by_resolver.get(resolver, 0)) for resolver in Resolver},
        mean_confidence=round(float(mean_confidence), 4),
        high_vacuity_count=int(high_vacuity),
        documents_ingested=int(documents),
    )


def _vacuity_threshold() -> float:
    from core.config import get_settings

    return get_settings().vacuity_gate_threshold


@router.get(
    "/{insight_id}",
    response_model=InsightDetail,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="One insight with attention, ablation history and fragility trials",
)
@contract("insights.detail.json", stage=4)
def get_insight(scope: ScopeDep, insight_id: uuid.UUID) -> InsightDetail:
    """Everything the detail sheet and the ablation panel need in one round trip.

    `attention` and `tokens` are index-parallel: an edge's `src_idx` and
    `dst_idx` point into `tokens`, which is how the panel renders the sentence
    with the candidate edges overlaid. `INSIGHT_NOT_FOUND` for another org's
    id, never another org's data.
    """
    insight = scope.db.execute(
        _loaded(scope.query(Insight).where(Insight.id == insight_id))
    ).scalar_one_or_none()
    if insight is None:
        raise InsightNotFound(details={"id": str(insight_id)})

    titles = _titles(scope, [insight])
    base = insight_out(insight, titles.get(insight.document_id, ""))

    history = scope.db.execute(
        select(AblationRun)
        .where(AblationRun.insight_id == insight.id)
        .order_by(AblationRun.created_at.desc())
        .limit(20)
    ).scalars()

    trials = scope.db.execute(
        select(FragilityTrial)
        .where(FragilityTrial.insight_id == insight.id)
        .order_by(FragilityTrial.perturbation, FragilityTrial.variant)
    ).scalars()

    return InsightDetail(
        **base.model_dump(),
        attention=[AttentionEdge(**edge) for edge in insight.attention],
        tokens=list(insight.tokens),
        graph_neighborhood=GraphNeighborhood(
            node_ids=_neighbourhood(scope, insight),
            depth=NEIGHBORHOOD_DEPTH,
        ),
        ablation_history=[
            AblationHistoryItem(
                id=run.id,
                masked_edges=list(run.masked_edges),
                confidence_before=run.confidence_before,
                confidence_after=run.confidence_after,
                load_bearing=run.load_bearing,
                created_at=run.created_at,
            )
            for run in history
        ],
        fragility_trials=[
            FragilityTrialOut(
                perturbation=trial.perturbation,
                label_flipped=trial.label_flipped,
                conf_delta=trial.conf_delta,
                relation_lost=trial.relation_lost,
            )
            for trial in trials
        ],
    )


def _neighbourhood(scope: Scope, insight: Insight) -> list[uuid.UUID]:
    """The entity ids the graph canvas should highlight for this insight.

    Two hops from either argument, computed with one query over the org's
    edges rather than a recursive CTE — at a few hundred insights the round
    trip costs more than the traversal.
    """
    edges = (
        scope.db.execute(
            select(Insight.subject_id, Insight.object_id).where(Insight.org_id == scope.org_id)
        )
        .tuples()
        .all()
    )

    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)

    seen = {insight.subject_id, insight.object_id}
    frontier = set(seen)
    for _ in range(NEIGHBORHOOD_DEPTH - 1):
        nxt: set[uuid.UUID] = set()
        for node in frontier:
            nxt |= adjacency.get(node, set()) - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return sorted(seen, key=str)


__all__ = ["insight_out", "router"]
