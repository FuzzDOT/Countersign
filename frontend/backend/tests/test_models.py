"""Schema invariants.

No database required — these assert facts about the SQLAlchemy metadata, which
is where the tenant-isolation guarantee and the index rationale actually live.
The parity test against the hand-written migration is the one that earns its
keep: the migration and the models are two descriptions of one schema, and
drift between them shows up as a runtime error at the worst moment.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from db.models import (
    ORG_SCOPED_MODELS,
    PG_ENUM_TYPES,
    Base,
    Insight,
    JobState,
    OrgScoped,
    RoutingBucket,
    UuidPk,
)

EXPECTED_TABLES = {
    "organizations",
    "users",
    "refresh_tokens",
    "documents",
    "entities",
    "mentions",
    "nemotron_runs",
    "insights",
    "ablation_runs",
    "fragility_trials",
    "calibration_snapshots",
    "routing_eval_cases",
    "ingest_jobs",
}


def test_table_inventory() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_every_table_has_a_uuid_primary_key() -> None:
    """The PK is declared once on a mixin; this proves it reached every table."""
    for name, table in Base.metadata.tables.items():
        pk_columns = list(table.primary_key.columns)
        assert len(pk_columns) == 1, f"{name} should have a single-column PK"
        assert pk_columns[0].name == "id", f"{name}'s PK is not called id"
        assert (
            pk_columns[0].server_default is not None
        ), f"{name}.id must be server-generated so psql inserts get an id too"


def test_org_scoped_implies_a_primary_key() -> None:
    """Scope.query() reads both `id` and `org_id` off `type[OrgScoped]`.

    OrgScoped inherits UuidPk precisely so that both attributes resolve on the
    bound TypeVar without a cast. Break this inheritance and Scope silently
    needs `type: ignore` again, which is how the tenancy guarantee rots.
    """
    assert issubclass(OrgScoped, UuidPk)


def test_org_scoped_models_all_carry_org_id() -> None:
    """The tenant filter has something to filter on."""
    for model in ORG_SCOPED_MODELS:
        assert issubclass(model, OrgScoped), f"{model.__name__} is in the registry but not scoped"
        column = model.__table__.columns["org_id"]
        assert column.nullable is False, f"{model.__name__}.org_id must be NOT NULL"
        assert column.foreign_keys, f"{model.__name__}.org_id must reference organizations"


def test_child_tables_are_deliberately_unscoped() -> None:
    """Tables reachable only through a scoped parent must NOT have org_id.

    If one of these grows an `org_id`, someone will read it directly and the
    join — which is what guarantees the parent's tenancy check ran — gets
    skipped. Adding org_id here is a design change, not a convenience.
    """
    for table_name in ("mentions", "ablation_runs", "fragility_trials"):
        assert "org_id" not in Base.metadata.tables[table_name].columns


def test_insight_hot_path_indexes_exist() -> None:
    """Brief §2 index rationale, asserted rather than trusted."""
    names = {idx.name for idx in Insight.__table__.indexes}
    assert "ix_insights_org_id_routing_created_at" in names, "the feed's hot path"
    assert "ix_insights_org_id_vacuity" in names, "the cascade gate's tail scan"
    assert "ix_insights_sentence_text_trgm" in names, "the feed's `q` substring filter"


def test_citation_columns_are_not_nullable() -> None:
    """Every insight must be traceable. Brief §4.1: if this breaks, nothing matters."""
    for column in ("char_start", "char_end", "sentence_text", "document_id"):
        assert Insight.__table__.columns[column].nullable is False


def test_trust_columns_are_bounded() -> None:
    """Confidence, vacuity and dissonance are claimed to be in [0,1]."""
    constraint_sql = " ".join(
        str(c.sqltext) for c in Insight.__table__.constraints if hasattr(c, "sqltext")
    )
    for field in ("confidence", "vacuity", "dissonance"):
        assert field in constraint_sql


def test_fragility_is_nullable() -> None:
    """Null until the fuzzer job has run; not zero, which would mean 'robust'."""
    assert Insight.__table__.columns["fragility"].nullable is True


def test_routing_bucket_severity_ordering() -> None:
    """Voice briefings rank by severity, so the ordering is load-bearing."""
    assert RoutingBucket.auto_file.severity < RoutingBucket.flag_for_review.severity
    assert RoutingBucket.flag_for_review.severity < RoutingBucket.escalate_now.severity


def test_job_terminal_states() -> None:
    """The websocket closes on a terminal frame and must agree on which those are."""
    assert JobState.done.is_terminal and JobState.failed.is_terminal
    assert not any(
        s.is_terminal
        for s in (
            JobState.queued,
            JobState.tagging,
            JobState.parsing,
            JobState.relating,
            JobState.scoring,
            JobState.routing,
        )
    )


# ── parity with the hand-written migration ───────────────────────────────────


def _load_initial_migration() -> Any:
    path = next(
        Path(__file__).resolve().parent.parent.glob("db/migrations/versions/*_initial_schema.py")
    )
    spec = importlib.util.spec_from_file_location("initial_schema", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration() -> Any:
    return _load_initial_migration()


def test_migration_enum_values_match_models(migration: Any) -> None:
    """A value added to a Python enum but not the migration is a runtime 500."""
    for name, enum_cls in PG_ENUM_TYPES.items():
        assert name in migration._ENUMS, f"migration is missing the {name} type"
        assert set(migration._ENUMS[name]) == {
            m.value for m in enum_cls
        }, f"{name} values differ between db/models.py and the initial migration"


def test_migration_drops_every_table_it_creates(migration: Any) -> None:
    """`make reset-db` runs `downgrade base`; a missed table leaves a broken DB."""
    source = Path(migration.__file__).read_text(encoding="utf-8")
    downgrade_source = source.split("def downgrade()", 1)[1]
    for table in EXPECTED_TABLES:
        assert f'"{table}"' in downgrade_source, f"downgrade() never drops {table}"
