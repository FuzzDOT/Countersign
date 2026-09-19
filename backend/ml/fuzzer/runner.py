"""Running the fuzzer. Plan §1.8 and §4 Stage 5 — the protected block.

For every insight, for every family, perturb the *whole document*, re-run the
pipeline over the perturbed text, and ask whether the same claim between the
same two parties survives.

Three decisions worth arguing with:

**The whole document, not just the sentence.** Re-inferring one sentence in
isolation would be four times faster and would make `reorder` a definitional
no-op. Running the document means coreference and sentence segmentation are
in scope too, which is where two of the five families do their damage.

**Matching is by entity, not by string.** After a rename, the parties have
different names; after a synonym swap, the sentence reads differently. The
runner resolves each candidate's surfaces through the same coreference layer
the pipeline uses, inverting the rename map first, and compares entity ids.
Matching on text would score every rename as a total loss and the family
would measure nothing.

**Nothing is written to `documents`.** Variants exist in memory for the
length of one trial. `fragility_trials` stores three outcomes and no offsets
(plan §3), and `tests/test_fuzzer.py` asserts the document count is unchanged
across a run.
"""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger
from db.models import Document, Entity, FragilityTrial, Insight
from ml.entities.coref import EntityResolver, entity_key
from ml.fuzzer.base import FAMILIES, Family, Variant
from ml.fuzzer.boilerplate import BoilerplateFamily
from ml.fuzzer.fragility import ScatterPoint, Trial, fragility_of
from ml.fuzzer.punctuation import PunctuationFamily
from ml.fuzzer.rename import RenameFamily
from ml.fuzzer.reorder import ReorderFamily
from ml.fuzzer.synonym import SynonymFamily
from ml.relations.infer import DraftInsight, get_extractor
from ml.tagger.infer import get_tagger
from ml.text.parse import parse

log = get_logger(__name__)


def all_families() -> dict[str, Family]:
    return {
        SynonymFamily.name: SynonymFamily(),
        RenameFamily.name: RenameFamily(),
        BoilerplateFamily.name: BoilerplateFamily(),
        ReorderFamily.name: ReorderFamily(),
        PunctuationFamily.name: PunctuationFamily(),
    }


@dataclass(slots=True)
class FuzzSummary:
    org_id: uuid.UUID
    n_insights: int
    n_trials: int
    elapsed_ms: int
    documents_before: int
    documents_after: int
    points: list[ScatterPoint] = field(default_factory=list)
    trials: list[Trial] = field(default_factory=list)

    @property
    def wrote_documents(self) -> bool:
        return self.documents_after != self.documents_before


class FuzzRunner:
    """One fuzzing pass over an organization's insights."""

    def __init__(
        self,
        db: Session,
        org_id: uuid.UUID,
        settings: Settings | None = None,
        *,
        variants_per_family: int = 1,
    ) -> None:
        self.db = db
        self.org_id = org_id
        self.settings = settings or get_settings()
        self.variants_per_family = variants_per_family
        self.families = all_families()

    # ── entry point ──────────────────────────────────────────────────────────

    def run(self, *, limit: int | None = None) -> FuzzSummary:
        started = time.monotonic()
        documents_before = self._document_count()

        insights = list(
            self.db.execute(
                select(Insight)
                .where(Insight.org_id == self.org_id)
                .order_by(Insight.created_at, Insight.id)
            ).scalars()
        )
        if limit is not None:
            insights = insights[:limit]

        resolver = self._resolver()
        texts = self._document_texts({i.document_id for i in insights})

        trials: list[Trial] = []
        points: list[ScatterPoint] = []

        by_document: dict[uuid.UUID, list[Insight]] = {}
        for insight in insights:
            by_document.setdefault(insight.document_id, []).append(insight)

        for document_id, members in by_document.items():
            text = texts.get(document_id)
            if text is None:  # pragma: no cover - insight without its document
                continue
            doc = parse(text)
            tagging = get_tagger(self.settings).tag(doc)

            for insight in members:
                insight_trials = self._fuzz_one(insight, doc, tagging, resolver)
                trials.extend(insight_trials)
                score = fragility_of(insight_trials)
                insight.fragility = score
                points.append(
                    ScatterPoint(
                        insight_id=insight.id,
                        vacuity=insight.vacuity,
                        fragility=score,
                        routing=insight.routing.value,
                    )
                )

        self._persist(trials)
        self.db.commit()

        summary = FuzzSummary(
            org_id=self.org_id,
            n_insights=len(insights),
            n_trials=len(trials),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            documents_before=documents_before,
            documents_after=self._document_count(),
            points=points,
            trials=trials,
        )
        log.info(
            "fuzzer_done",
            org_id=str(self.org_id),
            insights=summary.n_insights,
            trials=summary.n_trials,
            elapsed_ms=summary.elapsed_ms,
            wrote_documents=summary.wrote_documents,
        )
        return summary

    # ── one insight ──────────────────────────────────────────────────────────

    def _fuzz_one(
        self, insight: Insight, doc: object, tagging: object, resolver: EntityResolver
    ) -> list[Trial]:
        target = (insight.char_start, insight.char_end)
        trials: list[Trial] = []

        for family_name in FAMILIES:
            family = self.families[family_name]
            for variant_index in range(self.variants_per_family):
                # Seeded on the insight and the family, so a re-run of the
                # fuzzer reproduces the same perturbations and the same
                # number lands on the slide.
                rng = random.Random(  # noqa: S311 - reproducible perturbation
                    f"{self.settings.pipeline_seed}:{insight.id}:{family_name}:{variant_index}"
                )
                variant = family.build(doc, tagging, target, rng)  # type: ignore[arg-type]
                trials.append(self._score_variant(insight, variant, variant_index, resolver))

        return trials

    def _score_variant(
        self,
        insight: Insight,
        variant: Variant,
        variant_index: int,
        resolver: EntityResolver,
    ) -> Trial:
        if variant.vacuous:
            return Trial(
                insight_id=insight.id,
                perturbation=variant.family,
                variant=variant_index,
                label_flipped=False,
                conf_delta=0.0,
                relation_lost=False,
                vacuous=True,
            )

        # Cached like any other parse: the variants are deterministic, so a
        # second fuzzer run over an unchanged corpus is almost free. That is
        # the difference between a three-minute and a forty-minute run.
        perturbed = parse(variant.text)
        tagging = get_tagger(self.settings).tag(perturbed)
        drafts = get_extractor(self.settings).extract(perturbed, tagging)

        matches = [
            draft
            for draft in drafts
            if draft.char_start < variant.target_end
            and draft.char_end > variant.target_start
            and self._same_parties(draft, insight, variant, resolver)
        ]

        if not matches:
            return Trial(
                insight_id=insight.id,
                perturbation=variant.family,
                variant=variant_index,
                label_flipped=True,
                conf_delta=-insight.confidence,
                relation_lost=True,
            )

        best = max(matches, key=lambda draft: draft.confidence)
        return Trial(
            insight_id=insight.id,
            perturbation=variant.family,
            variant=variant_index,
            label_flipped=best.relation != insight.relation,
            conf_delta=round(best.confidence - insight.confidence, 6),
            relation_lost=False,
        )

    def _same_parties(
        self,
        draft: DraftInsight,
        insight: Insight,
        variant: Variant,
        resolver: EntityResolver,
    ) -> bool:
        subject = self._entity_for(
            draft.subject.surface, draft.subject.entity_type, variant, resolver
        )
        obj = self._entity_for(draft.object.surface, draft.object.entity_type, variant, resolver)
        return subject == insight.subject_id and obj == insight.object_id

    def _entity_for(
        self, surface: str, entity_type: str, variant: Variant, resolver: EntityResolver
    ) -> uuid.UUID | None:
        """Which original entity this surface refers to, undoing any rename."""
        key = entity_key(surface, entity_type)
        original = variant.renames.get(key)
        record = resolver.lookup(original or surface, entity_type)
        return record.id if record is not None else None

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _resolver(self) -> EntityResolver:
        resolver = EntityResolver(self.org_id, threshold=self.settings.entity_coref_threshold)
        for entity in self.db.execute(select(Entity).where(Entity.org_id == self.org_id)).scalars():
            resolver.add_existing(
                entity_id=entity.id,
                canonical=entity.canonical,
                entity_type=entity.entity_type,
                aliases=list(entity.aliases or []),
                mention_count=entity.mention_count,
                embedding=list(entity.embedding) if entity.embedding is not None else None,
            )
        return resolver

    def _document_texts(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = (
            self.db.execute(
                select(Document.id, Document.raw_text).where(
                    Document.org_id == self.org_id, Document.id.in_(ids)
                )
            )
            .tuples()
            .all()
        )
        return dict(rows)

    def _document_count(self) -> int:
        from sqlalchemy import func

        return int(
            self.db.execute(
                select(func.count(Document.id)).where(Document.org_id == self.org_id)
            ).scalar_one()
        )

    def _persist(self, trials: Sequence[Trial]) -> None:
        """Replace this org's trials. No offsets are stored (plan §3)."""
        insight_ids = {trial.insight_id for trial in trials}
        if insight_ids:
            self.db.execute(
                delete(FragilityTrial).where(FragilityTrial.insight_id.in_(insight_ids))
            )
        for trial in trials:
            if trial.vacuous:
                # A family that could not perturb this document produced no
                # evidence either way. Storing it would put a row in the
                # detail sheet that says nothing.
                continue
            self.db.add(
                FragilityTrial(
                    insight_id=trial.insight_id,
                    perturbation=trial.perturbation,
                    variant=trial.variant,
                    label_flipped=trial.label_flipped,
                    conf_delta=trial.conf_delta,
                    relation_lost=trial.relation_lost,
                )
            )


def run_fuzzer_job(
    org_id: uuid.UUID, settings: Settings | None = None, *, limit: int | None = None
) -> FuzzSummary:
    """Background-task entry point for `POST /evals/fragility/run`."""
    from core.logging import new_request_id, set_request_id
    from db.session import db_session

    set_request_id(new_request_id())
    with db_session() as db:
        return FuzzRunner(db, org_id, settings).run(limit=limit)
