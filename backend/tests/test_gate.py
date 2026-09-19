"""The uncertainty gate and the graph assembler. Brief §7, §9.

The gate is the cascade's whole argument — only the insights the classical
model is unsure about are worth an LLM call — so these tests pin *which*
uncertainty counts, and the graph tests pin the two things the canvas can get
wrong in a way nobody notices: a cycle that is really a detour, and a risk
score nobody can reconstruct.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from core.config import get_settings
from db.models import Entity, Insight, RoutingBucket
from ml.cascade.gate import (
    DISSONANCE_THRESHOLD,
    ESCALATION_REVIEW_CONFIDENCE,
    POLICY,
    escalation_rate,
    should_escalate,
)
from ml.graph import analysis


@pytest.fixture
def settings():  # type: ignore[no-untyped-def]
    return get_settings()


def _gate(**kw):  # type: ignore[no-untyped-def]
    defaults = {
        "vacuity": 0.0,
        "dissonance": 0.0,
        "confidence": 0.95,
        "classical": RoutingBucket.auto_file,
    }
    return should_escalate(**{**defaults, **kw})


# ── the gate ─────────────────────────────────────────────────────────────────


def test_a_confident_classical_decision_is_not_escalated() -> None:
    """The ~85% the cascade argument rests on."""
    decision = _gate()
    assert decision.handled_classically
    assert decision.reason == "confident_classical"


def test_high_vacuity_escalates(settings) -> None:  # type: ignore[no-untyped-def]
    decision = _gate(vacuity=settings.vacuity_gate_threshold + 0.01)
    assert decision.escalate
    assert decision.reason == "vacuity"


def test_the_threshold_is_exclusive_of_nothing(settings) -> None:  # type: ignore[no-untyped-def]
    """Exactly at the threshold escalates: the band is `>=`, and a boundary
    that behaved differently from the reported number would make the tuning
    report a lie."""
    assert _gate(vacuity=settings.vacuity_gate_threshold).escalate


def test_conflicting_evidence_escalates_even_at_low_vacuity() -> None:
    """A different failure from vacuity, and routing them identically would
    waste the distinction."""
    decision = _gate(vacuity=0.01, dissonance=DISSONANCE_THRESHOLD + 0.01)
    assert decision.escalate
    assert decision.reason == "dissonance"


def test_a_shaky_classical_escalation_is_checked() -> None:
    """Acting on an escalation we are not sure of is the expensive kind of
    wrong."""
    decision = _gate(
        confidence=ESCALATION_REVIEW_CONFIDENCE - 0.01,
        classical=RoutingBucket.escalate_now,
    )
    assert decision.escalate
    assert decision.reason == "uncertain_escalation"


def test_a_confident_classical_escalation_is_not_second_guessed() -> None:
    decision = _gate(
        confidence=ESCALATION_REVIEW_CONFIDENCE + 0.01,
        classical=RoutingBucket.escalate_now,
    )
    assert decision.handled_classically


def test_the_policy_has_a_name_for_the_eval_page() -> None:
    assert POLICY == "vacuity_gate_v2"


def test_escalation_rate_of_nothing_is_zero() -> None:
    assert escalation_rate([]) == 0.0


def test_escalation_rate_counts_what_it_says() -> None:
    decisions = [_gate(), _gate(vacuity=0.99), _gate(), _gate(vacuity=0.99)]
    assert escalation_rate(decisions) == pytest.approx(0.5)


# ── graph assembly ───────────────────────────────────────────────────────────

ORG = uuid.UUID(int=100)


def _entity(index: int, canonical: str) -> Entity:
    return Entity(
        id=uuid.UUID(int=index),
        org_id=ORG,
        canonical=canonical,
        entity_type="ORG",
        aliases=[],
        mention_count=index,
        first_seen=datetime(2026, 9, 14, tzinfo=UTC),
    )


def _insight(
    subject: int,
    obj: int,
    relation: str,
    *,
    confidence: float = 0.9,
    vacuity: float = 0.1,
    routing: RoutingBucket = RoutingBucket.auto_file,
) -> Insight:
    return Insight(
        id=uuid.uuid4(),
        org_id=ORG,
        document_id=uuid.uuid4(),
        subject_id=uuid.UUID(int=subject),
        object_id=uuid.UUID(int=obj),
        relation=relation,
        char_start=0,
        char_end=10,
        sentence_text="x" * 10,
        confidence=confidence,
        vacuity=vacuity,
        dissonance=0.0,
        routing=routing,
        resolved_by="classical",
        attention=[],
        tokens=[],
    )


def test_edges_group_by_source_target_and_relation() -> None:
    """`weight` is the count of supporting insights and drives stroke width."""
    groups = analysis.group_edges(
        [
            _insight(1, 2, "INVOICED", confidence=0.5),
            _insight(1, 2, "INVOICED", confidence=0.9),
            _insight(1, 2, "WIRED_FUNDS_TO"),
        ]
    )
    invoiced = next(g for g in groups if g.relation == "INVOICED")
    assert invoiced.weight == 2
    # The strongest supporting reading, not the average: an edge asserted
    # once weakly and once firmly is an edge we believe.
    assert invoiced.confidence == 0.9
    assert len(groups) == 2


def test_the_most_severe_routing_wins_for_an_edge() -> None:
    groups = analysis.group_edges(
        [
            _insight(1, 2, "INVOICED", routing=RoutingBucket.auto_file),
            _insight(1, 2, "INVOICED", routing=RoutingBucket.escalate_now),
        ]
    )
    assert groups[0].routing is RoutingBucket.escalate_now


def test_a_three_hop_ownership_loop_is_reported() -> None:
    entities = [_entity(i, f"Org {i}") for i in (1, 2, 3)]
    insights = [
        _insight(1, 2, "OWNED_BY"),
        _insight(2, 3, "OWNED_BY"),
        _insight(3, 1, "OWNED_BY"),
    ]
    view = analysis.build(entities, insights)
    assert len(view.cycles) == 1
    assert view.cycles[0]["length"] == 3
    assert view.cycles[0]["relation"] == "OWNED_BY"


def test_reciprocal_payments_are_not_a_funds_cycle() -> None:
    """Two companies paying each other is ordinary trade. Counting it as
    layering put 42% of the no-fraud corpus into the review queue."""
    entities = [_entity(i, f"Org {i}") for i in (1, 2)]
    insights = [_insight(1, 2, "WIRED_FUNDS_TO"), _insight(2, 1, "WIRED_FUNDS_TO")]
    assert analysis.build(entities, insights).cycles == []


def test_reciprocal_ownership_is_a_cycle() -> None:
    """Two companies that own each other is an anomaly however you look at it."""
    entities = [_entity(i, f"Org {i}") for i in (1, 2)]
    insights = [_insight(1, 2, "OWNED_BY"), _insight(2, 1, "OWNED_BY")]
    assert len(analysis.build(entities, insights).cycles) == 1


def test_a_detour_through_a_ring_is_not_a_second_cycle() -> None:
    """The raw enumeration returns the ring plus every loop that goes the
    long way round it. A canvas that highlights all of them highlights
    nothing."""
    entities = [_entity(i, f"Org {i}") for i in (1, 2, 3, 4)]
    insights = [
        _insight(1, 2, "OWNED_BY"),
        _insight(2, 3, "OWNED_BY"),
        _insight(3, 1, "OWNED_BY"),
        # 1 -> 2 -> 3 -> 1 is the ring; 1 -> 4 -> 2 -> 3 -> 1 is a detour
        # whose node set is a strict superset of it.
        _insight(1, 4, "OWNED_BY"),
        _insight(4, 2, "OWNED_BY"),
    ]
    cycles = analysis.build(entities, insights).cycles
    assert len(cycles) == 1
    assert cycles[0]["length"] == 3


def test_node_risk_is_bounded_and_rises_with_cycle_membership() -> None:
    entities = [_entity(i, f"Org {i}") for i in (1, 2, 3, 4)]
    ring = [
        _insight(1, 2, "OWNED_BY"),
        _insight(2, 3, "OWNED_BY"),
        _insight(3, 1, "OWNED_BY"),
    ]
    outsider = [_insight(4, 1, "INVOICED")]

    view = analysis.build(entities, ring + outsider)
    risk = {node["canonical"]: node["risk"] for node in view.nodes}

    for value in risk.values():
        assert 0.0 <= value <= 1.0
    assert risk["Org 1"] > risk["Org 4"]


def test_nodes_carry_the_flags_the_canvas_renders() -> None:
    entities = [_entity(i, f"Org {i}") for i in (1, 2, 3)]
    insights = [
        _insight(1, 2, "OWNED_BY"),
        _insight(2, 3, "OWNED_BY"),
        _insight(3, 1, "OWNED_BY"),
        _insight(1, 2, "SHARES_ADDRESS_WITH"),
        _insight(1, 3, "INVOICED", vacuity=0.9),
    ]
    flags = {
        node["canonical"]: set(node["flags"]) for node in analysis.build(entities, insights).nodes
    }
    assert "ownership_cycle" in flags["Org 1"]
    assert "shared_address" in flags["Org 1"]
    assert "high_vacuity" in flags["Org 1"]


def test_truncation_keeps_the_riskiest_nodes_and_says_so() -> None:
    """A missing node in a fraud ring is a worse failure than a
    "showing 300 of 412" label."""
    entities = [_entity(i, f"Org {i}") for i in range(1, 8)]
    insights = [
        _insight(1, 2, "OWNED_BY"),
        _insight(2, 3, "OWNED_BY"),
        _insight(3, 1, "OWNED_BY"),
        *[_insight(i, 7, "INVOICED") for i in range(4, 7)],
    ]
    view = analysis.build(entities, insights, limit_nodes=3)
    assert view.truncated
    assert len(view.nodes) == 3
    assert {node["canonical"] for node in view.nodes} == {"Org 1", "Org 2", "Org 3"}


def test_edges_to_a_truncated_node_are_dropped() -> None:
    """An edge pointing at a node the response does not contain is an edge
    the canvas cannot draw."""
    entities = [_entity(i, f"Org {i}") for i in range(1, 6)]
    insights = [
        _insight(1, 2, "OWNED_BY"),
        _insight(2, 1, "OWNED_BY"),
        _insight(3, 4, "INVOICED"),
    ]
    view = analysis.build(entities, insights, limit_nodes=2)
    kept = {node["id"] for node in view.nodes}
    for edge in view.edges:
        assert edge["source"] in kept and edge["target"] in kept


def test_a_root_limits_the_graph_to_its_neighbourhood() -> None:
    entities = [_entity(i, f"Org {i}") for i in range(1, 6)]
    insights = [
        _insight(1, 2, "INVOICED"),
        _insight(2, 3, "INVOICED"),
        _insight(3, 4, "INVOICED"),
        _insight(4, 5, "INVOICED"),
    ]
    view = analysis.build(entities, insights, root_entity_id=uuid.UUID(int=1), depth=1)
    assert {node["canonical"] for node in view.nodes} == {"Org 1", "Org 2"}


def test_depth_two_reaches_two_hops() -> None:
    entities = [_entity(i, f"Org {i}") for i in range(1, 6)]
    insights = [
        _insight(1, 2, "INVOICED"),
        _insight(2, 3, "INVOICED"),
        _insight(3, 4, "INVOICED"),
    ]
    view = analysis.build(entities, insights, root_entity_id=uuid.UUID(int=1), depth=2)
    assert {node["canonical"] for node in view.nodes} == {"Org 1", "Org 2", "Org 3"}


def test_edge_ids_are_deterministic() -> None:
    """The frontend keys a React list on them."""
    entities = [_entity(i, f"Org {i}") for i in (1, 2)]
    insights = [_insight(1, 2, "INVOICED")]
    first = analysis.build(entities, insights).edges[0]["id"]
    second = analysis.build(entities, insights).edges[0]["id"]
    assert first == second


def test_an_insight_naming_an_unknown_entity_is_skipped() -> None:
    """A filter that removes a node must not leave a dangling edge."""
    entities = [_entity(1, "Org 1")]
    insights = [_insight(1, 2, "INVOICED")]
    view = analysis.build(entities, insights)
    assert view.edges == []
