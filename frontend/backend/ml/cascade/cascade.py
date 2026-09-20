"""The cascade: gate, escalate, fall back. Brief §9 and §14.

This is where the project's second claim lives — *the classical model knows
what it does not know, so only the insights it is unsure about are worth an
LLM call.* The gate decides, the client calls, and every outcome is written
to `nemotron_runs` including the failures.

Three behaviours that are the whole point:

**A failed call is not a failed insight.** Timeout, bad key, malformed JSON,
no key at all — every one of them resolves to the classical decision with
`degraded: true`, and the frontend renders "reviewed classically". The
pipeline never 500s because an upstream is down.

**Failures are logged too.** A `nemotron_runs` table containing only
successes is a table that lies by omission. A degraded row carries the
classical decision it fell back to, the error, and `degraded = true`.

**Nothing here writes prose.** The rationale is whatever the model said, or a
template-filled sentence explaining the fallback. No other text in the
response is generated.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger
from db.models import NemotronRun, Resolver, RoutingBucket
from ml.cascade.gate import GateDecision, should_escalate
from ml.cascade.nemotron import (
    NemotronClient,
    NemotronRequest,
    NemotronResult,
    NemotronUnavailable,
)
from ml.cascade.prompt import PromptContext
from ml.cascade.routing import RoutingDecision

log = get_logger(__name__)

# Template-filled, never generated. The voice layer reads insight rationales
# verbatim, so a degraded one has to be a sentence rather than a stack trace.
DEGRADED_RATIONALE = (
    "The second-opinion model was unavailable ({reason}), so this was routed by "
    "the classical decision alone: {explanation}"
)

MAX_NEIGHBOURS = 8


@dataclass(frozen=True, slots=True)
class CascadeCandidate:
    """One scored insight, ready to be routed."""

    insight_id: uuid.UUID
    subject_id: uuid.UUID
    object_id: uuid.UUID
    subject_name: str
    object_name: str
    relation: str
    confidence: float
    vacuity: float
    dissonance: float
    citation: str
    classical: RoutingDecision


@dataclass(frozen=True, slots=True)
class CascadeOutcome:
    """What the cascade decided, and how."""

    insight_id: uuid.UUID
    routing: RoutingBucket
    resolved_by: Resolver
    degraded: bool
    gate: GateDecision
    nemotron_run: NemotronRun | None = None

    @property
    def escalated(self) -> bool:
        return self.gate.escalate


@dataclass(slots=True)
class CascadeReport:
    outcomes: list[CascadeOutcome] = field(default_factory=list)
    calls_attempted: int = 0
    calls_succeeded: int = 0
    calls_degraded: int = 0
    cached: int = 0

    @property
    def escalation_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.escalated) / len(self.outcomes)


def neighbourhood(
    candidates: Sequence[CascadeCandidate], entity_id: uuid.UUID
) -> tuple[tuple[str, str, str], ...]:
    """`(relation, other party, direction)` triples touching an entity.

    Serialized structure rather than raw document text (brief §14). It is
    what lets the model see that two counterparties also share an ownership
    edge, and it is bounded so a hub entity cannot dominate the prompt.
    """
    out: list[tuple[str, str, str]] = []
    for candidate in candidates:
        if candidate.subject_id == entity_id:
            out.append((candidate.relation, candidate.object_name, "out"))
        elif candidate.object_id == entity_id:
            out.append((candidate.relation, candidate.subject_name, "in"))
    # Deduplicated and ordered so the prompt — and therefore its digest and
    # its cache entry — is stable across runs.
    return tuple(sorted(dict.fromkeys(out)))[:MAX_NEIGHBOURS]


class Cascade:
    """Routes a batch of scored insights."""

    def __init__(
        self,
        db: Session,
        org_id: uuid.UUID,
        settings: Settings | None = None,
        *,
        client: NemotronClient | None = None,
    ) -> None:
        self.db = db
        self.org_id = org_id
        self.settings = settings or get_settings()
        self.client = client or NemotronClient(self.settings)

    def run(self, candidates: Sequence[CascadeCandidate]) -> CascadeReport:
        report = CascadeReport()
        gated: list[tuple[CascadeCandidate, GateDecision]] = []

        for candidate in candidates:
            decision = should_escalate(
                vacuity=candidate.vacuity,
                dissonance=candidate.dissonance,
                confidence=candidate.confidence,
                classical=candidate.classical.bucket,
                settings=self.settings,
            )
            gated.append((candidate, decision))

        escalating = [(c, g) for c, g in gated if g.escalate]
        results = self._call_upstream([c for c, _ in escalating])

        by_insight: dict[uuid.UUID, NemotronResult | NemotronUnavailable] = {
            candidate.insight_id: result
            for (candidate, _), result in zip(escalating, results, strict=True)
        }

        for candidate, gate in gated:
            if not gate.escalate:
                report.outcomes.append(
                    CascadeOutcome(
                        insight_id=candidate.insight_id,
                        routing=candidate.classical.bucket,
                        resolved_by=Resolver.classical,
                        degraded=False,
                        gate=gate,
                    )
                )
                continue

            report.calls_attempted += 1
            report.outcomes.append(
                self._resolve(candidate, gate, by_insight[candidate.insight_id], report)
            )

        log.info(
            "cascade_complete",
            org_id=str(self.org_id),
            insights=len(candidates),
            escalated=report.calls_attempted,
            succeeded=report.calls_succeeded,
            degraded=report.calls_degraded,
            cached=report.cached,
            escalation_rate=round(report.escalation_rate, 4),
        )
        return report

    # ── upstream ─────────────────────────────────────────────────────────────

    def _call_upstream(
        self, candidates: Sequence[CascadeCandidate]
    ) -> list[NemotronResult | NemotronUnavailable]:
        if not candidates:
            return []

        requests = [
            NemotronRequest(
                insight_id=candidate.insight_id,
                context=PromptContext(
                    relation=candidate.relation,
                    subject=candidate.subject_name,
                    object=candidate.object_name,
                    confidence=candidate.confidence,
                    vacuity=candidate.vacuity,
                    dissonance=candidate.dissonance,
                    classical_decision=candidate.classical.bucket.value,
                    classical_reasons=candidate.classical.reasons,
                    citation=candidate.citation,
                    neighbourhood=neighbourhood(candidates, candidate.subject_id),
                ),
            )
            for candidate in candidates
        ]

        # The pipeline is sync (plan §1.1) and runs in a worker thread, so
        # there is no loop to join — `asyncio.run` is the right call here and
        # the semaphore inside the client does the concurrency limiting.
        return asyncio.run(self.client.decide_many(requests))

    def _resolve(
        self,
        candidate: CascadeCandidate,
        gate: GateDecision,
        result: NemotronResult | NemotronUnavailable,
        report: CascadeReport,
    ) -> CascadeOutcome:
        if isinstance(result, NemotronUnavailable):
            report.calls_degraded += 1
            run = NemotronRun(
                insight_id=candidate.insight_id,
                org_id=self.org_id,
                prompt_sha=_failed_digest(candidate),
                decision=candidate.classical.bucket,
                rationale=DEGRADED_RATIONALE.format(
                    reason=result.reason,
                    explanation=candidate.classical.explanation[0].lower()
                    + candidate.classical.explanation[1:],
                ),
                latency_ms=0,
                degraded=True,
                error=result.reason[:500],
            )
            self.db.add(run)
            return CascadeOutcome(
                insight_id=candidate.insight_id,
                routing=candidate.classical.bucket,
                resolved_by=Resolver.classical,
                degraded=True,
                gate=gate,
                nemotron_run=run,
            )

        report.calls_succeeded += 1
        if result.cached:
            report.cached += 1

        run = NemotronRun(
            insight_id=candidate.insight_id,
            org_id=self.org_id,
            prompt_sha=result.prompt_sha,
            decision=RoutingBucket(result.decision.decision),
            rationale=result.decision.rationale,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            degraded=False,
        )
        self.db.add(run)
        return CascadeOutcome(
            insight_id=candidate.insight_id,
            routing=RoutingBucket(result.decision.decision),
            resolved_by=Resolver.nemotron,
            degraded=False,
            gate=gate,
            nemotron_run=run,
        )


def _failed_digest(candidate: CascadeCandidate) -> str:
    """A prompt digest for a call that never reached the wire.

    `nemotron_runs.prompt_sha` is NOT NULL and 64 characters, and a degraded
    row still needs to identify which insight it was about. Derived from the
    insight id so it is stable and obviously not a real prompt hash to anyone
    who checks.
    """
    import hashlib

    return hashlib.sha256(f"degraded:{candidate.insight_id}".encode()).hexdigest()


def agreement(
    outcomes: Sequence[CascadeOutcome], classical: dict[uuid.UUID, RoutingBucket]
) -> dict[str, float]:
    """How often the second opinion changed the answer.

    Only over insights that actually reached the upstream: counting the
    classically-handled ones as "upheld" would inflate the agreement rate
    with cases nobody asked about.
    """
    upheld = overrode = 0
    for outcome in outcomes:
        if outcome.resolved_by is not Resolver.nemotron:
            continue
        if outcome.routing == classical.get(outcome.insight_id):
            upheld += 1
        else:
            overrode += 1
    total = upheld + overrode
    return {
        "nemotron_upheld_classical": upheld,
        "nemotron_overrode_classical": overrode,
        "override_rate": round(overrode / total, 4) if total else 0.0,
    }


def distribution(outcomes: Sequence[CascadeOutcome]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for bucket in RoutingBucket:
        counts[bucket.value] = 0
    for outcome in outcomes:
        counts[outcome.routing.value] += 1
    return dict(counts)
