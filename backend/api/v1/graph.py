"""Risk graph. Brief §7.

Cycle detection is server-side and non-negotiable: the frontend renders what is
in `cycles` and must not attempt to find them client-side (frontend brief §9).
A three-hop ownership loop is the single most legible piece of evidence in the
demo, and computing it in a canvas component with 300 nodes would be both slow
and wrong.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import PERM_GRAPH_READ, ScopeDep, require_perm
from api.mock import NotImplementedYet, contract
from api.v1.schemas import EntityDetail, GraphResponse
from db.models import RoutingBucket

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get(
    "",
    response_model=GraphResponse,
    dependencies=[Depends(require_perm(PERM_GRAPH_READ))],
    summary="Nodes, edges and detected ownership cycles",
)
@contract("graph.full.json", stage=4, pending=True)
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
    """`cycles` comes from Tarjan SCC over the OWNED_BY subgraph.

    `truncated` is true when `limit_nodes` cut the result. The frontend must
    surface that rather than silently showing a partial graph — a missing node
    in a fraud ring is a worse failure than a "showing 300 of 412" label.

    `depth` only applies when `root_entity_id` is given; without a root the
    whole org graph is returned up to `limit_nodes`, ordered by node risk so a
    truncated result keeps the interesting nodes.
    """
    raise NotImplementedYet(stage=4)


@router.get(
    "/entities/{entity_id}",
    response_model=EntityDetail,
    dependencies=[Depends(require_perm(PERM_GRAPH_READ))],
    summary="One entity with neighbors, aliases and source documents",
)
@contract("graph.entity.json", stage=4, pending=True)
def get_entity(scope: ScopeDep, entity_id: uuid.UUID) -> EntityDetail:
    """`aliases` is the honest surface of our coreference.

    Entity resolution is char-3gram TF-IDF cosine plus a legal-suffix
    heuristic (plan §1.5), so the alias list is exactly the set of surface
    forms we decided were one company. It will occasionally be wrong, which is
    why it is shown rather than hidden — a judge can see what got merged.
    """
    raise NotImplementedYet(stage=4)
