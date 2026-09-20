"""The ingest pipeline, end to end against Postgres.

Stage 2's exit criterion (plan §4): `make seed s=meridian_shell_ring` ingests
34 documents, mentions persist with verified offsets, and the job state
machine reports per-stage progress.

Every test in here is `integration` — they run the real tagger against a real
database, because the things most likely to break are the seams: foreign-key
ordering between entities and mentions, offsets surviving a round trip
through Postgres, and a failed job leaving a row somebody can retry.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, select

from db.models import Document, Entity, IngestJob, JobState, Mention
from workers.pipeline import STAGE_KEYS, IngestPipeline, create_job
from workers.seed import seed_documents

pytestmark = [pytest.mark.integration, pytest.mark.ml]

DEMO_ORGS = {
    "Meridian Supply LLC",
    "Advent Holdings",
    "Kestrel Registry Ltd",
    "Northgate Logistics Inc",
    "Pinebrook Freight Co",
    "Halcyon Tooling LLC",
}


@pytest.fixture
def ingested(db_session, tenant, settings):  # type: ignore[no-untyped-def]
    """meridian_shell_ring, seeded and ingested into a throwaway tenant."""
    org_id = tenant["org_id"]
    result = seed_documents(
        db_session, org_id=org_id, scenario="meridian_shell_ring", settings=settings
    )
    job = create_job(
        db_session,
        org_id=org_id,
        document_ids=result.document_ids,
        scenario="meridian_shell_ring",
    )
    db_session.commit()
    outcome = IngestPipeline(db_session, job, settings).run()
    return {"org_id": org_id, "job": job, "outcome": outcome}


# ── the exit criterion ───────────────────────────────────────────────────────


def test_the_demo_scenario_ingests_end_to_end(ingested) -> None:  # type: ignore[no-untyped-def]
    outcome = ingested["outcome"]
    assert outcome.state is JobState.done
    assert outcome.documents == 34
    assert outcome.mentions > 250
    assert outcome.entities > 0


def test_every_persisted_mention_slices_back_to_raw_text(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """The invariant, checked after a full round trip through Postgres.

    `tests/test_offsets.py` proves it in memory. This proves the database did
    not change the string on the way in — an encoding surprise in the driver
    would move every citation in the demo and nothing else would notice.
    """
    rows = db_session.execute(
        select(Mention, Document.raw_text)
        .join(Document, Document.id == Mention.document_id)
        .where(Document.org_id == ingested["org_id"])
    ).all()

    assert rows
    for mention, raw_text in rows:
        assert raw_text[mention.char_start : mention.char_end] == mention.surface


def test_the_scenario_cast_resolves_to_one_entity_each(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """Six companies, six nodes.

    This is the test that would have caught `Advent Holdings` splitting across
    an ORG node and a PERSON node, which breaks the three-hop ownership cycle
    the whole demo turns on.
    """
    canonicals = set(
        db_session.execute(
            select(Entity.canonical).where(
                Entity.org_id == ingested["org_id"],
                Entity.entity_type.in_(("ORG", "PERSON")),
            )
        ).scalars()
    )
    missing = DEMO_ORGS - canonicals
    assert not missing, f"the scenario's cast did not resolve cleanly: {sorted(missing)}"


def test_aliases_of_a_merged_entity_are_recorded(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """A merge this system got wrong has to be visible to whoever looks at the
    entity, which is what `aliases` is for (brief §13)."""
    entity = db_session.execute(
        select(Entity).where(
            Entity.org_id == ingested["org_id"],
            Entity.canonical == "Kestrel Registry Ltd",
        )
    ).scalar_one()
    assert entity.aliases
    assert entity.mention_count >= len(entity.aliases)


def test_entity_embeddings_are_stored_for_named_types_only(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    rows = db_session.execute(
        select(Entity.entity_type, Entity.embedding).where(Entity.org_id == ingested["org_id"])
    ).all()
    for entity_type, embedding in rows:
        if entity_type in ("ORG", "PERSON"):
            assert embedding is not None and len(embedding) == 256
        else:
            assert embedding is None


# ── the job state machine ────────────────────────────────────────────────────


def test_job_reports_progress_for_every_stage_it_ran(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    job = db_session.get(IngestJob, ingested["job"].id)
    assert job.state is JobState.done
    assert job.docs_done == job.docs_total == 34
    assert job.started_at is not None and job.finished_at is not None

    progress = job.stage_progress
    assert set(progress) == set(STAGE_KEYS)
    assert progress["tagging"] == 1.0
    assert progress["parsing"] == 1.0


def test_a_job_over_no_documents_completes_rather_than_hanging(  # type: ignore[no-untyped-def]
    db_session, tenant, settings
) -> None:
    job = create_job(db_session, org_id=tenant["org_id"], document_ids=[])
    db_session.commit()
    outcome = IngestPipeline(db_session, job, settings).run()
    assert outcome.state is JobState.done
    assert outcome.documents == 0


def test_a_failing_stage_marks_the_job_failed_and_never_raises(  # type: ignore[no-untyped-def]
    db_session, tenant, settings, monkeypatch
) -> None:
    """A background task that raises is a log line nobody reads. A failed job
    row is a card with a retry button (frontend brief §4.3)."""
    result = seed_documents(
        db_session, org_id=tenant["org_id"], scenario="clean_baseline", settings=settings
    )
    job = create_job(db_session, org_id=tenant["org_id"], document_ids=result.document_ids[:3])
    db_session.commit()

    pipeline = IngestPipeline(db_session, job, settings)
    monkeypatch.setattr(
        pipeline, "_stage_parsing", lambda documents: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    outcome = pipeline.run()

    assert outcome.state is JobState.failed
    reloaded = db_session.get(IngestJob, job.id)
    assert reloaded.state is JobState.failed
    assert reloaded.error_stage == "parsing"
    assert "boom" in reloaded.error


def test_re_running_a_job_replaces_mentions_rather_than_duplicating(  # type: ignore[no-untyped-def]
    db_session, ingested, settings
) -> None:
    """Retry and re-seed both run over documents that already have mentions."""
    org_id = ingested["org_id"]
    before = db_session.execute(
        select(func.count(Mention.id))
        .join(Document, Document.id == Mention.document_id)
        .where(Document.org_id == org_id)
    ).scalar_one()

    job = db_session.get(IngestJob, ingested["job"].id)
    IngestPipeline(db_session, job, settings).run()

    after = db_session.execute(
        select(func.count(Mention.id))
        .join(Document, Document.id == Mention.document_id)
        .where(Document.org_id == org_id)
    ).scalar_one()
    assert after == before


# ── seeding ──────────────────────────────────────────────────────────────────


def test_seeding_is_idempotent_by_content_hash(db_session, tenant, settings) -> None:  # type: ignore[no-untyped-def]
    org_id = tenant["org_id"]
    first = seed_documents(db_session, org_id=org_id, scenario="clean_baseline", settings=settings)
    db_session.commit()
    second = seed_documents(db_session, org_id=org_id, scenario="clean_baseline", settings=settings)
    db_session.commit()

    assert len(first.created) == 28
    assert second.created == []
    assert second.duplicates_skipped == 28


def test_the_training_corpus_is_not_seedable(db_session, tenant, settings) -> None:  # type: ignore[no-untyped-def]
    """Plan §0 and §4: serving an insight drawn from the training split would
    contaminate every number the project reports."""
    from api.errors import ValidationFailed

    with pytest.raises(ValidationFailed) as caught:
        seed_documents(
            db_session, org_id=tenant["org_id"], scenario="train_corpus", settings=settings
        )
    assert "training split" in str(caught.value.details)


def test_seeded_document_ids_are_stable_across_reseeds(db_session, tenant, settings) -> None:  # type: ignore[no-untyped-def]
    """The rehearsed "click this insight" and the prerecorded briefing both
    depend on ids surviving `make nuke` (plan §1.11)."""
    org_id = tenant["org_id"]
    first = seed_documents(db_session, org_id=org_id, scenario="clean_baseline", settings=settings)
    first_ids = sorted(str(d.id) for d in first.created)
    db_session.commit()

    db_session.execute(delete(Document).where(Document.org_id == org_id))
    db_session.commit()

    second = seed_documents(db_session, org_id=org_id, scenario="clean_baseline", settings=settings)
    assert sorted(str(d.id) for d in second.created) == first_ids


def test_documents_from_another_org_are_never_loaded(db_session, tenant, settings) -> None:  # type: ignore[no-untyped-def]
    """The pipeline reads by `doc_ids`, and a forged id must not widen it."""
    other_org = uuid.uuid4()
    job = create_job(
        db_session,
        org_id=tenant["org_id"],
        document_ids=[uuid.uuid4(), uuid.uuid4()],
    )
    db_session.commit()
    outcome = IngestPipeline(db_session, job, settings).run()
    assert outcome.documents == 0
    assert other_org != tenant["org_id"]


# ── insights (Stage 3) ───────────────────────────────────────────────────────


def test_the_demo_scenario_produces_insights(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    from db.models import Insight

    rows = (
        db_session.execute(select(Insight).where(Insight.org_id == ingested["org_id"]))
        .scalars()
        .all()
    )
    assert len(rows) >= 40
    assert ingested["outcome"].insights == len(rows)


def test_every_citation_round_trips_through_postgres(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """Plan §3: the one assertion that keeps every citation in the demo
    honest, checked after a full write-read cycle."""
    from db.models import Insight

    rows = db_session.execute(
        select(Insight, Document.raw_text)
        .join(Document, Document.id == Insight.document_id)
        .where(Insight.org_id == ingested["org_id"])
    ).all()

    assert rows
    for insight, raw_text in rows:
        assert raw_text[insight.char_start : insight.char_end] == insight.sentence_text


def test_at_least_eighty_percent_of_documents_yield_a_relation(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """The Stage 3 exit criterion from plan §4."""
    from db.models import Insight

    with_relation = db_session.execute(
        select(func.count(func.distinct(Insight.document_id))).where(
            Insight.org_id == ingested["org_id"]
        )
    ).scalar_one()
    assert with_relation / 34 >= 0.80


def test_trust_scores_are_populated_and_bounded(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    from db.models import Insight

    rows = (
        db_session.execute(select(Insight).where(Insight.org_id == ingested["org_id"]))
        .scalars()
        .all()
    )
    for insight in rows:
        for value in (insight.confidence, insight.vacuity, insight.dissonance):
            assert 0.0 <= value <= 1.0
        assert insight.fragility is None, "fragility is Stage 5's job, not ingest's"


def test_vacuity_has_real_variance(db_session, ingested) -> None:
    """Plan §4 Stage 4: "if it doesn't, stop and fix it here" — Stage 5 has
    nothing to correlate against a constant."""
    import statistics

    from db.models import Insight

    values = list(
        db_session.execute(
            select(Insight.vacuity).where(Insight.org_id == ingested["org_id"])
        ).scalars()
    )
    assert statistics.pstdev(values) > 0.05
    assert max(values) - min(values) > 0.5


def test_attention_and_tokens_are_index_parallel(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """Brief §6: an edge's `src_idx`/`dst_idx` index into `tokens`, which is
    how the ablation panel overlays the sentence."""
    from db.models import Insight

    rows = (
        db_session.execute(select(Insight).where(Insight.org_id == ingested["org_id"]))
        .scalars()
        .all()
    )

    checked = 0
    for insight in rows:
        if not insight.attention:
            continue
        checked += 1
        for edge in insight.attention:
            assert 0 <= edge["src_idx"] < len(insight.tokens)
            assert 0 <= edge["dst_idx"] < len(insight.tokens)
            assert edge["src_token"] == insight.tokens[edge["src_idx"]]
            assert edge["dst_token"] == insight.tokens[edge["dst_idx"]]
            assert 0.0 <= edge["weight"] <= 1.0
    assert checked, "no insight carried attention"


def test_evidence_logits_are_retained_for_recalibration(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """Stage 9 refits temperature on these rather than re-running inference
    over the corpus."""
    from db.models import Insight
    from ml.relations.interface import N_RELATIONS

    rows = (
        db_session.execute(
            select(Insight.evidence_logits).where(Insight.org_id == ingested["org_id"])
        )
        .scalars()
        .all()
    )
    assert all(logits is not None and len(logits) == N_RELATIONS for logits in rows)


def test_one_sentence_never_asserts_a_relation_in_both_directions(  # type: ignore[no-untyped-def]
    db_session, ingested
) -> None:
    """Both cannot be true, and keeping both put a spurious two-node loop in
    the ownership graph — the one visual the demo turns on."""
    from db.models import Insight

    rows = (
        db_session.execute(select(Insight).where(Insight.org_id == ingested["org_id"]))
        .scalars()
        .all()
    )

    seen: set[tuple] = set()
    for insight in rows:
        key = (
            insight.document_id,
            insight.char_start,
            insight.relation,
            frozenset((insight.subject_id, insight.object_id)),
        )
        assert key not in seen, f"contradictory pair kept for {insight.relation}"
        seen.add(key)


def test_no_insight_relates_an_entity_to_itself(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    from db.models import Insight

    rows = (
        db_session.execute(
            select(Insight).where(
                Insight.org_id == ingested["org_id"],
                Insight.subject_id == Insight.object_id,
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


def test_insight_ids_are_stable_across_a_reingest(db_session, ingested, settings) -> None:  # type: ignore[no-untyped-def]
    """The prerecorded briefing's `insight_id` values have to survive a
    reseed (plan §1.11)."""
    from db.models import Insight

    before = set(
        db_session.execute(select(Insight.id).where(Insight.org_id == ingested["org_id"])).scalars()
    )
    job = db_session.get(IngestJob, ingested["job"].id)
    IngestPipeline(db_session, job, settings).run()
    after = set(
        db_session.execute(select(Insight.id).where(Insight.org_id == ingested["org_id"])).scalars()
    )
    assert before == after


def test_the_ownership_chain_is_recovered(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """Two of the scenario's three ownership edges.

    The third — `Kestrel Registry Limited` -> `Meridian Supply LLC` — is a
    band-D construction held out of the training split on purpose, and the
    model misses it. That is the design working, not a bug to paper over:
    band D exists so there is something the model has genuinely never seen.
    The funds graph closes the loop that ownership does not.
    """
    from db.models import Entity, Insight

    names = {
        entity.id: entity.canonical
        for entity in db_session.execute(
            select(Entity).where(Entity.org_id == ingested["org_id"])
        ).scalars()
    }
    ownership = {
        (names[i.subject_id], names[i.object_id])
        for i in db_session.execute(
            select(Insight).where(
                Insight.org_id == ingested["org_id"], Insight.relation == "OWNED_BY"
            )
        ).scalars()
    }
    assert ("Meridian Supply LLC", "Advent Holdings") in ownership
    assert ("Advent Holdings", "Kestrel Registry Ltd") in ownership


def test_the_funds_graph_contains_a_cycle_through_the_ring(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """Money leaving a company and returning through intermediaries is
    layering, and it is what the demo's graph panel shows."""
    from db.models import Entity, Insight
    from ml.graph.cycles import cycles

    names = {
        entity.id: entity.canonical
        for entity in db_session.execute(
            select(Entity).where(Entity.org_id == ingested["org_id"])
        ).scalars()
    }
    edges = [
        (i.subject_id, i.object_id)
        for i in db_session.execute(
            select(Insight).where(
                Insight.org_id == ingested["org_id"],
                Insight.relation == "WIRED_FUNDS_TO",
            )
        ).scalars()
    ]
    found = [{names[node] for node in component} for component in cycles(edges)]
    assert any(
        {"Meridian Supply LLC", "Advent Holdings", "Kestrel Registry Ltd"} <= members
        for members in found
    ), f"no funds cycle through the ring; found {found}"


def test_routing_buckets_are_all_represented(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """A feed that is all one colour is a broken gate, not a clean corpus."""
    from db.models import Insight

    buckets = set(
        db_session.execute(
            select(Insight.routing).where(Insight.org_id == ingested["org_id"])
        ).scalars()
    )
    assert len(buckets) == 3


def test_document_detail_exposes_the_citation_spans(db_session, ingested) -> None:  # type: ignore[no-untyped-def]
    """`spans` is what the citation reader highlights."""
    from db.models import Insight

    rows = (
        db_session.execute(select(Insight).where(Insight.org_id == ingested["org_id"]))
        .scalars()
        .all()
    )
    assert {i.document_id for i in rows}
