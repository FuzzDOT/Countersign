"""Risk graph. Brief §7.

Cycle detection is server-side and non-negotiable: the frontend renders what is
in `cycles` and must not attempt to find them client-side (frontend brief §9).
A three-hop ownership loop is the single most legible piece of evidence in the
demo, and computing it in a canvas component with 300 nodes would be both slow
and wrong.

The assembly itself lives in `ml/graph/analysis.py`, over plain rows, so the
same code can answer the voice layer's entity questions in Stage 8 and can be
tested without an HTTP client.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from api.deps import PERM_GRAPH_READ, Scope, ScopeDep, require_perm
from api.errors import NotFound
from api.mock import contract
from api.v1.schemas import (
    EntityDetail,
    EntityDetailNode,
    EntityDocument,
    GraphEdge,
    GraphNode,
    GraphResponse,
    NeighborOut,
)
from core.config import get_settings
from db.models import Document, Entity, Insight, Mention, RoutingBucket
from ml.graph import analysis

router = APIRouter(prefix="/graph", tags=["graph"])

# Documents listed on an entity's detail panel. Enough to show provenance,
# few enough that the panel does not become a second feed.
MAX_ENTITY_DOCUMENTS = 8
MAX_NEIGHBORS = 10


def _filtered_insights(
    scope: Scope,
    *,
    relation: list[str] | None,
    min_confidence: float | None,
    routing: list[RoutingBucket] | None,
) -> list[Insight]:
    stmt = scope.query(Insight)
    if relation:
        stmt = stmt.where(Insight.relation.in_(relation))
    if min_confidence is not None:
        stmt = stmt.where(Insight.confidence >= min_confidence)
    if routing:
        stmt = stmt.where(Insight.routing.in_(routing))
    return list(scope.db.execute(stmt).scalars())


def _entities(scope: Scope, entity_type: list[str] | None) -> list[Entity]:
    stmt = scope.query(Entity)
    if entity_type:
        stmt = stmt.where(Entity.entity_type.in_(entity_type))
    else:
        # Only parties are graph nodes. MONEY, DATE and ACCOUNT_REF are
        # entity rows because the brief's tag set makes them so, but a canvas
        # with a node per invoice amount is unreadable and says nothing.
        stmt = stmt.where(Entity.entity_type.in_(("ORG", "PERSON")))
    return list(scope.db.execute(stmt).scalars())


@router.get(
    "",
    response_model=GraphResponse,
    dependencies=[Depends(require_perm(PERM_GRAPH_READ))],
    summary="Nodes, edges and detected ownership cycles",
    description=(
        "Node `risk` is a weighted composite of degree centrality, cycle "
        "participation and mean incident routing severity, normalized to "
        "[0,1]. The weights are configuration, not magic numbers: "
        "`GRAPH_RISK_DEGREE_WEIGHT` (0.25), `GRAPH_RISK_CYCLE_WEIGHT` (0.40) "
        "and `GRAPH_RISK_SEVERITY_WEIGHT` (0.35) by default.\n\n"
        "`cycles` holds *simple* cycles up to length 5, found over the "
        "`OWNED_BY` and `WIRED_FUNDS_TO` subgraphs. Strongly connected "
        "components alone would return the demo corpus's funds subgraph as "
        "one five-node blob and hide the three-hop ring inside it."
    ),
)
@contract("graph.full.json", stage=4)
def get_graph(
    scope: ScopeDep,
    entity_type: Annotated[list[str] | None, Query(max_length=6)] = None,
    relation: Annotated[list[str] | None, Query(max_length=6)] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    routing: Annotated[list[RoutingBucket] | None, Query()] = None,
    root_entity_id: Annotated[uuid.UUID | None, Query()] = None,
    depth: Annotated[int, Query(ge=1, le=3)] = 2,
    limit_nodes: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> GraphResponse:
    """`cycles` comes from simple-cycle enumeration inside each SCC.

    `truncated` is true when `limit_nodes` cut the result. The frontend must
    surface that rather than silently showing a partial graph — a missing node
    in a fraud ring is a worse failure than a "showing 300 of 412" label.

    `depth` only applies when `root_entity_id` is given; without a root the
    whole org graph is returned up to `limit_nodes`, ordered by node risk so a
    truncated result keeps the interesting nodes.
    """
    view = analysis.build(
        _entities(scope, entity_type),
        _filtered_insights(
            scope, relation=relation, min_confidence=min_confidence, routing=routing
        ),
        settings=get_settings(),
        limit_nodes=limit_nodes,
        root_entity_id=root_entity_id,
        depth=depth,
    )

    return GraphResponse(
        nodes=[GraphNode(**node) for node in view.nodes],
        edges=[GraphEdge(**edge) for edge in view.edges],
        cycles=view.cycles,
        truncated=view.truncated,
    )


@router.get(
    "/entities/{entity_id}",
    response_model=EntityDetail,
    dependencies=[Depends(require_perm(PERM_GRAPH_READ))],
    summary="One entity with neighbors, aliases and source documents",
)
@contract("graph.entity.json", stage=4)
def get_entity(scope: ScopeDep, entity_id: uuid.UUID) -> EntityDetail:
    """`aliases` is the honest surface of our coreference.

    Entity resolution is char-3gram TF-IDF cosine plus a legal-suffix
    heuristic (plan §1.5), so the alias list is exactly the set of surface
    forms we decided were one company. It will occasionally be wrong, which is
    why it is shown rather than hidden — a judge can see what got merged.
    """
    entity = scope.get_or_404(Entity, entity_id)

    entities = _entities(scope, None)
    if all(candidate.id != entity.id for candidate in entities):
        # A MONEY or DATE row is a real entity but not a graph node, and its
        # "neighbours" would be meaningless.
        raise NotFound(
            "That entity is a value, not a party in the graph.",
            details={"id": str(entity_id), "entity_type": entity.entity_type},
        )

    insights = _filtered_insights(scope, relation=None, min_confidence=None, routing=None)
    view = analysis.build(entities, insights, settings=get_settings(), limit_nodes=10_000)
    nodes = {node["id"]: node for node in view.nodes}
    groups = analysis.group_edges(insights)

    neighbours: list[NeighborOut] = []
    for group, direction in analysis.neighbours_of(groups, entity.id):
        other = group.target if direction == "out" else group.source
        node = nodes.get(other)
        if node is None:
            continue
        neighbours.append(
            NeighborOut(
                entity=GraphNode(**node),
                relation=group.relation,
                direction=direction,
                confidence=group.confidence,
                insight_ids=group.insight_ids,
            )
        )
    neighbours.sort(key=lambda item: (-item.confidence, item.entity.canonical))

    documents = (
        scope.db.execute(
            select(Document.id, Document.title, func.count(Mention.id))
            .join(Mention, Mention.document_id == Document.id)
            .where(Document.org_id == scope.org_id, Mention.entity_id == entity.id)
            .group_by(Document.id, Document.title)
            .order_by(func.count(Mention.id).desc(), Document.title)
            .limit(MAX_ENTITY_DOCUMENTS)
        )
        .tuples()
        .all()
    )

    insight_count = scope.db.execute(
        select(func.count(Insight.id)).where(
            Insight.org_id == scope.org_id,
            (Insight.subject_id == entity.id) | (Insight.object_id == entity.id),
        )
    ).scalar_one()

    node = nodes[entity.id]
    return EntityDetail(
        entity=EntityDetailNode(**node, first_seen=entity.first_seen),
        aliases=list(entity.aliases or []),
        neighbors=neighbours[:MAX_NEIGHBORS],
        documents=[
            EntityDocument(id=document_id, title=title, mention_count=count)
            for document_id, title, count in documents
        ],
        insight_count=int(insight_count),
    )
