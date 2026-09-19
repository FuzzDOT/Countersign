"""The ingest pipeline and its job state machine. Brief §5.

One job, one thread, five stages over §2's `job_state` enum:

  tagging   spaCy tokenization + BiLSTM-CRF entity spans, per document
  parsing   coreference onto `entities`, mentions persisted with exact offsets
  relating  sentence graphs and the relation model over candidate pairs
  scoring   temperature-scaled evidence -> confidence, vacuity, dissonance
  routing   whole-corpus graph context, the classical decision, the Nemotron
            cascade over the insights the gate flags, and the rows themselves

`stage_progress` carries a fraction per stage rather than one overall
percentage, because "relating 56%" is a useful thing to show a judge watching
a progress bar and "48%" is not (plan §2.4). Progress is committed on every
stage transition and every `COMMIT_EVERY_DOCUMENTS` documents — the websocket
polls the row, so an uncommitted update is an update nobody sees.

**Nothing in here re-extracts text.** `documents.raw_text` was written once at
upload and is the coordinate system for every offset this module persists
(plan §3). Mention offsets come from the tokens the tagger predicted over, and
`verify_offsets` re-slices each one against `raw_text` before the flush — the
cheapest possible insurance against the one bug that would invalidate every
citation in the demo.

Failures mark the job `failed` with the stage that broke and never propagate:
a job is a background task, and an exception escaping it would be a log line
nobody reads instead of a job card with a retry button.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core import ids
from core.config import Settings, get_settings
from core.logging import get_logger, new_request_id, set_request_id
from db.models import (
    Document,
    Entity,
    IngestJob,
    Insight,
    JobState,
    Mention,
    NemotronRun,
)
from db.session import db_session
from ml.cascade.cascade import Cascade, CascadeCandidate, CascadeOutcome
from ml.cascade.routing import ClaimContext, GraphContext
from ml.cascade.routing import score as route_claim
from ml.entities.coref import EntityResolver
from ml.evidential import temperature as temperature_scaling
from ml.evidential.uncertainty import trust_of
from ml.relations.infer import DraftInsight, get_extractor
from ml.tagger.infer import DocumentTagging, get_tagger
from ml.text.parse import parse
from ml.text.tokenize import OffsetIntegrityError, TokenizedDoc

log = get_logger(__name__)

# Order matters: it is the order the state machine advances through and the
# key order the frontend renders.
STAGE_KEYS: tuple[str, ...] = ("tagging", "parsing", "relating", "scoring", "routing")

# Brief §5: the socket pushes on every transition and every 5 documents.
COMMIT_EVERY_DOCUMENTS = 5


@dataclass(slots=True)
class DocumentWork:
    """Everything one document accumulates as it moves through the stages."""

    document_id: uuid.UUID
    title: str
    raw_text: str
    tokenized: TokenizedDoc
    tagging: DocumentTagging
    # Span -> entity id, filled while the mentions are persisted. The
    # relation stage needs to know which entity a mention resolved to, and
    # re-resolving the surface would count every mention twice.
    entity_by_span: dict[tuple[int, int], uuid.UUID] = field(default_factory=dict)
    drafts: list[DraftInsight] = field(default_factory=list)
    insight_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(slots=True)
class PipelineResult:
    job_id: uuid.UUID
    documents: int
    mentions: int
    entities: int
    insights: int
    elapsed_ms: int
    state: JobState


class IngestPipeline:
    """Runs one `ingest_jobs` row to completion.

    Constructed with an open session it does not own — `run_job` supplies one
    from `db_session()`, and tests supply their own so they can inspect rows
    without a second connection.
    """

    def __init__(self, db: Session, job: IngestJob, settings: Settings | None = None) -> None:
        self.db = db
        self.job = job
        self.settings = settings or get_settings()
        self.work: list[DocumentWork] = []
        # Entity ids already in the database. Maintained rather than re-queried
        # so `_persist_entities` can run once per document — which it must,
        # because `mentions.entity_id` is a foreign key and the stage commits
        # partway through the loop to drive the progress socket.
        self._persisted_entity_ids: set[uuid.UUID] = set()
        self.resolver = EntityResolver(job.org_id, threshold=self.settings.entity_coref_threshold)
        self.counts: dict[str, int] = {
            "mentions": 0,
            "entities": 0,
            "insights": 0,
            "escalated": 0,
            "degraded": 0,
        }

    # ── the state machine ────────────────────────────────────────────────────

    def stages(self) -> list[tuple[JobState, Callable[[list[Document]], None]]]:
        """The stages this build actually implements.

        A stage appears here only once it does real work. An entry that set
        its progress to 1.0 without running anything would make the progress
        bar lie, and the whole point of per-stage progress is that it does
        not.
        """
        return [
            (JobState.tagging, self._stage_tagging),
            (JobState.parsing, self._stage_parsing),
            (JobState.relating, self._stage_relating),
            (JobState.scoring, self._stage_scoring),
            (JobState.routing, self._stage_routing),
        ]

    def run(self) -> PipelineResult:
        started = time.monotonic()
        documents = self._load_documents()

        self.job.state = JobState.queued
        self.job.started_at = datetime.now(UTC)
        self.job.docs_total = len(documents)
        self.job.docs_done = 0
        self.job.insights_found = 0
        self.job.error = None
        self.job.error_stage = None
        self.job.stage_progress = dict.fromkeys(STAGE_KEYS, 0.0)
        self._commit()

        if not documents:
            return self._finish(started, JobState.done)

        for state, handler in self.stages():
            self.job.state = state
            self._commit()
            try:
                handler(documents)
            except Exception as exc:
                return self._fail(started, state, exc)
            self._set_progress(state.value, 1.0)
            self._commit()

        return self._finish(started, JobState.done)

    def _load_documents(self) -> list[Document]:
        """The job's documents, in a deterministic order.

        Ordered by `received_at` then id rather than by the `doc_ids` array,
        because entity ids are minted from the first surface form the resolver
        sees (plan §1.11) — a different document order would mint different
        ids for the same corpus and break the prerecorded briefing's links.
        """
        if not self.job.doc_ids:
            return []
        stmt = (
            select(Document)
            .where(Document.org_id == self.job.org_id, Document.id.in_(self.job.doc_ids))
            .order_by(Document.received_at, Document.id)
        )
        return list(self.db.execute(stmt).scalars())

    # ── stage 1: tagging ─────────────────────────────────────────────────────

    def _stage_tagging(self, documents: list[Document]) -> None:
        tagger = get_tagger(self.settings)
        total = len(documents)

        for index, document in enumerate(documents, start=1):
            tokenized = parse(document.raw_text)
            tagging = tagger.tag(tokenized)
            self.work.append(
                DocumentWork(
                    document_id=document.id,
                    title=document.title,
                    raw_text=document.raw_text,
                    tokenized=tokenized,
                    tagging=tagging,
                )
            )
            self._advance(index, total, "tagging", docs_done=index)

    # ── stage 2: parsing (coreference + persistence) ─────────────────────────

    def _stage_parsing(self, documents: list[Document]) -> None:
        self._load_existing_entities()
        total = len(self.work)

        # Re-ingest and retry both re-run over documents that may already have
        # mentions. Replacing rather than appending keeps the citation reader
        # from rendering the same highlight twice.
        self.db.execute(
            delete(Mention).where(Mention.document_id.in_([w.document_id for w in self.work]))
        )

        for index, work in enumerate(self.work, start=1):
            self._persist_mentions(work)
            self._advance(index, total, "parsing")

        self._persist_entities()
        self.counts["entities"] = len(self.resolver.records())
        log.info("coref_summary", job_id=str(self.job.id), **self.resolver.summary())

    def _load_existing_entities(self) -> None:
        rows = self.db.execute(select(Entity).where(Entity.org_id == self.job.org_id)).scalars()
        for entity in rows:
            self.resolver.add_existing(
                entity_id=entity.id,
                canonical=entity.canonical,
                entity_type=entity.entity_type,
                aliases=list(entity.aliases or []),
                mention_count=entity.mention_count,
                embedding=list(entity.embedding) if entity.embedding is not None else None,
            )
            self._persisted_entity_ids.add(entity.id)

    def _persist_mentions(self, work: DocumentWork) -> None:
        # Document order, so the resolver meets each entity's most complete
        # surface form first — headers carry the full legal name and the
        # aliases live in the body. Entity ids are minted from the first
        # surface a cluster is seen under (plan §1.11), so this ordering is
        # what makes them reproducible.
        mentions = sorted(work.tagging.mentions, key=lambda m: (m.char_start, m.char_end))

        resolved = []
        for mention in mentions:
            actual = work.raw_text[mention.char_start : mention.char_end]
            if actual != mention.surface:  # pragma: no cover - guarded upstream too
                raise OffsetIntegrityError(
                    f"{work.title}: mention [{mention.char_start}:{mention.char_end}] "
                    f"claims {mention.surface!r} but raw_text holds {actual!r}"
                )
            resolved.append((mention, self.resolver.resolve(mention.surface, mention.entity_type)))

        # Every referenced entity has to exist before its mentions do. The
        # ORM cannot order this for us: `Mention` deliberately holds no
        # relationship to `Entity` (it is reachable only through its
        # document), so the unit of work does not know they are related.
        self._persist_entities()

        for mention, record in resolved:
            work.entity_by_span[(mention.char_start, mention.char_end)] = record.id
            self.db.add(
                Mention(
                    entity_id=record.id,
                    document_id=work.document_id,
                    surface=mention.surface,
                    char_start=mention.char_start,
                    char_end=mention.char_end,
                    tagger_conf=mention.confidence,
                )
            )
            self.counts["mentions"] += 1

    def _persist_entities(self) -> None:
        """Insert entities created since the last call, refresh the rest."""
        for record in self.resolver.dirty_records():
            embedding = record.embedding.tolist() if record.embedding is not None else None

            if record.id in self._persisted_entity_ids:
                entity = self.db.get(Entity, record.id)
                if entity is None:  # pragma: no cover - concurrent delete
                    continue
                entity.canonical = record.canonical
                entity.aliases = record.aliases
                entity.mention_count = record.mention_count
                if embedding is not None:
                    # Mapped[list[float] | None] over pgvector; mypy reads the
                    # descriptor's setter as the column expression type.
                    entity.embedding = embedding  # type: ignore[assignment]
                continue

            self.db.add(
                Entity(
                    id=record.id,
                    org_id=self.job.org_id,
                    canonical=record.canonical,
                    entity_type=record.entity_type,
                    embedding=embedding,
                    aliases=record.aliases,
                    mention_count=record.mention_count,
                )
            )
            self._persisted_entity_ids.add(record.id)

        self.db.flush()

    # ── stage 3: relating ────────────────────────────────────────────────────

    def _stage_relating(self, documents: list[Document]) -> None:
        extractor = get_extractor(self.settings)
        total = len(self.work)

        for index, work in enumerate(self.work, start=1):
            work.drafts = extractor.extract(work.tokenized, work.tagging)
            self._advance(index, total, "relating")

        log.info(
            "relations_extracted",
            job_id=str(self.job.id),
            model=extractor.name,
            drafts=sum(len(w.drafts) for w in self.work),
        )

    # ── stage 4: scoring ─────────────────────────────────────────────────────

    def _stage_scoring(self, documents: list[Document]) -> None:
        """Re-derive the trust triple from temperature-scaled evidence.

        The relation model already produced a trust triple, but at T = 1. If
        Stage 9's recalibration has run for this organization, the live
        temperature is what the insight should be stored with — otherwise a
        recalibrated system would keep writing uncalibrated rows and the ECE
        on the next eval would be unchanged for no visible reason.
        """
        temperature = temperature_scaling.current_temperature(self.db, self.job.org_id)
        total = len(self.work)

        for index, work in enumerate(self.work, start=1):
            if temperature != temperature_scaling.IDENTITY:
                for draft in work.drafts:
                    trust = trust_of(temperature_scaling.rescale(draft.logits, temperature))
                    draft.confidence = trust.confidence
                    draft.vacuity = trust.vacuity
                    draft.dissonance = trust.dissonance
            self._advance(index, total, "scoring")

        if temperature != temperature_scaling.IDENTITY:
            log.info(
                "insights_temperature_scaled",
                job_id=str(self.job.id),
                temperature=round(temperature, 4),
            )

    # ── stage 5: routing and persistence ─────────────────────────────────────

    def _stage_routing(self, documents: list[Document]) -> None:
        """Whole-corpus context, then the classical decision, then the rows.

        Persistence happens here rather than in `relating` because routing
        needs the ownership cycle, which is a property of the full set of
        extracted relations — no single triple can see it.
        """
        pending: list[tuple[DocumentWork, DraftInsight, uuid.UUID, uuid.UUID]] = []
        for work in self.work:
            for draft in work.drafts:
                subject_id = work.entity_by_span.get(
                    (draft.subject.char_start, draft.subject.char_end)
                )
                object_id = work.entity_by_span.get(
                    (draft.object.char_start, draft.object.char_end)
                )
                if subject_id is None or object_id is None:
                    # The mention was tagged but did not survive coreference,
                    # which should not happen — the same mention list feeds
                    # both. Dropping the claim beats writing an insight whose
                    # subject is a dangling id.
                    log.warning(
                        "draft_without_entity",
                        job_id=str(self.job.id),
                        relation=draft.relation,
                        subject=draft.subject.surface,
                    )
                    continue
                if subject_id == object_id:
                    # Coreference merged the two arguments, so the sentence
                    # is asserting a relation between a company and itself.
                    continue
                pending.append((work, draft, subject_id, object_id))

        pending = _keep_one_direction(pending)

        claims = [
            ClaimContext(
                subject_id=subject_id,
                object_id=object_id,
                relation=draft.relation,
                confidence=draft.confidence,
                subject_type=draft.subject.entity_type,
            )
            for _, draft, subject_id, object_id in pending
        ]
        context = GraphContext.build(claims)

        # Re-ingest and retry run over documents that may already have
        # insights, exactly as with mentions. The audit rows go with them:
        # `nemotron_runs.insight_id` is deliberately FK-less, so orphans
        # would otherwise accumulate and inflate the cascade's call count on
        # every re-run.
        stale = list(
            self.db.execute(
                select(Insight.id).where(
                    Insight.org_id == self.job.org_id,
                    Insight.document_id.in_([w.document_id for w in self.work]),
                )
            ).scalars()
        )
        if stale:
            self.db.execute(delete(NemotronRun).where(NemotronRun.insight_id.in_(stale)))
        self.db.execute(
            delete(Insight).where(
                Insight.org_id == self.job.org_id,
                Insight.document_id.in_([w.document_id for w in self.work]),
            )
        )

        # Deduplicate before routing, not after: two mention pairs in one
        # sentence that coreference merged onto the same two entities are one
        # claim, and escalating it twice would spend two LLM calls on it.
        seen: set[uuid.UUID] = set()
        candidates: list[CascadeCandidate] = []
        rows: list[tuple[DocumentWork, DraftInsight, uuid.UUID]] = []
        classical: dict[uuid.UUID, Any] = {}
        names = {record.id: record.canonical for record in self.resolver.records()}

        for (work, draft, subject_id, object_id), claim in zip(pending, claims, strict=True):
            insight_id = ids.insight_id(
                work.document_id, draft.char_start, draft.relation, subject_id, object_id
            )
            if insight_id in seen:
                continue
            seen.add(insight_id)

            decision = route_claim(claim, context, self.settings)
            classical[insight_id] = decision.bucket
            rows.append((work, draft, insight_id))
            candidates.append(
                CascadeCandidate(
                    insight_id=insight_id,
                    subject_id=subject_id,
                    object_id=object_id,
                    subject_name=names.get(subject_id, draft.subject.surface),
                    object_name=names.get(object_id, draft.object.surface),
                    relation=draft.relation,
                    confidence=draft.confidence,
                    vacuity=draft.vacuity,
                    dissonance=draft.dissonance,
                    citation=draft.sentence_text,
                    classical=decision,
                )
            )

        report = Cascade(self.db, self.job.org_id, self.settings).run(candidates)
        outcomes: dict[uuid.UUID, CascadeOutcome] = {
            outcome.insight_id: outcome for outcome in report.outcomes
        }
        # The runs have to exist before an insight can reference one.
        self.db.flush()

        total = max(len(rows), 1)
        for index, ((work, draft, insight_id), candidate) in enumerate(
            zip(rows, candidates, strict=True), start=1
        ):
            outcome = outcomes[insight_id]
            self.db.add(
                Insight(
                    id=insight_id,
                    org_id=self.job.org_id,
                    document_id=work.document_id,
                    subject_id=candidate.subject_id,
                    object_id=candidate.object_id,
                    relation=draft.relation,
                    char_start=draft.char_start,
                    char_end=draft.char_end,
                    sentence_text=draft.sentence_text,
                    confidence=draft.confidence,
                    vacuity=draft.vacuity,
                    dissonance=draft.dissonance,
                    routing=outcome.routing,
                    resolved_by=outcome.resolved_by,
                    nemotron_run_id=(
                        outcome.nemotron_run.id if outcome.nemotron_run is not None else None
                    ),
                    classical_routing=classical[insight_id],
                    degraded=outcome.degraded,
                    attention=draft.attention,
                    tokens=draft.tokens,
                    evidence_logits=draft.logits,
                )
            )
            work.insight_ids.append(insight_id)
            self.counts["insights"] += 1
            self._advance(index, total, "routing")

        self.counts["escalated"] = report.calls_attempted
        self.counts["degraded"] = report.calls_degraded
        self._set_progress("routing", 1.0)
        self.job.insights_found = self.counts["insights"]
        self._commit()

    # ── progress and termination ─────────────────────────────────────────────

    def _set_progress(self, stage: str, fraction: float) -> None:
        # Reassigned rather than mutated: SQLAlchemy does not track in-place
        # changes to a JSONB dict, so `progress[k] = v` would never be written.
        progress = dict(self.job.stage_progress or {})
        progress[stage] = round(min(max(fraction, 0.0), 1.0), 4)
        self.job.stage_progress = progress

    def _advance(self, index: int, total: int, stage: str, *, docs_done: int | None = None) -> None:
        self._set_progress(stage, index / max(total, 1))
        if docs_done is not None:
            self.job.docs_done = docs_done
        if index % COMMIT_EVERY_DOCUMENTS == 0 or index == total:
            self._commit()

    def _commit(self) -> None:
        self.db.commit()

    def _finish(self, started: float, state: JobState) -> PipelineResult:
        self.job.state = state
        self.job.docs_done = self.job.docs_total
        self.job.insights_found = self.counts["insights"]
        self.job.finished_at = datetime.now(UTC)
        self._commit()
        elapsed = int((time.monotonic() - started) * 1000)
        log.info(
            "ingest_job_done",
            job_id=str(self.job.id),
            documents=self.job.docs_total,
            elapsed_ms=elapsed,
            **self.counts,
        )
        return PipelineResult(
            job_id=self.job.id,
            documents=self.job.docs_total,
            mentions=self.counts["mentions"],
            entities=self.counts["entities"],
            insights=self.counts["insights"],
            elapsed_ms=elapsed,
            state=state,
        )

    def _fail(self, started: float, state: JobState, exc: Exception) -> PipelineResult:
        self.db.rollback()
        log.error(
            "ingest_job_failed",
            job_id=str(self.job.id),
            stage=state.value,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        # Re-read after the rollback: the job object's pending changes were
        # discarded with everything else, so mutating the stale instance would
        # write nothing.
        job = self.db.get(IngestJob, self.job.id)
        if job is not None:
            job.state = JobState.failed
            job.error = f"{type(exc).__name__}: {exc}"[:500]
            job.error_stage = state.value
            job.finished_at = datetime.now(UTC)
            self.db.commit()
            self.job = job
        return PipelineResult(
            job_id=self.job.id,
            documents=self.job.docs_total,
            mentions=self.counts["mentions"],
            entities=self.counts["entities"],
            insights=self.counts["insights"],
            elapsed_ms=int((time.monotonic() - started) * 1000),
            state=JobState.failed,
        )


def _keep_one_direction(
    pending: list[tuple[DocumentWork, DraftInsight, uuid.UUID, uuid.UUID]],
) -> list[tuple[DocumentWork, DraftInsight, uuid.UUID, uuid.UUID]]:
    """One claim per sentence, relation and pair of parties.

    "Meridian Supply LLC is a wholly owned subsidiary of Advent Holdings"
    yields both directions: the right one at confidence 0.73 and its reverse
    at 0.35. Both cannot be true, and keeping both put a spurious two-node
    loop in the ownership graph — which is the one visual the demo turns on,
    so a false cycle there is worse than a missing edge.

    The model's own confidence picks the winner. For a symmetric relation
    like SHARES_ADDRESS_WITH the two directions are the same claim anyway,
    and collapsing them is what stops the graph drawing the edge twice.
    """
    best: dict[tuple[uuid.UUID, int, str, frozenset[uuid.UUID]], int] = {}
    for position, (work, draft, subject_id, object_id) in enumerate(pending):
        key = (
            work.document_id,
            draft.char_start,
            draft.relation,
            frozenset((subject_id, object_id)),
        )
        incumbent = best.get(key)
        if incumbent is None or draft.confidence > pending[incumbent][1].confidence:
            best[key] = position

    kept = set(best.values())
    return [entry for position, entry in enumerate(pending) if position in kept]


# ── entry points ─────────────────────────────────────────────────────────────


def run_job(job_id: uuid.UUID, settings: Settings | None = None) -> PipelineResult | None:
    """Run a job in its own session. The background task target.

    A fresh request id is bound so every log line from the job is correlatable
    even though the HTTP request that started it returned long ago.
    """
    set_request_id(new_request_id())
    with db_session() as db:
        job = db.get(IngestJob, job_id)
        if job is None:
            log.warning("ingest_job_missing", job_id=str(job_id))
            return None
        return IngestPipeline(db, job, settings).run()


def create_job(
    db: Session,
    *,
    org_id: uuid.UUID,
    document_ids: Sequence[uuid.UUID],
    scenario: str | None = None,
) -> IngestJob:
    job = IngestJob(
        org_id=org_id,
        state=JobState.queued,
        doc_ids=list(document_ids),
        docs_total=len(document_ids),
        docs_done=0,
        insights_found=0,
        stage_progress=dict.fromkeys(STAGE_KEYS, 0.0),
        scenario=scenario,
    )
    db.add(job)
    db.flush()
    return job
