"""The ingest pipeline and its job state machine. Brief §5.

One job, one thread, five stages over §2's `job_state` enum:

  tagging   spaCy tokenization + BiLSTM-CRF entity spans, per document
  parsing   coreference onto `entities`, mentions persisted with exact offsets
  relating  relation extraction over candidate entity pairs      (Stage 3)
  scoring   the evidential head: confidence, vacuity, dissonance (Stage 4)
  routing   the vacuity gate and the Nemotron cascade            (Stage 6)

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

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger, new_request_id, set_request_id
from db.models import Document, Entity, IngestJob, JobState, Mention
from db.session import db_session
from ml.entities.coref import EntityResolver
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
    # Populated by Stage 3 onward; each entry is one persisted insight id.
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
        self.counts: dict[str, int] = {"mentions": 0, "entities": 0, "insights": 0}

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
