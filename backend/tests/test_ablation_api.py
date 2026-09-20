"""The ablation engine and endpoint. Brief §8, plan §1.3 and §4 Stage 7.

The demo's strongest thirty seconds, and the one claim that needed a design
decision at hour 6 to still be possible at hour 19. Two things get tested
hardest:

**The counterfactual is real.** Masking edges has to change the computation
in a way that shows up in the response — and the honest measurement is that
one arc does not and four do, which is asserted rather than glossed.

**Both models honour the mask.** The endpoint is typed against the
`RelationModel` protocol, so `RELATION_MODEL=rules` must produce a real
counterfactual too. That is what kept claim 4 alive through the hour-9 cut
list (plan §1.3).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db.models import AblationRun, Insight, UserRole
from ml.ablation import templates
from ml.ablation.engine import ablate, rebuild, top_edge, top_edges
from workers.pipeline import IngestPipeline, create_job
from workers.seed import seed_documents

pytestmark = [pytest.mark.integration, pytest.mark.ml]


@pytest.fixture
def ingested(db_session, tenant, settings):  # type: ignore[no-untyped-def]
    org_id = tenant["org_id"]
    result = seed_documents(
        db_session, org_id=org_id, scenario="meridian_shell_ring", settings=settings
    )
    job = create_job(db_session, org_id=org_id, document_ids=result.document_ids)
    db_session.commit()
    IngestPipeline(db_session, job, settings).run()
    return org_id


@pytest.fixture
def insights(db_session, ingested):  # type: ignore[no-untyped-def]
    rows = db_session.execute(
        select(Insight)
        .where(Insight.org_id == ingested)
        .order_by(Insight.confidence.desc())
    ).scalars().all()
    return [row for row in rows if row.attention]


def _top(insight: Insight, count: int = 4) -> list[str]:
    return [
        edge["edge_id"]
        for edge in sorted(insight.attention, key=lambda e: -e["weight"])[:count]
    ]


# ── reconstruction ───────────────────────────────────────────────────────────


def test_an_insight_can_be_rebuilt_into_a_graph(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    """Rebuilt from `raw_text`, not cached, so the thing being ablated is
    provably the thing that produced the citation."""
    rebuilt = rebuild(db_session, insights[0])
    assert rebuilt.graph.n_nodes > 0
    assert rebuilt.graph.n_edges > 0
    assert rebuilt.pair.subject_tokens and rebuilt.pair.object_tokens


def test_the_rebuilt_graph_matches_the_stored_tokens(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    """If these diverged, the edge ids the panel sends back would index a
    different sentence."""
    insight = insights[0]
    rebuilt = rebuild(db_session, insight)
    assert list(rebuilt.graph.tokens) == list(insight.tokens)


def test_stored_edge_ids_all_exist_in_the_rebuilt_graph(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    for insight in insights[:5]:
        known = rebuild(db_session, insight).graph.known_edge_ids()
        for edge in insight.attention:
            assert edge["edge_id"] in known


# ── the counterfactual ───────────────────────────────────────────────────────


def test_masking_edges_changes_the_prediction(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    """Claim 4, as one assertion."""
    moved = 0
    for insight in insights[:12]:
        result = ablate(db_session, insight, _top(insight, 4), persist=False)
        if abs(result.delta_confidence) > 0.01:
            moved += 1
    assert moved >= 6, "masking four top edges moved almost nothing — attention is decoration"


def test_a_four_edge_mask_is_load_bearing_somewhere(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    """Plan §4 Stage 7's exit criterion, at the intervention size that the
    measured report (ml/evals/ablation_report.json) says is meaningful: a
    single arc out of forty is not, and four are."""
    results = [ablate(db_session, i, _top(i, 4), persist=False) for i in insights[:20]]
    assert any(abs(r.delta_confidence) > 0.10 for r in results)
    assert any(r.load_bearing for r in results)


def test_at_least_one_insight_flips_its_routing(db_session, insights) -> None:
    """The plan asks for one demo insight where ablation changes the bucket.

    Picked by measurement rather than by tuning the model to produce one.
    """
    flipped = [
        insight
        for insight in insights[:25]
        if ablate(db_session, insight, _top(insight, 4), persist=False).routing_changed
    ]
    assert flipped, "no insight changed routing under a four-edge mask"


def test_uniform_and_zero_answer_different_questions(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    zeroed = ablate(db_session, insight, _top(insight), mode="zero", persist=False)
    uniform = ablate(db_session, insight, _top(insight), mode="uniform", persist=False)
    assert zeroed.after.confidence != uniform.after.confidence


def test_ablation_is_deterministic(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    """Judges click twice."""
    insight = insights[0]
    first = ablate(db_session, insight, _top(insight), persist=False)
    second = ablate(db_session, insight, _top(insight), persist=False)
    assert first.delta_confidence == second.delta_confidence
    assert first.interpretation == second.interpretation


def test_latency_stays_inside_the_budget(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    """Plan §4 Stage 7: p95 under 300 ms, measured not assumed."""
    latencies = sorted(
        ablate(db_session, i, _top(i), persist=False).latency_ms for i in insights[:20]
    )
    assert latencies[int(0.95 * (len(latencies) - 1))] < 300


def test_an_unknown_edge_is_rejected(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    """Ablating nothing and reporting "not load-bearing" is the most
    misleading possible answer."""
    from api.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        ablate(db_session, insights[0], ["999->1000"], persist=False)


def test_a_run_is_persisted_with_both_states(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    ablate(db_session, insight, _top(insight))
    run = db_session.execute(
        select(AblationRun).where(AblationRun.insight_id == insight.id)
    ).scalars().first()

    assert run is not None
    assert run.mode == "zero"
    assert run.masked_edges == _top(insight)
    assert 0.0 <= run.confidence_before <= 1.0
    assert 0.0 <= run.confidence_after <= 1.0
    assert run.interpretation


# ── the rule-based fallback honours the mask too ─────────────────────────────


def test_the_rule_model_also_produces_a_counterfactual(db_session, insights, settings) -> None:  # type: ignore[no-untyped-def]
    """Plan §1.3: the hour-9 cut list must not cost us claim 4.

    Deleting the arcs from the path search reroutes, lengthens or breaks the
    dependency path, which changes the features — a real counterfactual, not
    a stand-in.
    """
    from ml.relations.infer import get_extractor, reset_extractor
    from ml.relations.interface import EdgeMask

    rules = settings.model_copy(update={"relation_model": "rules"})
    reset_extractor()
    try:
        extractor = get_extractor(rules)
        moved = 0
        for insight in insights[:10]:
            rebuilt = rebuild(db_session, insight, rules)
            every = list(rebuilt.graph.known_edge_ids())

            before = extractor.infer(
                rebuilt.graph, rebuilt.pair, None, lemmas=rebuilt.lemmas
            )
            after = extractor.infer(
                rebuilt.graph, rebuilt.pair, EdgeMask.of(every), lemmas=rebuilt.lemmas
            )
            moved += int(before.confidence != after.confidence)

        # Not every insight: where the two arguments were already
        # disconnected in the dependency graph, destroying the arcs changes
        # nothing because there was no path to destroy. That is a property
        # of the sentence, not a failure of the mask.
        assert moved >= 5, "masking every arc left the rule model unmoved"
    finally:
        reset_extractor()


# ── interpretations ──────────────────────────────────────────────────────────


def test_every_interpretation_is_a_written_sentence(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    for insight in insights[:10]:
        text = ablate(db_session, insight, _top(insight), persist=False).interpretation
        assert text.endswith(".")
        assert text[0].isupper()
        assert len(text) > 40


def test_a_negligible_effect_says_so_plainly() -> None:
    """When the heat map was lying, the pre-written sentence says it was."""
    text = templates.render(
        templates.Verdict(0.001, False, False, 1, "zero"),
        relation_before="INVOICED",
        relation_after="INVOICED",
    )
    assert "changes almost nothing" in text
    assert "attention" in text


def test_a_routing_flip_is_described_as_load_bearing() -> None:
    text = templates.render(
        templates.Verdict(-0.22, True, False, 1, "zero"),
        relation_before="OWNED_BY",
        relation_after="OWNED_BY",
    )
    assert "routing decision" in text


def test_the_wording_distinguishes_the_two_modes() -> None:
    verdict = templates.Verdict(-0.15, False, False, 2, "uniform")
    assert "Flattening attention" in templates.render(
        verdict, relation_before="a", relation_after="a"
    )


# ── the endpoint ─────────────────────────────────────────────────────────────


def test_the_endpoint_returns_before_after_and_delta(client, tenant_header, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    response = client.post(
        f"/api/v1/ablation/insights/{insight.id}",
        headers=tenant_header,
        json={"masked_edges": _top(insight), "mode": "zero"},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["insight_id"] == str(insight.id)
    assert body["masked_edges"] == _top(insight)
    assert body["before"]["relation"] and body["after"]["relation"]
    assert body["delta"]["confidence"] == pytest.approx(
        body["after"]["confidence"] - body["before"]["confidence"], abs=1e-6
    )
    assert isinstance(body["load_bearing"], bool)
    assert body["latency_ms"] >= 0
    assert body["interpretation"]


def test_the_run_appears_in_the_insight_detail(client, tenant_header, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    client.post(
        f"/api/v1/ablation/insights/{insight.id}",
        headers=tenant_header,
        json={"masked_edges": _top(insight)},
    )
    detail = client.get(f"/api/v1/insights/{insight.id}", headers=tenant_header).json()
    assert detail["ablation_history"]
    assert detail["ablation_history"][0]["masked_edges"] == _top(insight)


def test_an_unknown_edge_is_a_422_naming_it(client, tenant_header, insights) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        f"/api/v1/ablation/insights/{insights[0].id}",
        headers=tenant_header,
        json={"masked_edges": ["404->405"]},
    )
    assert response.status_code == 422
    assert "404->405" in str(response.json()["error"]["details"])


def test_an_empty_mask_is_rejected_by_the_schema(client, tenant_header, insights) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        f"/api/v1/ablation/insights/{insights[0].id}",
        headers=tenant_header,
        json={"masked_edges": []},
    )
    assert response.status_code == 422


def test_an_unknown_insight_is_404(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        f"/api/v1/ablation/insights/{uuid.uuid4()}",
        headers=tenant_header,
        json={"masked_edges": ["0->1"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INSIGHT_NOT_FOUND"


def test_another_orgs_insight_is_404_not_403(client, insights, auth_header) -> None:  # type: ignore[no-untyped-def]
    intruder = auth_header(org=uuid.uuid4(), role=UserRole.owner)
    response = client.post(
        f"/api/v1/ablation/insights/{insights[0].id}",
        headers=intruder,
        json={"masked_edges": ["0->1"]},
    )
    assert response.status_code == 404


def test_a_viewer_cannot_run_an_ablation(client, tenant, insights, auth_header) -> None:  # type: ignore[no-untyped-def]
    viewer = auth_header(org=tenant["org_id"], role=UserRole.viewer)
    response = client.post(
        f"/api/v1/ablation/insights/{insights[0].id}",
        headers=viewer,
        json={"masked_edges": ["0->1"]},
    )
    assert response.status_code == 403


def test_top_edge_picks_the_heaviest(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    assert top_edge(insight) == max(insight.attention, key=lambda e: e["weight"])["edge_id"]


def test_top_edges_are_sorted_heaviest_first(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    n = min(3, len(insight.attention))
    picked = top_edges(insight, n=n)
    weights = {e["edge_id"]: e["weight"] for e in insight.attention}
    assert len(picked) == n
    assert [weights[e] for e in picked] == sorted((weights[e] for e in picked), reverse=True)


def test_top_edges_of_one_agrees_with_top_edge(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    assert top_edges(insight, n=1) == [top_edge(insight)]


def test_top_edges_is_capped_at_the_available_edge_count(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    requested = len(insight.attention) + 10
    assert len(top_edges(insight, n=requested)) == len(insight.attention)


def test_top_edges_of_zero_is_empty(db_session, insights) -> None:  # type: ignore[no-untyped-def]
    insight = insights[0]
    assert top_edges(insight, n=0) == []


def test_top_edges_on_an_insight_with_no_attention_is_empty() -> None:
    """A duck-typed stand-in, not `Insight.__new__(Insight)`.

    SQLAlchemy declarative models set up `_sa_instance_state` through their
    instrumented `__init__`; `__new__` skips that, and assigning any mapped
    attribute afterward raises `AttributeError` before the assertion this
    test cares about ever runs. `top_edges`/`top_edge` only read `.attention`,
    so a real ORM instance was never necessary here.
    """
    from types import SimpleNamespace

    bare = SimpleNamespace(attention=[])
    assert top_edges(bare, n=4) == []
    assert top_edge(bare) is None
