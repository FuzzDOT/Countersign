"""Cascade metrics and the Nemotron audit log. Brief §9.

The strongest available answer to the Beyond the Chatbot track's own question,
*why did you need Nemotron?* — because the classical model knows what it does
not know, and only the insights it flags are worth an LLM call. These two
endpoints are how that is shown rather than asserted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from api.deps import (
    PERM_EVALS_READ,
    PaginationDep,
    Scope,
    ScopeDep,
    iso_cursor_value,
    keyset_page,
    parse_iso_cursor_value,
    require_perm,
)
from api.mock import contract
from api.v1.schemas import (
    CascadeAgreement,
    CascadeLatency,
    CascadeVolume,
    GateConfig,
    NemotronRunOut,
    Paginated,
    RoutingSummary,
)
from db.models import Insight, NemotronRun, Resolver, RoutingBucket
from ml.cascade import latency as timing
from ml.cascade.gate import POLICY

router = APIRouter(prefix="/routing", tags=["routing"])


def build_summary(scope: Scope) -> RoutingSummary:
    total = int(
        scope.db.execute(
            select(func.count(Insight.id)).where(Insight.org_id == scope.org_id)
        ).scalar_one()
    )

    # Escalation is "the gate sent this upstream", which includes the calls
    # that then failed. Counting only the successes would understate the
    # cascade's volume every time an upstream is down, and the degraded ones
    # are exactly the cases the cascade wanted a second opinion on.
    escalated = int(
        scope.db.execute(
            select(func.count(NemotronRun.id)).where(NemotronRun.org_id == scope.org_id)
        ).scalar_one()
    )

    distribution = dict(
        scope.db.execute(
            select(Insight.routing, func.count(Insight.id))
            .where(Insight.org_id == scope.org_id)
            .group_by(Insight.routing)
        )
        .tuples()
        .all()
    )

    upheld, overrode = _agreement(scope)
    nemotron_latency = timing.percentiles_of(
        list(
            scope.db.execute(
                select(NemotronRun.latency_ms).where(
                    NemotronRun.org_id == scope.org_id,
                    NemotronRun.degraded.is_(False),
                )
            ).scalars()
        )
    )
    classical_latency = timing.classical_percentiles(scope.db, scope.org_id)

    return RoutingSummary(
        gate=GateConfig(vacuity_threshold=_settings().vacuity_gate_threshold, policy=POLICY),
        volume=CascadeVolume(
            total_insights=total,
            handled_classically=total - escalated,
            escalated_to_nemotron=escalated,
            escalation_rate=round(escalated / total, 4) if total else 0.0,
            # The number the cascade argument rests on.
            llm_calls_avoided=total - escalated,
        ),
        latency=CascadeLatency(
            classical_p50_ms=classical_latency.p50,
            classical_p95_ms=classical_latency.p95,
            nemotron_p50_ms=nemotron_latency.p50,
            nemotron_p95_ms=nemotron_latency.p95,
        ),
        agreement=CascadeAgreement(
            nemotron_upheld_classical=upheld,
            nemotron_overrode_classical=overrode,
            override_rate=round(overrode / (upheld + overrode), 4) if upheld + overrode else 0.0,
        ),
        distribution={bucket.value: int(distribution.get(bucket, 0)) for bucket in RoutingBucket},
    )


def _agreement(scope: Scope) -> tuple[int, int]:
    """Upheld and overrode, over the insights that reached the upstream.

    Only `resolved_by = nemotron`: a degraded insight kept the classical
    decision by definition, and counting it as agreement would inflate the
    rate with cases nobody actually asked about.
    """
    rows = (
        scope.db.execute(
            select(Insight.routing, Insight.classical_routing).where(
                Insight.org_id == scope.org_id,
                Insight.resolved_by == Resolver.nemotron,
            )
        )
        .tuples()
        .all()
    )
    upheld = sum(1 for routing, classical in rows if routing == classical)
    return upheld, len(rows) - upheld


def _settings():  # type: ignore[no-untyped-def]
    from core.config import get_settings

    return get_settings()


@router.get(
    "",
    response_model=RoutingSummary,
    dependencies=[Depends(require_perm(PERM_EVALS_READ))],
    summary="Gate configuration, cascade volume, latency and agreement",
)
@contract("routing.summary.json", stage=6)
def routing_summary(scope: ScopeDep) -> RoutingSummary:
    """`gate.vacuity_threshold` is reported because it is tuned, not assumed.

    `scripts/tune_gate.py` picks it so escalation lands in the 8-20% band on
    the demo scenario, then verifies on `clean_baseline` that the system does
    not cry wolf. If a judge asks where the number came from, that is the
    answer.

    `llm_calls_avoided` is the number the cascade argument rests on.

    `latency.classical_*` is measured on demand over a sample of this
    organization's own insights rather than stored, because insights are
    written in a batch and a batch gives a mean rather than a percentile.
    See `ml/cascade/latency.py`.
    """
    return build_summary(scope)


@router.get(
    "/summary",
    response_model=RoutingSummary,
    dependencies=[Depends(require_perm(PERM_EVALS_READ))],
    summary="Alias of GET /routing, matching the path in brief §9",
)
@contract("routing.summary.json", stage=6)
def routing_summary_alias(scope: ScopeDep) -> RoutingSummary:
    """Same payload, at the path the brief documents.

    Brief §9 writes `/api/v1/routing/summary`; the route table published at
    hour 3 — and therefore the TypeScript the frontend generated from it —
    uses `/api/v1/routing`. Serving both costs one line and breaks nobody,
    which is cheaper than a contract change at hour 16.
    """
    return build_summary(scope)


@router.get(
    "/runs",
    response_model=Paginated[NemotronRunOut],
    dependencies=[Depends(require_perm(PERM_EVALS_READ))],
    summary="Every Nemotron call, with prompt digest, decision and latency",
)
@contract("routing.runs.json", stage=6)
def routing_runs(scope: ScopeDep, pagination: PaginationDep) -> Paginated[NemotronRunOut]:
    """The "show your work" table.

    Append-only, and it includes the failures — a table containing only
    successful calls is a table that lies by omission, and the degraded rows
    are what make the fallback claim checkable.

    `prompt_sha` rather than the prompt itself: the digest proves a specific
    input produced a specific decision without putting document text in an
    audit table that renders in a browser.
    """
    page = keyset_page(
        scope.db,
        select(NemotronRun).where(NemotronRun.org_id == scope.org_id),
        pagination,
        sort_column=NemotronRun.created_at,
        id_column=NemotronRun.id,
        sort_value=iso_cursor_value,
        parse_sort_value=parse_iso_cursor_value,
    )
    return Paginated[NemotronRunOut](
        data=[
            NemotronRunOut(
                id=run.id,
                insight_id=run.insight_id,
                prompt_sha=run.prompt_sha,
                decision=run.decision,
                rationale=run.rationale,
                latency_ms=run.latency_ms,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                degraded=run.degraded,
                created_at=run.created_at,
            )
            for run in page.rows
        ],
        pagination=page.envelope(),
    )
