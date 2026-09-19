"""Version 1 API surface.

Each pipeline stage owns its own router so that any stage can be demoed
independently if another one breaks (brief §1). `api/main.py` includes only
this aggregator, so adding an endpoint group never touches the app factory.

Every handler carries a `@contract(...)` declaring the build stage that owns it
and the fixture that stands in until then. `tests/test_no_stubs.py` fails the
build if a route owned by a landed stage is still pending, which is what makes
contract-first scaffolding safe rather than a pile of forgotten stubs.

Stage ownership (docs/03-BACKEND-PLAN.md §4):
  1  auth
  2  documents, ingest, ws
  4  insights, graph
  5  evals/fragility
  6  routing, evals/routing
  7  ablation
  8  voice
  9  calibration, evals/calibration
"""

from __future__ import annotations

from fastapi import APIRouter

from api.v1 import (
    ablation,
    auth,
    calibration,
    documents,
    evals,
    graph,
    ingest,
    insights,
    routing,
    voice,
    ws,
)

api_router = APIRouter(prefix="/api/v1")

# Order sets the tag order in /docs. Within insights.py the literal /stats
# route is declared before /{insight_id} so it wins the path match.
api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(ingest.router)
api_router.include_router(insights.router)
api_router.include_router(graph.router)
api_router.include_router(ablation.router)
api_router.include_router(routing.router)
api_router.include_router(evals.router)
api_router.include_router(calibration.router)
api_router.include_router(voice.router)
api_router.include_router(ws.router)

__all__ = ["api_router"]
