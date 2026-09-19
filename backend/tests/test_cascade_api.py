"""The cascade end to end, and the endpoints that report it. Brief §9, §10.

`NEMOTRON_API_KEY` is unset in this environment, so the tests split in two:

  * the **degraded** path runs against the real configuration, which is the
    state the demo would be in on a dead upstream, and is therefore worth
    testing exactly as it is;
  * the **live** path runs the same `Cascade` with a client built on
    `httpx.MockTransport`, so escalation, override, agreement and the audit
    log are all exercised without a credential.

What neither proves is that the hosted endpoint likes our request. That is
recorded as outstanding in `docs/STATE.md` rather than implied to be done.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from db.models import Insight, NemotronRun, Resolver, RoutingBucket, RoutingEvalCase, UserRole
from ml.cascade.cascade import Cascade
from ml.cascade.evalset import build as build_eval_set
from ml.cascade.nemotron import NemotronClient
from workers.pipeline import IngestPipeline, create_job
from workers.seed import seed_documents

pytestmark = [pytest.mark.integration, pytest.mark.ml]


def _transport(
    decision: str = "escalate_now", rationale: str = "Ownership loop."
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                f'{{"decision":"{decision}","rationale":"{rationale}",'
                                f'"confidence":0.77}}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 210, "completion_tokens": 44},
            },
        )

    return httpx.MockTransport(handler)


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


# ── the degraded path, which is what this environment actually does ──────────


def test_no_api_key_degrades_instead_of_failing(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """Brief §14: the pipeline must never block on an upstream outage. With
    no key every gated insight falls back to the classical decision."""
    insights = db_session.execute(select(Insight).where(Insight.org_id == ingested)).scalars().all()
    runs = (
        db_session.execute(select(NemotronRun).where(NemotronRun.org_id == ingested))
        .scalars()
        .all()
    )

    assert insights
    assert runs, "the gate flagged nothing, so the degraded path was never taken"
    assert all(run.degraded for run in runs)
    assert all(run.error for run in runs)

    degraded = [i for i in insights if i.degraded]
    assert len(degraded) == len(runs)
    for insight in degraded:
        assert insight.resolved_by is Resolver.classical
        assert insight.routing == insight.classical_routing


def test_a_degraded_run_still_records_the_audit_row(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """A table containing only successful calls lies by omission."""
    run = (
        db_session.execute(select(NemotronRun).where(NemotronRun.org_id == ingested))
        .scalars()
        .first()
    )
    assert run is not None
    assert len(run.prompt_sha) == 64
    assert run.latency_ms == 0
    assert "unavailable" in run.rationale.lower()


def test_the_escalation_rate_is_inside_the_definition_of_done_band(  # type: ignore[no-untyped-def]
    db_session, ingested
) -> None:
    """Brief §16 and plan §4 Stage 6: 8-20%."""
    total = len(
        db_session.execute(select(Insight.id).where(Insight.org_id == ingested)).scalars().all()
    )
    escalated = len(
        db_session.execute(select(NemotronRun.id).where(NemotronRun.org_id == ingested))
        .scalars()
        .all()
    )
    assert total
    assert 0.08 <= escalated / total <= 0.20, f"{escalated}/{total}"


# ── the live path, against a mock upstream ───────────────────────────────────


def _candidates(db_session, org_id, settings):  # type: ignore[no-untyped-def]
    """Rebuild cascade candidates from stored insights."""
    from db.models import Entity
    from ml.cascade.cascade import CascadeCandidate
    from ml.cascade.routing import ClaimContext, GraphContext
    from ml.cascade.routing import score as route_claim

    names = dict(
        db_session.execute(select(Entity.id, Entity.canonical).where(Entity.org_id == org_id))
        .tuples()
        .all()
    )
    insights = db_session.execute(select(Insight).where(Insight.org_id == org_id)).scalars().all()
    claims = [ClaimContext(i.subject_id, i.object_id, i.relation, i.confidence) for i in insights]
    context = GraphContext.build(claims)
    return [
        CascadeCandidate(
            insight_id=i.id,
            subject_id=i.subject_id,
            object_id=i.object_id,
            subject_name=names.get(i.subject_id, "?"),
            object_name=names.get(i.object_id, "?"),
            relation=i.relation,
            confidence=i.confidence,
            vacuity=i.vacuity,
            dissonance=i.dissonance,
            citation=i.sentence_text,
            classical=route_claim(claim, context, settings),
        )
        for i, claim in zip(insights, claims, strict=True)
    ]


def test_a_working_upstream_resolves_gated_insights(db_session, ingested, settings) -> None:  # type: ignore[no-untyped-def]
    live = settings.model_copy(
        update={"nemotron_api_key": "test-key", "nemotron_cache_enabled": False}
    )
    client = NemotronClient(live, transport=_transport())
    report = Cascade(db_session, ingested, live, client=client).run(
        _candidates(db_session, ingested, live)
    )

    assert report.calls_attempted > 0
    assert report.calls_succeeded == report.calls_attempted
    assert report.calls_degraded == 0

    escalated = [o for o in report.outcomes if o.escalated]
    assert escalated
    for outcome in escalated:
        assert outcome.resolved_by is Resolver.nemotron
        assert not outcome.degraded
        assert outcome.nemotron_run is not None
        assert outcome.nemotron_run.input_tokens == 210
        assert outcome.nemotron_run.rationale == "Ownership loop."


def test_the_upstream_can_override_the_classical_decision(db_session, ingested, settings) -> None:  # type: ignore[no-untyped-def]
    """The second opinion has to be able to change the answer, or the
    cascade is a very expensive pass-through."""
    from ml.cascade.cascade import agreement

    live = settings.model_copy(
        update={"nemotron_api_key": "test-key", "nemotron_cache_enabled": False}
    )
    candidates = _candidates(db_session, ingested, live)
    client = NemotronClient(live, transport=_transport(decision="auto_file"))
    report = Cascade(db_session, ingested, live, client=client).run(candidates)

    classical = {c.insight_id: c.classical.bucket for c in candidates}
    numbers = agreement(report.outcomes, classical)
    assert numbers["nemotron_upheld_classical"] + numbers["nemotron_overrode_classical"] > 0
    assert all(
        o.routing is RoutingBucket.auto_file
        for o in report.outcomes
        if o.resolved_by is Resolver.nemotron
    )


def test_a_failing_upstream_degrades_every_gated_insight(db_session, ingested, settings) -> None:  # type: ignore[no-untyped-def]
    live = settings.model_copy(
        update={"nemotron_api_key": "test-key", "nemotron_cache_enabled": False}
    )
    client = NemotronClient(
        live, transport=httpx.MockTransport(lambda request: httpx.Response(503))
    )
    report = Cascade(db_session, ingested, live, client=client).run(
        _candidates(db_session, ingested, live)
    )

    assert report.calls_attempted > 0
    assert report.calls_degraded == report.calls_attempted
    assert all(o.degraded for o in report.outcomes if o.escalated)


def test_force_fail_exercises_the_whole_degraded_path(db_session, ingested, settings) -> None:  # type: ignore[no-untyped-def]
    """The Stage 10 drill, asserted rather than rehearsed."""
    forced = settings.model_copy(
        update={
            "nemotron_api_key": "test-key",
            "nemotron_force_fail": True,
            "nemotron_cache_enabled": False,
        }
    )
    client = NemotronClient(forced, transport=_transport())
    report = Cascade(db_session, ingested, forced, client=client).run(
        _candidates(db_session, ingested, forced)
    )
    assert report.calls_succeeded == 0
    assert report.calls_degraded == report.calls_attempted


# ── the endpoints ────────────────────────────────────────────────────────────


def test_routing_summary_reports_the_gate_and_the_volume(client, tenant_header, ingested) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/routing", headers=tenant_header).json()

    assert body["gate"]["policy"] == "vacuity_gate_v2"
    assert 0.0 < body["gate"]["vacuity_threshold"] < 1.0

    volume = body["volume"]
    assert volume["total_insights"] > 0
    assert (
        volume["handled_classically"] + volume["escalated_to_nemotron"] == volume["total_insights"]
    )
    assert volume["llm_calls_avoided"] == volume["handled_classically"]
    assert 0.08 <= volume["escalation_rate"] <= 0.20

    assert set(body["distribution"]) == {"auto_file", "flag_for_review", "escalate_now"}
    assert sum(body["distribution"].values()) == volume["total_insights"]


def test_the_brief_documented_alias_serves_the_same_payload(
    client, tenant_header, ingested
) -> None:  # type: ignore[no-untyped-def]
    at_root = client.get("/api/v1/routing", headers=tenant_header).json()
    at_summary = client.get("/api/v1/routing/summary", headers=tenant_header).json()
    assert at_root["volume"] == at_summary["volume"]


def test_classical_latency_is_measured_not_invented(client, tenant_header, ingested) -> None:  # type: ignore[no-untyped-def]
    latency = client.get("/api/v1/routing", headers=tenant_header).json()["latency"]
    assert latency["classical_p50_ms"] > 0
    assert latency["classical_p95_ms"] >= latency["classical_p50_ms"]
    # No successful upstream call in this environment, so its percentiles are
    # honestly zero rather than a plausible-looking default.
    assert latency["nemotron_p50_ms"] == 0


def test_the_audit_log_lists_every_call_including_failures(client, tenant_header, ingested) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/routing/runs", headers=tenant_header).json()
    assert body["data"]
    for run in body["data"]:
        assert len(run["prompt_sha"]) == 64
        assert run["decision"] in ("auto_file", "flag_for_review", "escalate_now")
        assert run["rationale"]
    assert any(run["degraded"] for run in body["data"])


def test_the_audit_log_never_contains_document_text(
    client, tenant_header, ingested, db_session
) -> None:  # type: ignore[no-untyped-def]
    """`prompt_sha` rather than the prompt: the digest proves the input
    without putting document text in a table a browser renders."""
    sentences = (
        db_session.execute(select(Insight.sentence_text).where(Insight.org_id == ingested))
        .scalars()
        .all()
    )
    body = client.get("/api/v1/routing/runs?limit=100", headers=tenant_header).json()

    blob = " ".join(run["rationale"] + run["prompt_sha"] for run in body["data"])
    for sentence in sentences:
        assert sentence not in blob


def test_another_org_sees_no_runs(client, ingested, auth_header) -> None:  # type: ignore[no-untyped-def]
    intruder = auth_header(org=uuid.uuid4(), role=UserRole.owner)
    assert client.get("/api/v1/routing/runs", headers=intruder).json()["data"] == []


# ── the routing eval ─────────────────────────────────────────────────────────


@pytest.fixture
def with_eval_set(db_session, ingested, settings):  # type: ignore[no-untyped-def]
    build_eval_set(db_session, ingested, settings)
    db_session.commit()
    return ingested


def test_the_eval_set_is_built_from_gold_relations(db_session, with_eval_set) -> None:  # type: ignore[no-untyped-def]
    cases = (
        db_session.execute(select(RoutingEvalCase).where(RoutingEvalCase.org_id == with_eval_set))
        .scalars()
        .all()
    )
    assert len(cases) >= 30, "plan §4 Stage 6 wants a matrix over at least 30 cases"
    assert all(case.split == "eval" for case in cases)
    assert any(case.is_failure for case in cases)


def test_the_confusion_matrix_totals_the_cases(client, tenant_header, with_eval_set) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/evals/routing", headers=tenant_header).json()
    matrix = body["confusion_matrix"]
    assert matrix["labels"] == ["auto_file", "flag_for_review", "escalate_now"]
    assert sum(sum(row) for row in matrix["matrix"]) == body["n_cases"]
    assert body["n_cases"] >= 30
    assert 0.0 <= body["accuracy"] <= 1.0
    assert 0.0 <= body["macro_f1"] <= 1.0


def test_per_class_support_matches_the_matrix_rows(client, tenant_header, with_eval_set) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/evals/routing", headers=tenant_header).json()
    rows = body["confusion_matrix"]["matrix"]
    for index, entry in enumerate(body["per_class"]):
        assert entry["support"] == sum(rows[index])


def test_documented_failures_never_ship_empty(client, tenant_header, with_eval_set) -> None:
    """Brief §10 is explicit: do not ship this endpoint with an empty array.

    The track criteria reward finding your own failure, and the hand-written
    note is worth more than every clean metric above it.
    """
    body = client.get("/api/v1/evals/routing", headers=tenant_header).json()
    failures = body["documented_failures"]
    assert failures
    for failure in failures:
        assert failure["note"]
        assert failure["ground_truth"] != failure["predicted"]


def test_the_planted_failure_comes_first_with_its_mechanism(
    client, tenant_header, with_eval_set
) -> None:  # type: ignore[no-untyped-def]
    """It must not be pushed off the list by six uninteresting misroutes."""
    failures = client.get("/api/v1/evals/routing", headers=tenant_header).json()[
        "documented_failures"
    ]
    assert "exculpatory context is invisible" in failures[0]["note"]
    assert "Observed in this run" in failures[0]["note"]


def test_the_baseline_is_honest_about_the_arm_it_did_not_run(
    client, tenant_header, with_eval_set
) -> None:  # type: ignore[no-untyped-def]
    """With no key, no arm involves an LLM and all three numbers are the
    classical one. Saying so is the only reading that is not misleading."""
    baseline = client.get("/api/v1/evals/routing", headers=tenant_header).json()["cascade_baseline"]
    assert baseline["cascade_llm_calls"] == 0
    assert baseline["nemotron_on_everything_llm_calls"] == 0
    assert baseline["classical_only_accuracy"] == baseline["cascade_accuracy"]
    assert "not configured" in baseline["interpretation"]


def test_an_org_with_no_cases_says_so(client, auth_header) -> None:  # type: ignore[no-untyped-def]
    body = client.get(
        "/api/v1/evals/routing", headers=auth_header(org=uuid.uuid4(), role=UserRole.owner)
    ).json()
    assert body["n_cases"] == 0
    assert body["documented_failures"] == []
    assert "No labeled cases" in body["cascade_baseline"]["interpretation"]


def test_a_viewer_cannot_read_the_evals(client, tenant, auth_header) -> None:  # type: ignore[no-untyped-def]
    viewer = auth_header(org=tenant["org_id"], role=UserRole.viewer)
    assert client.get("/api/v1/evals/routing", headers=viewer).status_code == 403
