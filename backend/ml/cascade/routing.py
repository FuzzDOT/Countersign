"""The classical routing decision. Brief §9, plan §4 Stage 4.

Every insight gets a bucket from this before the cascade ever considers
calling Nemotron, and for the ~85% that are not escalated this *is* the
answer. So it is a scoring rule that can be read aloud, not a model:

    risk = base(relation)
         + 0.30 if both parties sit inside an ownership or funds cycle
         + 0.15 if the pair also shares a registered address
         + 0.15 if one person signs for both parties
         + 0.10 if the pair is linked by more than one kind of relation
    risk *= 0.5 + 0.5 * confidence

The multiplier is the important line. A claim the model is unsure about is
weaker evidence of wrongdoing, so it should not escalate on the strength of
its relation type alone — and this is the only place that judgement is made,
rather than being smeared across five call sites.

**Context is computed over the whole job, not per insight.** An ownership
cycle is a property of the graph; a single extracted triple cannot see it.
That is why routing is its own pipeline stage after everything is scored.

The two thresholds live in config and are tuned in Stage 4 against the
definition of done's 8–20% escalation rate (`scripts/tune_gate.py`). They are
not fitted to the ground-truth labels — that would be scoring our own
homework — and `GET /evals/routing` reports the accuracy that results.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from core.config import Settings, get_settings
from db.models import RoutingBucket
from ml.graph.cycles import cycle_members

# Base risk per relation. Ordered by how much an analyst would care about the
# relation *on its own*, with nothing else known: an ownership link between
# two counterparties is structural and hard to explain away; an invoice is
# the most ordinary thing in the corpus.
RELATION_RISK: dict[str, float] = {
    "OWNED_BY": 0.45,
    "WIRED_FUNDS_TO": 0.35,
    "SHARES_ADDRESS_WITH": 0.30,
    "SIGNATORY_OF": 0.25,
    "INVOICED": 0.15,
}
DEFAULT_RISK = 0.15

CYCLE_BONUS = 0.30

# Relations whose cycles mean something. An ownership loop is opacity; a
# funds loop is layering — money leaving a company and returning to it
# through intermediaries. Both are structural findings no single triple can
# see, and in the demo corpus the funds loop is the one that closes: the
# third ownership edge is a band-D construction the model has never seen.
CYCLIC_RELATIONS: tuple[str, ...] = ("OWNED_BY", "WIRED_FUNDS_TO")
SHARED_ADDRESS_BONUS = 0.15
COMMON_SIGNATORY_BONUS = 0.15
MULTI_RELATION_BONUS = 0.10

# Named so the response and the voice layer can quote the reason rather than
# the number. Template-filled, never generated (brief §8).
REASON_TEXT: dict[str, str] = {
    "ownership_cycle": "both parties sit inside an ownership cycle",
    "funds_cycle": "funds move in a loop that returns to its origin",
    "shared_address": "the pair also shares a registered address",
    "common_signatory": "one person signs for both parties",
    "multi_relation": "the pair is linked by more than one kind of relation",
    "low_confidence": "the extraction is weakly supported",
}


@dataclass(frozen=True, slots=True)
class ClaimContext:
    """One scored claim, as the router sees it."""

    subject_id: uuid.UUID
    object_id: uuid.UUID
    relation: str
    confidence: float
    subject_type: str = "ORG"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    bucket: RoutingBucket
    risk: float
    reasons: tuple[str, ...] = field(default=())

    @property
    def explanation(self) -> str:
        """A sentence built from the reason codes. No model writes this."""
        if not self.reasons:
            return "Routine activity with no corroborating risk signal."
        parts = [REASON_TEXT[reason] for reason in self.reasons if reason in REASON_TEXT]
        if not parts:
            return "Routine activity with no corroborating risk signal."
        if len(parts) == 1:
            return f"Flagged because {parts[0]}."
        return f"Flagged because {', '.join(parts[:-1])}, and {parts[-1]}."


@dataclass(frozen=True, slots=True)
class GraphContext:
    """Whole-corpus structure the per-claim rule reads."""

    cycles_by_relation: dict[str, frozenset[uuid.UUID]]
    shared_address_pairs: frozenset[frozenset[uuid.UUID]]
    common_signatory_pairs: frozenset[frozenset[uuid.UUID]]
    relation_kinds: dict[frozenset[uuid.UUID], int]

    @property
    def in_cycle(self) -> frozenset[uuid.UUID]:
        return (
            frozenset().union(*self.cycles_by_relation.values())
            if self.cycles_by_relation
            else frozenset()
        )

    @classmethod
    def build(cls, claims: Sequence[ClaimContext]) -> GraphContext:
        loops = {
            relation: frozenset(
                cycle_members(
                    [
                        (claim.subject_id, claim.object_id)
                        for claim in claims
                        if claim.relation == relation
                    ]
                )
            )
            for relation in CYCLIC_RELATIONS
        }
        shared = {
            frozenset((claim.subject_id, claim.object_id))
            for claim in claims
            if claim.relation == "SHARES_ADDRESS_WITH"
        }

        # One person signing for several organizations is the human tell the
        # demo scenario is built around, and it is invisible from any single
        # triple.
        signs_for: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for claim in claims:
            if claim.relation == "SIGNATORY_OF":
                signs_for[claim.subject_id].add(claim.object_id)

        co_signed: set[frozenset[uuid.UUID]] = set()
        for organizations in signs_for.values():
            members = sorted(organizations, key=str)
            for index, left in enumerate(members):
                for right in members[index + 1 :]:
                    co_signed.add(frozenset((left, right)))

        kinds: dict[frozenset[uuid.UUID], set[str]] = defaultdict(set)
        for claim in claims:
            kinds[frozenset((claim.subject_id, claim.object_id))].add(claim.relation)

        return cls(
            cycles_by_relation=loops,
            shared_address_pairs=frozenset(shared),
            common_signatory_pairs=frozenset(co_signed),
            relation_kinds={pair: len(names) for pair, names in kinds.items()},
        )


def score(
    claim: ClaimContext, context: GraphContext, settings: Settings | None = None
) -> RoutingDecision:
    settings = settings or get_settings()
    pair = frozenset((claim.subject_id, claim.object_id))

    risk = RELATION_RISK.get(claim.relation, DEFAULT_RISK)
    reasons: list[str] = []

    # Both endpoints, not either. A person who signs for a company inside a
    # cycle is worth a look; they are not themselves part of the loop, and
    # crediting them with it escalated every signatory in the scenario.
    #
    # The bonus is paid once even if the pair sits in both loops: two names
    # for one structural fact should not double the risk.
    for relation, members in context.cycles_by_relation.items():
        if claim.subject_id in members and claim.object_id in members:
            risk += CYCLE_BONUS
            reasons.append("ownership_cycle" if relation == "OWNED_BY" else "funds_cycle")
            break

    if pair in context.shared_address_pairs and claim.relation != "SHARES_ADDRESS_WITH":
        risk += SHARED_ADDRESS_BONUS
        reasons.append("shared_address")

    if pair in context.common_signatory_pairs:
        risk += COMMON_SIGNATORY_BONUS
        reasons.append("common_signatory")

    if context.relation_kinds.get(pair, 0) > 1:
        risk += MULTI_RELATION_BONUS
        reasons.append("multi_relation")

    risk *= 0.5 + 0.5 * claim.confidence
    risk = min(max(risk, 0.0), 1.0)

    if risk >= settings.routing_escalate_threshold:
        bucket = RoutingBucket.escalate_now
    elif risk >= settings.routing_flag_threshold:
        bucket = RoutingBucket.flag_for_review
    else:
        bucket = RoutingBucket.auto_file

    return RoutingDecision(bucket=bucket, risk=round(risk, 4), reasons=tuple(reasons))


def route_all(
    claims: Sequence[ClaimContext], settings: Settings | None = None
) -> list[RoutingDecision]:
    """Route a whole job's claims, sharing one graph context."""
    context = GraphContext.build(claims)
    return [score(claim, context, settings) for claim in claims]
