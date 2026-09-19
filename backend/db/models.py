"""SQLAlchemy 2.0 schema.

Mirrors 01-BACKEND-BRIEF.md §2 with the five deltas documented in
docs/03-BACKEND-PLAN.md §2:

  1. `nemotron_runs` declared before `insights` (no real FK cycle; the brief's
     DDL just listed them in an order Postgres would reject)
  2. `calibration_snapshots.idempotency_key`
  3. `insights.degraded`
  4. `ingest_jobs.stage_progress`
  5. `routing_eval_cases.split` / `gate_bypassed`

Every index here is deliberate and matched to a query that exists, not to
hypothetical scale — see the rationale comments on each `__table_args__`.

`OrgScoped` is the load-bearing abstraction: any model carrying tenant data
inherits it, and `api/deps.py::Scope.query()` only accepts `type[OrgScoped]`.
That makes "forgot to filter by org_id" a type error rather than a data breach.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    declared_attr,
    mapped_column,
    relationship,
)

# Explicit naming convention so Alembic autogenerate produces stable,
# non-random constraint names across machines.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ── enum types ───────────────────────────────────────────────────────────────
# `values_callable` makes SQLAlchemy emit the enum *values* rather than the
# Python member names. They happen to be identical here, but relying on that
# breaks the day someone renames a member.


class UserRole(StrEnum):
    owner = "owner"
    analyst = "analyst"
    viewer = "viewer"


class DocSource(StrEnum):
    invoice = "invoice"
    email = "email"
    press_release = "press_release"
    rss = "rss"
    gdelt = "gdelt"
    note = "note"
    transaction_log = "transaction_log"


class RoutingBucket(StrEnum):
    auto_file = "auto_file"
    flag_for_review = "flag_for_review"
    escalate_now = "escalate_now"

    @property
    def severity(self) -> int:
        return {"auto_file": 0, "flag_for_review": 1, "escalate_now": 2}[self.value]


class Resolver(StrEnum):
    classical = "classical"
    nemotron = "nemotron"


class JobState(StrEnum):
    queued = "queued"
    tagging = "tagging"
    parsing = "parsing"
    relating = "relating"
    scoring = "scoring"
    routing = "routing"
    done = "done"
    failed = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobState.done, JobState.failed)


class EntityType(StrEnum):
    """Tagger output labels. Stored as TEXT, not a PG enum.

    Deliberate: the tag set is an ML artifact that may gain a label at hour 5,
    and an ALTER TYPE mid-hackathon is a worse problem than an unconstrained
    column. A CHECK constraint guards the known values instead.
    """

    ORG = "ORG"
    PERSON = "PERSON"
    MONEY = "MONEY"
    DATE = "DATE"
    ACCOUNT_REF = "ACCOUNT_REF"
    TRANSACTION_TYPE = "TRANSACTION_TYPE"


class RelationType(StrEnum):
    WIRED_FUNDS_TO = "WIRED_FUNDS_TO"
    OWNED_BY = "OWNED_BY"
    INVOICED = "INVOICED"
    SHARES_ADDRESS_WITH = "SHARES_ADDRESS_WITH"
    SIGNATORY_OF = "SIGNATORY_OF"
    NO_RELATION = "NO_RELATION"


class Perturbation(StrEnum):
    synonym = "synonym"
    rename = "rename"
    boilerplate = "boilerplate"
    reorder = "reorder"
    punctuation = "punctuation"


def _pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_type=False,  # created explicitly in the initial migration
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


# ── shared mixins and column helpers ────────────────────────────────────────


class UuidPk:
    """Server-generated UUID primary key, declared once for all 13 tables.

    Server-side `gen_random_uuid()` rather than a client-side default, so a
    row inserted by psql or a migration gets an id the same way the ORM does.
    Seeded scenarios override this with deterministic UUID5 values
    (docs/03-BACKEND-PLAN.md §1.11) so demo ids survive a database reset.
    """

    @declared_attr
    def id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=False
    )


class OrgScoped(UuidPk):
    """Declares `org_id` for every table holding tenant data.

    Inheriting this is what makes a model eligible for `Scope.query()`, which
    is typed to accept only `type[OrgScoped]`. That is the whole mechanism: a
    query against tenant data that skips the tenant filter is a type error at
    lint time rather than a cross-org read at demo time.

    The column is defined once here rather than seven times in the models, so
    the FK, the CASCADE and the nullability cannot drift between tables — and
    `model.org_id` resolves for the type checker without a cast.

    Models with no tenant data of their own (mentions, nemotron_runs,
    ablation_runs, fragility_trials) deliberately do NOT inherit this. They are
    reachable only through an org-scoped parent, which forces the join that
    proves the tenancy check ran.
    """

    @declared_attr
    def org_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        )


# ── identity ─────────────────────────────────────────────────────────────────


class Organization(UuidPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    users: Mapped[list[User]] = relationship(back_populates="org", cascade="all, delete-orphan")


class User(OrgScoped, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # argon2id
    role: Mapped[UserRole] = mapped_column(
        _pg_enum(UserRole, "user_role"), nullable=False, server_default=text("'viewer'")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    failed_logins: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    # Window start for the "5 failures in 15 minutes" rule. Without it, five
    # failures spread over a year would lock the account.
    first_failed_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    org: Mapped[Organization] = relationship(back_populates="users")

    __table_args__ = (
        # Global unique, not per-org: email is the login identifier and two orgs
        # cannot share one. CITEXT makes this case-insensitive.
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_org_id", "org_id"),
    )


class RefreshToken(UuidPk, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    # Rotation family. Presenting an already-revoked token from a family revokes
    # the whole family — that is the reuse-detection mechanism in brief §4.
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # Lookup is always by hash on refresh; unique prevents two rows for one
        # token even under a race.
        Index("ix_refresh_tokens_token_hash", "token_hash", unique=True),
        # "revoke this user's live sessions" and "revoke this family".
        Index("ix_refresh_tokens_user_id_revoked_at", "user_id", "revoked_at"),
        Index("ix_refresh_tokens_family_id", "family_id"),
    )


# ── documents ────────────────────────────────────────────────────────────────


class Document(OrgScoped, Base):
    __tablename__ = "documents"

    source: Mapped[DocSource] = mapped_column(_pg_enum(DocSource, "doc_source"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # CANONICAL. Every char_start/char_end in this database indexes into this
    # exact string. It is never normalized, trimmed, or re-extracted.
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = _created_at()
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    mentions: Mapped[list[Mention]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Dedupe on re-upload. Per-org so two tenants can hold the same doc.
        Index("ix_documents_org_id_content_sha", "org_id", "content_sha", unique=True),
        # The document list is "this org's docs, newest first".
        Index("ix_documents_org_id_received_at", "org_id", text("received_at DESC")),
    )


# ── extraction ───────────────────────────────────────────────────────────────


class Entity(OrgScoped, Base):
    __tablename__ = "entities"

    canonical: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Hashed char-3gram TF-IDF, L2-normalized (plan §1.5). Cosine, not L2.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(256), nullable=True)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    first_seen: Mapped[datetime] = _created_at()
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        Index("ix_entities_org_id_entity_type", "org_id", "entity_type"),
        # Coref does a k-NN lookup per new mention. At a few thousand entities
        # this is borderline unnecessary, but it is one line and it keeps coref
        # off an O(n) scan.
        Index(
            "ix_entities_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        CheckConstraint(
            "entity_type IN ('ORG','PERSON','MONEY','DATE','ACCOUNT_REF','TRANSACTION_TYPE')",
            name="entity_type_known",
        ),
    )


class Mention(UuidPk, Base):
    """A tagged span in a specific document.

    Not OrgScoped on purpose — reachable only via its document, which is.
    """

    __tablename__ = "mentions"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    tagger_conf: Mapped[float] = mapped_column(Float, nullable=False)

    document: Mapped[Document] = relationship(back_populates="mentions")

    __table_args__ = (
        # The citation reader renders highlight spans in document order.
        Index("ix_mentions_document_id_char_start", "document_id", "char_start"),
        Index("ix_mentions_entity_id", "entity_id"),
        CheckConstraint("char_end > char_start", name="span_ordered"),
        CheckConstraint("char_start >= 0", name="span_non_negative"),
    )


# ── cascade audit ────────────────────────────────────────────────────────────
# Declared before Insight: insights.nemotron_run_id references this table, and
# nemotron_runs.insight_id is deliberately FK-less so there is no cycle.


class NemotronRun(UuidPk, Base):
    __tablename__ = "nemotron_runs"

    # No FK: the run is logged before the insight row is finalized, and the
    # audit trail must survive an insight being deleted.
    insight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    prompt_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[RoutingBucket] = mapped_column(
        _pg_enum(RoutingBucket, "routing_bucket"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the call failed and the classical decision was used instead.
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # Append-only audit, read once per eval page load, so the PK is nearly
        # enough. The org filter is the exception: the audit table is rendered
        # in the UI and must not leak another tenant's rationales.
        Index("ix_nemotron_runs_org_id_created_at", "org_id", text("created_at DESC")),
    )


class Insight(OrgScoped, Base):
    __tablename__ = "insights"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(Text, nullable=False)

    # ── citation: byte-exact, non-negotiable ─────────────────────────────────
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # Denormalized so the feed renders without joining documents. Must satisfy
    # raw_text[char_start:char_end] == sentence_text (tests/test_offsets.py).
    sentence_text: Mapped[str] = mapped_column(Text, nullable=False)

    # ── trust layer ──────────────────────────────────────────────────────────
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    vacuity: Mapped[float] = mapped_column(Float, nullable=False)
    dissonance: Mapped[float] = mapped_column(Float, nullable=False)
    # Null until the fuzzer job has run over this insight.
    fragility: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── cascade ──────────────────────────────────────────────────────────────
    routing: Mapped[RoutingBucket] = mapped_column(
        _pg_enum(RoutingBucket, "routing_bucket"), nullable=False
    )
    resolved_by: Mapped[Resolver] = mapped_column(_pg_enum(Resolver, "resolver"), nullable=False)
    nemotron_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nemotron_runs.id", ondelete="SET NULL"), nullable=True
    )
    # Plan §2.3 — set when Nemotron was gated for escalation but hard-failed, so
    # the frontend can render "reviewed classically" (frontend brief §4.3).
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Classical prediction retained even when Nemotron overrode it. Needed for
    # routing agreement/override rates and the classical-only eval baseline.
    classical_routing: Mapped[RoutingBucket | None] = mapped_column(
        _pg_enum(RoutingBucket, "routing_bucket"), nullable=True
    )
    # [{edge_id, src_token, dst_token, weight, src_idx, dst_idx}]
    attention: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Token surfaces for the ablation panel, parallel to attention indices.
    tokens: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Raw evidence logits, kept so temperature scaling can be refit post-hoc
    # without re-running inference over the corpus (Stage 9 needs this).
    evidence_logits: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    document: Mapped[Document] = relationship()
    subject: Mapped[Entity] = relationship(foreign_keys=[subject_id])
    object: Mapped[Entity] = relationship(foreign_keys=[object_id])
    nemotron_run: Mapped[NemotronRun | None] = relationship()

    __table_args__ = (
        # THE hot path: the feed's default query is this org's items filtered by
        # routing bucket, newest first.
        Index(
            "ix_insights_org_id_routing_created_at", "org_id", "routing", text("created_at DESC")
        ),
        # The cascade gate scans the high-vacuity tail; descending matters.
        Index("ix_insights_org_id_vacuity", "org_id", text("vacuity DESC")),
        Index("ix_insights_document_id", "document_id"),
        Index("ix_insights_subject_id", "subject_id"),
        Index("ix_insights_object_id", "object_id"),
        # The feed's `q` param is a substring match over sentence_text, which
        # is a sequential scan without this. pg_trgm is already installed by
        # docker/postgres/init/01-extensions.sql.
        Index(
            "ix_insights_sentence_text_trgm",
            "sentence_text",
            postgresql_using="gin",
            postgresql_ops={"sentence_text": "gin_trgm_ops"},
        ),
        CheckConstraint("char_end > char_start", name="span_ordered"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_unit"),
        CheckConstraint("vacuity >= 0 AND vacuity <= 1", name="vacuity_unit"),
        CheckConstraint("dissonance >= 0 AND dissonance <= 1", name="dissonance_unit"),
        CheckConstraint(
            "fragility IS NULL OR (fragility >= 0 AND fragility <= 1)", name="fragility_unit"
        ),
    )


# ── ablation ─────────────────────────────────────────────────────────────────


class AblationRun(UuidPk, Base):
    __tablename__ = "ablation_runs"

    insight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insights.id", ondelete="CASCADE"), nullable=False
    )
    masked_edges: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'zero'"))
    confidence_before: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_after: Mapped[float] = mapped_column(Float, nullable=False)
    vacuity_before: Mapped[float] = mapped_column(Float, nullable=False)
    vacuity_after: Mapped[float] = mapped_column(Float, nullable=False)
    routing_before: Mapped[RoutingBucket] = mapped_column(
        _pg_enum(RoutingBucket, "routing_bucket"), nullable=False
    )
    routing_after: Mapped[RoutingBucket] = mapped_column(
        _pg_enum(RoutingBucket, "routing_bucket"), nullable=False
    )
    relation_before: Mapped[str] = mapped_column(Text, nullable=False)
    relation_after: Mapped[str] = mapped_column(Text, nullable=False)
    # |delta_conf| > tau OR routing changed
    load_bearing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Template-filled, never generated (brief §8).
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # The insight detail response embeds ablation_history newest-first.
        Index("ix_ablation_runs_insight_id_created_at", "insight_id", text("created_at DESC")),
        CheckConstraint("mode IN ('zero','uniform')", name="mode_known"),
    )


# ── evals ────────────────────────────────────────────────────────────────────


class FragilityTrial(UuidPk, Base):
    """One perturbation trial against one insight.

    Stores no offsets by design: the perturbed text has different offsets from
    the stored document, and persisting them would silently corrupt citations.
    """

    __tablename__ = "fragility_trials"

    insight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insights.id", ondelete="CASCADE"), nullable=False
    )
    perturbation: Mapped[Perturbation] = mapped_column(Text, nullable=False)
    variant: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    label_flipped: Mapped[bool] = mapped_column(Boolean, nullable=False)
    conf_delta: Mapped[float] = mapped_column(Float, nullable=False)
    relation_lost: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # Aggregated per insight (fragility score) and per family (the
        # by_perturbation breakdown).
        Index("ix_fragility_trials_insight_id", "insight_id"),
        Index("ix_fragility_trials_perturbation", "perturbation"),
        CheckConstraint(
            "perturbation IN ('synonym','rename','boilerplate','reorder','punctuation')",
            name="perturbation_known",
        ),
    )


class CalibrationSnapshot(OrgScoped, Base):
    __tablename__ = "calibration_snapshots"

    label: Mapped[str] = mapped_column(Text, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    ece: Mapped[float] = mapped_column(Float, nullable=False)
    mce: Mapped[float] = mapped_column(Float, nullable=False)
    brier: Mapped[float] = mapped_column(Float, nullable=False)
    # [{bin_lo, bin_hi, avg_conf, accuracy, count}] x 10
    bins: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    n_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Plan §2.2 — a judge will double-click the recalibrate button.
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_calibration_snapshots_org_id_created_at", "org_id", text("created_at DESC")),
        # Partial unique: replaying a key returns the same snapshot, but the
        # many rows with a NULL key do not collide.
        Index(
            "ix_calibration_snapshots_idempotency_key",
            "org_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("label IN ('baseline','post_recalibration')", name="label_known"),
    )


class RoutingEvalCase(OrgScoped, Base):
    __tablename__ = "routing_eval_cases"

    insight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insights.id", ondelete="SET NULL"), nullable=True
    )
    # Generator-authored. Stated as a limitation in the writeup: we wrote the
    # fraud, so we know the answer — this is not human adjudication.
    ground_truth: Mapped[RoutingBucket] = mapped_column(
        _pg_enum(RoutingBucket, "routing_bucket"), nullable=False
    )
    predicted: Mapped[RoutingBucket] = mapped_column(
        _pg_enum(RoutingBucket, "routing_bucket"), nullable=False
    )
    resolved_by: Mapped[Resolver] = mapped_column(_pg_enum(Resolver, "resolver"), nullable=False)
    # Plan §2.5 — the three cascade_baseline arms are computed over the same
    # cases: gate on (cascade), gate bypassed (classical only), and forced
    # escalation (nemotron on everything).
    split: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'eval'"))
    gate_bypassed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_failure: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Hand-written mechanism explanation. Worth more to the Nemotron judges
    # than every clean metric above it (brief §10). Never ships empty.
    failure_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_routing_eval_cases_org_id_split", "org_id", "split"),
        Index("ix_routing_eval_cases_insight_id", "insight_id"),
        CheckConstraint("split IN ('eval','classical_only','nemotron_all')", name="split_known"),
    )


# ── ingest jobs ──────────────────────────────────────────────────────────────


class IngestJob(OrgScoped, Base):
    __tablename__ = "ingest_jobs"

    state: Mapped[JobState] = mapped_column(
        _pg_enum(JobState, "job_state"), nullable=False, server_default=text("'queued'")
    )
    doc_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    docs_total: Mapped[int] = mapped_column(Integer, nullable=False)
    docs_done: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    insights_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Plan §2.4 — per-stage fractions, which cannot be derived from
    # docs_done/docs_total alone. {"tagging": 1.0, "parsing": 0.56, ...}
    stage_progress: Mapped[dict[str, float]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    scenario: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (Index("ix_ingest_jobs_org_id_created_at", "org_id", text("created_at DESC")),)


# ── registry used by tests and the migration ─────────────────────────────────

PG_ENUM_TYPES: dict[str, type[StrEnum]] = {
    "user_role": UserRole,
    "doc_source": DocSource,
    "routing_bucket": RoutingBucket,
    "resolver": Resolver,
    "job_state": JobState,
}

ORG_SCOPED_MODELS: tuple[type[Base], ...] = (
    User,
    Document,
    Entity,
    Insight,
    CalibrationSnapshot,
    RoutingEvalCase,
    IngestJob,
)
