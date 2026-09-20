"""Fixture integrity.

The frontend develops against these files for the first several hours, so a
fixture that does not match its response model is a contract break that shows
up as their bug rather than ours. Two independent checks here: every payload
validates through the production model, and the coverage set matches the route
table exactly.
"""

from __future__ import annotations

import itertools
import json

import pytest
from fastapi import FastAPI

from api.mock import declared_fixtures, fixture_path
from core.config import Settings
from data.synth.fixtures import DEMO_SENTENCE, FixtureSet, build_all, spearman
from scripts.gen_fixtures import ERROR_FIXTURES, model_for, validate

BUILT = build_all()


@pytest.mark.parametrize("name", sorted(BUILT))
def test_every_payload_validates_through_its_response_model(name: str) -> None:
    """The anti-drift guarantee, exercised one fixture at a time so a failure
    names the offender."""
    validate(name, BUILT[name])


def test_every_fixture_has_a_declared_model() -> None:
    for name in BUILT:
        assert model_for(name) is not None


def test_coverage_matches_the_route_table(app: FastAPI) -> None:
    declared = declared_fixtures(app) | set(ERROR_FIXTURES)
    assert declared == set(BUILT), (
        "fixture coverage drift.\n"
        f"  declared by routes but not built: {sorted(declared - set(BUILT))}\n"
        f"  built but served by no route:     {sorted(set(BUILT) - declared)}"
    )


@pytest.mark.parametrize("name", sorted(BUILT))
def test_fixtures_on_disk_match_what_the_builder_produces(name: str, settings: Settings) -> None:
    """Catches a fixture edited by hand, or one left stale after a model change.

    Hand-editing a fixture is the failure mode this is really aimed at: it
    works for one afternoon and then silently disagrees with the server.
    """
    path = (
        fixture_path(name, settings)
        if "/" not in name
        else (settings.path(settings.fixtures_dir) / name)
    )
    if not path.exists():
        pytest.skip(f"{name} not yet generated — run `make fixtures`")
    assert json.loads(path.read_text(encoding="utf-8")) == validate(
        name, BUILT[name]
    ), f"{name} on disk differs from the generated payload. Run `make fixtures`."


def test_document_spans_index_into_the_returned_raw_text() -> None:
    """The citation contract, checked on the fixture the frontend actually
    builds the reader against.

    Cross-checked against the **full** insight set rather than
    `insights.list.json`. That fixture is page one of 61 sorted newest-first,
    and document 0's insights land on the last page — so resolving spans
    against the page would match nothing and this test would pass while
    checking nothing at all.
    """
    detail = BUILT["documents.detail.json"]
    raw = detail["raw_text"]
    assert raw

    by_id = {str(i.insight_id): i for i in FixtureSet().insights}
    assert detail["spans"], "the demo document has no citation spans"

    for span in detail["spans"]:
        sliced = raw[span["char_start"] : span["char_end"]]
        assert sliced, f"empty span at {span['char_start']}:{span['char_end']}"

        insight = by_id.get(span["insight_id"])
        assert (
            insight is not None
        ), f"span references insight {span['insight_id']} which does not exist"
        assert sliced == insight.gold.sentence_text, (
            "a span in documents.detail.json does not slice to the sentence its "
            "insight cites — every citation highlight in the UI would be misplaced"
        )
        assert span["relation"] == insight.gold.relation

    assert detail["mentions"], "the demo document has no tagged mentions"
    for mention in detail["mentions"]:
        assert raw[mention["char_start"] : mention["char_end"]] == mention["surface"]


def test_demo_document_contains_both_headline_cases() -> None:
    """Document 0 is what the demo script opens, so it is worth asserting it
    still holds the two sentences the script depends on: the routed-payment
    insight and the planted timing failure."""
    detail = BUILT["documents.detail.json"]
    fixtures = FixtureSet()
    span_ids = {s["insight_id"] for s in detail["spans"]}
    assert str(fixtures.demo_insight.insight_id) in span_ids
    assert str(fixtures.planted_failure.insight_id) in span_ids
    assert "$48,200" in detail["raw_text"]


def test_the_demo_insight_is_unambiguous() -> None:
    """`demo_insight` must resolve to exactly one insight, in document 0.

    The `wired.c1` template is also sampled for ordinary band-C relations, so
    an earlier version of this resolved by template id and picked an unrelated
    counterparty — which silently retargeted the insight detail, the ablation
    run and the spoken answer. All three fixtures are asserted to agree here.
    """
    fixtures = FixtureSet()
    demo = fixtures.demo_insight

    citing = [i for i in fixtures.insights if i.gold.sentence_text == DEMO_SENTENCE]
    assert len(citing) == 1, "the pinned demo sentence is no longer unique"
    assert demo.document.index == 0, "the demo insight drifted out of document 0"
    assert demo.gold.routing == "escalate_now"

    demo_id = str(demo.insight_id)
    assert BUILT["insights.detail.json"]["id"] == demo_id
    assert BUILT["ablation.run.json"]["insight_id"] == demo_id
    assert BUILT["voice.ask.json"]["resolved_insight_id"] == demo_id
    for name in ("insights.detail.json", "voice.ask.json"):
        assert BUILT[name]["citation"]["sentence_text"] == DEMO_SENTENCE


def test_insight_detail_attention_indices_are_in_range() -> None:
    """`src_idx`/`dst_idx` point into `tokens`; the panel renders by index, so
    an out-of-range value is an immediate frontend crash."""
    detail = BUILT["insights.detail.json"]
    tokens = detail["tokens"]
    assert tokens
    for edge in detail["attention"]:
        assert 0 <= edge["src_idx"] < len(tokens)
        assert 0 <= edge["dst_idx"] < len(tokens)
        assert tokens[edge["src_idx"]] == edge["src_token"]
        assert tokens[edge["dst_idx"]] == edge["dst_token"]


def test_graph_fixture_finds_exactly_the_planted_cycle() -> None:
    graph = BUILT["graph.full.json"]
    cycles = graph["cycles"]
    assert len(cycles) == 1, f"expected one ownership cycle, found {len(cycles)}"
    assert cycles[0]["length"] == 3
    assert cycles[0]["relation"] == "OWNED_BY"

    node_ids = {n["id"] for n in graph["nodes"]}
    for node in cycles[0]["node_ids"]:
        assert node in node_ids, "a cycle references a node absent from `nodes`"

    flagged = [n for n in graph["nodes"] if "ownership_cycle" in n["flags"]]
    assert len(flagged) == 3, "cycle membership is not reflected in node flags"


def test_graph_edges_reference_existing_nodes() -> None:
    graph = BUILT["graph.full.json"]
    node_ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids


def test_fragility_fixture_shows_a_quartile_gradient() -> None:
    """The headline claim, asserted on the fixture.

    This is not asserting a *specific* coefficient — the real one arrives in
    Stage 5 and may be lower. It asserts the fixture is internally coherent:
    the top vacuity quartile must flip more than the bottom, or the chart the
    frontend builds is showing something the data does not say.
    """
    fragility = BUILT["evals.fragility.json"]
    table = fragility["quartile_table"]
    assert len(table) == 4
    assert table[-1]["flip_rate"] >= table[0]["flip_rate"]
    assert table[-1]["mean_fragility"] > table[0]["mean_fragility"]
    assert fragility["correlation"]["spearman"] > 0
    # p is floored rather than reported as zero.
    assert fragility["correlation"]["p_value"] > 0


def test_fragility_scatter_is_consistent_with_the_reported_correlation() -> None:
    """Recompute the coefficient from the scatter the frontend plots.

    If these disagree, the trend line and the headline number are describing
    different data — which is the kind of thing a judge notices.
    """
    fragility = BUILT["evals.fragility.json"]
    scatter = fragility["scatter"]
    assert len(scatter) == fragility["n_insights"]
    recomputed = spearman([p["vacuity"] for p in scatter], [p["fragility"] for p in scatter])
    assert abs(recomputed - fragility["correlation"]["spearman"]) < 0.02


def test_fragility_trials_count_matches_the_reported_total() -> None:
    fragility = BUILT["evals.fragility.json"]
    assert fragility["n_trials"] == fragility["n_insights"] * len(fragility["perturbations"])


def test_rename_is_the_harshest_perturbation() -> None:
    """Expected, and worth asserting: if renaming entities to unseen names
    stopped hurting most, the model would be relying on something other than
    structure and the whole held-out-pool design would need revisiting."""
    rows = {r["perturbation"]: r for r in BUILT["evals.fragility.json"]["by_perturbation"]}
    assert rows["rename"]["flip_rate"] == max(r["flip_rate"] for r in rows.values())


def test_routing_eval_documents_at_least_one_failure() -> None:
    """Brief §10: this list never ships empty."""
    routing = BUILT["evals.routing.json"]
    assert routing["documented_failures"]
    failure = routing["documented_failures"][0]
    assert failure["ground_truth"] != failure["predicted"]
    assert len(failure["note"]) > 80, "a documented failure needs a real explanation"


def test_routing_confusion_matrix_sums_to_the_case_count() -> None:
    routing = BUILT["evals.routing.json"]
    matrix = routing["confusion_matrix"]["matrix"]
    assert sum(sum(row) for row in matrix) == routing["n_cases"]
    supports = sum(c["support"] for c in routing["per_class"])
    assert supports == routing["n_cases"]


def test_cascade_baseline_uses_fewer_calls_than_running_the_llm_on_everything() -> None:
    """The cascade argument, in numbers. If this ever fails, the gate is
    escalating everything and the Beyond the Chatbot claim is gone."""
    baseline = BUILT["evals.routing.json"]["cascade_baseline"]
    assert baseline["cascade_llm_calls"] < baseline["nemotron_on_everything_llm_calls"]


def test_calibration_bins_cover_the_unit_interval_and_all_cases() -> None:
    for snapshot in BUILT["evals.calibration.json"]["snapshots"]:
        bins = snapshot["bins"]
        assert len(bins) == 10
        assert bins[0]["bin_lo"] == 0.0
        assert bins[-1]["bin_hi"] == 1.0
        assert sum(b["count"] for b in bins) == len(FixtureSet().insights)


def test_recalibration_improves_ece() -> None:
    recalibrate = BUILT["calibration.recalibrate.json"]
    assert recalibrate["after"]["ece"] < recalibrate["before"]["ece"]
    assert recalibrate["improvement"]["ece_absolute"] < 0


def test_briefing_transcript_segments_are_ordered_and_mapped() -> None:
    """Segment-to-insight mapping is what drives the highlight sync."""
    briefing = BUILT["voice.briefing.json"]
    segments = briefing["transcript"]
    assert segments
    for earlier, later in itertools.pairwise(segments):
        assert earlier["end_ms"] <= later["start_ms"], "transcript segments overlap"
    mapped = [s["insight_id"] for s in segments if s["insight_id"]]
    assert mapped, "no segment maps to an insight — the highlight sync has nothing to drive"
    assert set(mapped) <= set(briefing["insight_ids"])


def test_fallback_briefing_has_the_same_shape_as_a_live_one() -> None:
    """Plan §1.10: degraded mode must not be a different response shape.

    If the fallback dropped the transcript, the transcript-sync moment would
    die exactly when the wifi did — which is when it matters most.
    """
    live, fallback = BUILT["voice.briefing.json"], BUILT["voice.fallback.json"]
    assert set(live) == set(fallback)
    assert fallback["is_fallback"] is True
    assert live["is_fallback"] is False
    assert fallback["transcript"], "the fallback briefing has no transcript"
    assert [s["insight_id"] for s in fallback["transcript"] if s["insight_id"]]


def test_voice_answer_is_grounded_in_a_citation() -> None:
    """No generated text: the spoken answer must carry the source it came
    from, or the no-hallucination claim is unverifiable."""
    ask = BUILT["voice.ask.json"]
    assert ask["citation"] is not None
    assert ask["citation"]["sentence_text"]
    assert ask["resolved_insight_id"]


@pytest.mark.parametrize("name", ERROR_FIXTURES)
def test_error_fixtures_are_well_formed_envelopes(name: str) -> None:
    payload = BUILT[name]
    error = payload["error"]
    assert error["code"] and error["message"] and error["request_id"]
    assert 400 <= error["status"] <= 599
