"""The relation layer: the graph, the GAT, the rules, and the ablation
contract they share. Plan §1.2, §1.3, §4 Stage 3.

The tests that matter most here are the masking ones. Claim 4 — "remove this
syntactic link and watch the conclusion move" — is only true if `edge_mask`
actually changes the computation, in **both** implementations, and if the
edge ids round-trip between the API and the model.
"""

from __future__ import annotations

import pytest
import torch

from ml.relations.gat import GATRelationModel
from ml.relations.graph_builder import (
    EDGE_FEATURE_DIM,
    build_sentence_graph,
    candidate_pairs,
    node_feature_dim,
)
from ml.relations.interface import (
    N_RELATIONS,
    RELATIONS,
    EdgeMask,
    RelationModel,
    edge_id,
    parse_edge_id,
)
from ml.relations.rules import FEATURE_DIM, RuleRelationModel, extract_features, shortest_path
from ml.tagger.infer import get_tagger
from ml.text.parse import parse

SENTENCE = "Meridian Supply LLC wired $48,200 to Advent Holdings on 14 September 2026."


@pytest.fixture(scope="module")
def tagged():  # type: ignore[no-untyped-def]
    doc = parse(SENTENCE)
    return doc, get_tagger().tag(doc)


@pytest.fixture(scope="module")
def graph_and_pair(tagged):  # type: ignore[no-untyped-def]
    doc, tagging = tagged
    encoding = tagging.sentences[0]
    mentions = tagging.mentions_in_sentence(0)
    graph = build_sentence_graph(doc, encoding, mentions)
    pairs = candidate_pairs(mentions, encoding.unit.token_offset)
    assert pairs, "the demo sentence must produce at least one candidate pair"
    lemmas = tuple(token.lemma for token in encoding.unit.tokens)
    return graph, pairs[0], lemmas


# ── edge ids ─────────────────────────────────────────────────────────────────


def test_edge_ids_round_trip() -> None:
    assert parse_edge_id(edge_id(3, 7)) == (3, 7)


def test_a_malformed_edge_id_is_none_rather_than_an_exception() -> None:
    """`masked_edges` is client input. An unrecognized id has to produce
    "that edge is not in this graph" at the endpoint, not a 500 in a parser."""
    for value in ("", "nonsense", "3->", "->7", "a->b", "3-7"):
        assert parse_edge_id(value) is None


# ── the graph ────────────────────────────────────────────────────────────────


def test_graph_has_a_node_per_token_and_a_self_loop_per_node(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, _, _ = graph_and_pair
    assert graph.n_nodes == len(graph.tokens)
    self_loops = sum(
        1
        for index in range(graph.n_edges)
        if graph.edge_index[0][index] == graph.edge_index[1][index]
    )
    assert self_loops == graph.n_nodes


def test_dependency_arcs_are_stored_in_both_directions(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, _, _ = graph_and_pair
    pairs = {
        (int(graph.edge_index[0][i]), int(graph.edge_index[1][i])) for i in range(graph.n_edges)
    }
    arcs = {(a, b) for a, b in pairs if a != b}
    assert arcs
    for source, target in arcs:
        assert (target, source) in arcs


def test_edge_features_are_a_direction_one_hot(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, _, _ = graph_and_pair
    assert graph.edge_features.size(1) == EDGE_FEATURE_DIM
    assert torch.all(graph.edge_features.sum(dim=1) == 1.0)


def test_node_feature_width_matches_the_declared_layout(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """Node features concatenate the *pre-BiLSTM* embedding, not the encoder
    hidden state (Stage 7's residual-connection fix, docs/STATE.md) — a
    residual stream made attention non-causal, and part of the fix was
    building graph nodes from features the BiLSTM hadn't already smoothed
    together. `encoder_dim` (512) was the right width before that; it is not
    now. `pre_bilstm_dim` is."""
    graph, _, _ = graph_and_pair
    width = get_tagger().model.config.pre_bilstm_dim
    assert graph.node_features.size(1) == node_feature_dim(width)


def test_edge_ids_are_parallel_to_the_edge_index(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """The ablation panel names an edge by id; the model masks a column."""
    graph, _, _ = graph_and_pair
    for position, identifier in enumerate(graph.edge_ids):
        parsed = parse_edge_id(identifier)
        assert parsed == (
            int(graph.edge_index[0][position]),
            int(graph.edge_index[1][position]),
        )


def test_candidate_pairs_are_ordered_and_exclude_self_pairs(tagged) -> None:  # type: ignore[no-untyped-def]
    """`OWNED_BY` is directional: "Meridian owns Advent" is a different claim
    from its reverse, so both orderings are offered to the classifier."""
    _, tagging = tagged
    encoding = tagging.sentences[0]
    pairs = candidate_pairs(tagging.mentions_in_sentence(0), encoding.unit.token_offset)
    keys = [(p.subject_key, p.object_key) for p in pairs]
    assert keys
    assert all(a != b for a, b in keys)
    for a, b in keys:
        assert (b, a) in keys


def test_mask_vector_is_none_when_nothing_is_masked(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, _, _ = graph_and_pair
    assert graph.mask_vector(None) is None
    assert graph.mask_vector(EdgeMask.of([])) is None


# ── the GAT ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def gat(graph_and_pair):  # type: ignore[no-untyped-def]
    graph, _, _ = graph_and_pair
    torch.manual_seed(0)
    model = GATRelationModel(node_features=graph.node_features.size(1), hidden=32, heads=4)
    model.eval()
    return model


def test_gat_satisfies_the_relation_model_protocol(gat) -> None:  # type: ignore[no-untyped-def]
    assert isinstance(gat, RelationModel)


def test_attention_is_a_distribution_over_each_nodes_incoming_edges(gat, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, _, _ = graph_and_pair
    encoding = gat.encode(graph)
    weights = encoding.layer_attention[0]

    totals: dict[int, float] = {}
    for position in range(graph.n_edges):
        destination = int(graph.edge_index[1][position])
        totals[destination] = totals.get(destination, 0.0) + float(weights[position])
    for total in totals.values():
        assert total == pytest.approx(1.0, abs=1e-4)


def test_masking_zeroes_the_edge_and_renormalizes(gat, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, pair, _ = graph_and_pair
    before = gat.infer(graph, pair)
    top = before.top_edges(1)[0]

    after = gat.infer(graph, pair, EdgeMask.of([top.edge_id]))
    weights = {edge.edge_id: edge.weight for edge in after.attention}
    assert weights[top.edge_id] == pytest.approx(0.0, abs=1e-6)

    # The other edges into the same node must absorb the freed mass.
    destination = top.dst_idx
    siblings = [
        edge
        for edge in after.attention
        if edge.dst_idx == destination and edge.edge_id != top.edge_id
    ]
    if siblings:
        assert sum(edge.weight for edge in siblings) > 0.0


def test_masking_changes_the_prediction_inputs(gat, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """The whole of claim 4 in one assertion."""
    graph, pair, _ = graph_and_pair
    before = gat.infer(graph, pair)
    masked = [edge.edge_id for edge in before.top_edges(4)]
    after = gat.infer(graph, pair, EdgeMask.of(masked))
    assert not torch.equal(before.logits, after.logits)


def test_uniform_mode_differs_from_zero_mode(gat, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """They ask different questions: "what if this link were absent" versus
    "what if the model could not tell these links apart"."""
    graph, pair, _ = graph_and_pair
    target = [gat.infer(graph, pair).top_edges(1)[0].edge_id]
    zeroed = gat.infer(graph, pair, EdgeMask.of(target, mode="zero"))
    uniform = gat.infer(graph, pair, EdgeMask.of(target, mode="uniform"))
    assert not torch.equal(zeroed.logits, uniform.logits)


def test_masking_an_unknown_edge_id_is_a_no_op(gat, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, pair, _ = graph_and_pair
    before = gat.infer(graph, pair)
    after = gat.infer(graph, pair, EdgeMask.of(["999->1000"]))
    assert torch.equal(before.logits, after.logits)


def test_self_loops_are_not_offered_as_ablation_targets(gat, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """Ablating a self-loop is not a counterfactual anybody can interpret."""
    graph, pair, _ = graph_and_pair
    for edge in gat.infer(graph, pair).attention:
        assert edge.src_idx != edge.dst_idx


def test_output_carries_the_trust_triple_and_raw_logits(gat, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """Stage 9 refits temperature on logged logits rather than re-running
    inference over the corpus, so the raw values have to survive."""
    graph, pair, _ = graph_and_pair
    out = gat.infer(graph, pair)
    assert out.relation in RELATIONS
    assert out.logits.shape == (N_RELATIONS,)
    for value in (out.confidence, out.vacuity, out.dissonance):
        assert 0.0 <= value <= 1.0


# ── the rule model ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def rules():  # type: ignore[no-untyped-def]
    torch.manual_seed(0)
    model = RuleRelationModel(hidden=16)
    model.eval()
    return model


def test_rule_model_satisfies_the_same_protocol(rules) -> None:  # type: ignore[no-untyped-def]
    """Plan §1.3: the hour-9 cut must not cost us claim 4."""
    assert isinstance(rules, RelationModel)


def test_rule_features_have_the_declared_width(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, pair, lemmas = graph_and_pair
    assert extract_features(graph, pair, lemmas).shape == (FEATURE_DIM,)


def test_rule_features_notice_the_verb(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """`wired` is on the path between the two companies, and `wire` is in the
    lexicon — so that feature must be set, or the extractor is reading
    nothing."""
    from ml.relations.rules import VERB_LEXICON

    graph, pair, lemmas = graph_and_pair
    features = extract_features(graph, pair, lemmas)
    assert float(features[VERB_LEXICON.index("wire")]) == 1.0


def test_masking_the_path_changes_the_rule_features(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """The rule model's counterfactual is as real as the GAT's: delete the
    arcs and the path reroutes, lengthens, or disappears."""
    graph, pair, lemmas = graph_and_pair
    before = extract_features(graph, pair, lemmas)
    every_edge = EdgeMask.of(list(graph.edge_ids))
    after = extract_features(graph, pair, lemmas, every_edge)
    assert not torch.equal(before, after)


def test_masking_every_arc_leaves_no_path(graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    graph, pair, _ = graph_and_pair
    blocked = shortest_path(graph, 0, graph.n_nodes - 1, EdgeMask.of(list(graph.edge_ids)))
    assert not blocked.found


def test_rule_model_reports_the_path_as_its_attention(rules, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """Rather than fabricate attention it does not have, the rule model
    reports the arcs it actually used — the same shape, and a counterfactual
    that is just as real."""
    graph, pair, lemmas = graph_and_pair
    out = rules.infer(graph, pair, None, lemmas=lemmas)
    assert out.attention
    assert sum(edge.weight for edge in out.attention) == pytest.approx(1.0, abs=1e-4)


def test_both_models_produce_commensurable_uncertainty(gat, rules, graph_and_pair) -> None:  # type: ignore[no-untyped-def]
    """They share the evidential head, which is what lets the gate, the
    fragility correlation and the calibration curve ignore which is running."""
    graph, pair, lemmas = graph_and_pair
    for out in (gat.infer(graph, pair), rules.infer(graph, pair, None, lemmas=lemmas)):
        assert 0.0 <= out.confidence <= 1.0
        assert 0.0 <= out.vacuity <= 1.0
        assert out.logits.shape == (N_RELATIONS,)


# ── cycles ───────────────────────────────────────────────────────────────────


def test_tarjan_finds_a_three_hop_loop() -> None:
    from ml.graph.cycles import cycles

    found = cycles([("m", "a"), ("a", "k"), ("k", "m"), ("p", "n")])
    assert len(found) == 1
    assert set(found[0]) == {"m", "a", "k"}


def test_a_chain_is_not_a_cycle() -> None:
    from ml.graph.cycles import cycles

    assert cycles([("a", "b"), ("b", "c")]) == []


def test_a_self_loop_is_a_cycle() -> None:
    """A company recorded as its own parent is worth surfacing, not dropping."""
    from ml.graph.cycles import cycles

    assert cycles([("x", "x")]) == [["x"]]


def test_cycle_detection_survives_a_chain_longer_than_the_recursion_limit() -> None:
    from ml.graph.cycles import strongly_connected_components

    chain = [(index, index + 1) for index in range(3_000)]
    assert len(strongly_connected_components(chain)) == 3_001


# ── classical routing ────────────────────────────────────────────────────────


def _claims():  # type: ignore[no-untyped-def]
    import uuid

    from ml.cascade.routing import ClaimContext

    a, b, c = (uuid.UUID(int=n) for n in (1, 2, 3))
    return (
        a,
        b,
        c,
        [
            ClaimContext(a, b, "OWNED_BY", 0.9),
            ClaimContext(b, c, "OWNED_BY", 0.9),
            ClaimContext(c, a, "OWNED_BY", 0.9),
            ClaimContext(a, b, "SHARES_ADDRESS_WITH", 0.9),
        ],
    )


def test_a_relation_inside_an_ownership_cycle_escalates() -> None:
    from ml.cascade.routing import route_all

    _, _, _, claims = _claims()
    decisions = route_all(claims)
    assert decisions[0].bucket.value == "escalate_now"
    assert "ownership_cycle" in decisions[0].reasons


def test_the_same_relation_outside_a_cycle_does_not_escalate() -> None:
    import uuid

    from ml.cascade.routing import ClaimContext, route_all

    lone = [ClaimContext(uuid.UUID(int=9), uuid.UUID(int=10), "INVOICED", 0.9)]
    assert route_all(lone)[0].bucket.value == "auto_file"


def test_low_confidence_weakens_the_claim() -> None:
    """A claim the model is unsure about is weaker evidence of wrongdoing,
    and that judgement is made in exactly one place."""
    import uuid

    from ml.cascade.routing import ClaimContext, GraphContext, score

    pair = (uuid.UUID(int=4), uuid.UUID(int=5))
    context = GraphContext.build([])
    confident = score(ClaimContext(*pair, "OWNED_BY", 0.95), context)
    unsure = score(ClaimContext(*pair, "OWNED_BY", 0.20), context)
    assert confident.risk > unsure.risk


def test_the_cycle_bonus_is_paid_once_even_in_two_loops() -> None:
    """Two names for one structural fact should not double the risk."""
    import uuid

    from ml.cascade.routing import ClaimContext, GraphContext, score

    a, b = uuid.UUID(int=6), uuid.UUID(int=7)
    both = [
        ClaimContext(a, b, "OWNED_BY", 0.9),
        ClaimContext(b, a, "OWNED_BY", 0.9),
        ClaimContext(a, b, "WIRED_FUNDS_TO", 0.9),
        ClaimContext(b, a, "WIRED_FUNDS_TO", 0.9),
    ]
    decision = score(both[0], GraphContext.build(both))
    assert sum(r in ("ownership_cycle", "funds_cycle") for r in decision.reasons) == 1


def test_the_explanation_is_assembled_from_reason_codes() -> None:
    """Template-filled, never generated — the voice layer reads this."""
    from ml.cascade.routing import route_all

    _, _, _, claims = _claims()
    text = route_all(claims)[0].explanation
    assert text.startswith("Flagged because")
    assert text.endswith(".")


def test_a_claim_with_no_signal_says_so_plainly() -> None:
    import uuid

    from ml.cascade.routing import ClaimContext, GraphContext, score

    decision = score(
        ClaimContext(uuid.UUID(int=11), uuid.UUID(int=12), "INVOICED", 0.9),
        GraphContext.build([]),
    )
    assert "no corroborating risk signal" in decision.explanation
