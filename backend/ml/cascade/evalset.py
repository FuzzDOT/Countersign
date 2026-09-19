"""Building the routing eval set from the generator's ground truth.

Brief §10. We authored the fraud, so we know what each claim *should* route
to — which is the only reason a confusion matrix is computable at all
without human annotators.

**Said plainly, here and in the writeup: this ground truth is
generator-authored.** It is not human adjudication on real documents. That
is a real limitation of a 24-hour project and it is better stated than
discovered.

Matching a gold relation to an extracted insight goes through entity ids,
not text: the insight's citation span is the *segmented* sentence, which can
differ from the gold span when spaCy merges two sentences, and the surfaces
can be aliases. The pair of entities plus the relation plus the document is
the identity that survives both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger
from data.synth.generate import generate
from data.synth.labels import GoldRelation
from db.models import Document, Insight, RoutingBucket, RoutingEvalCase
from ml.entities.coref import entity_uuid

log = get_logger(__name__)

# Relations whose subject is a person rather than an organization; needed to
# mint the right entity id for the gold side of the match.
PERSON_SUBJECT_RELATIONS = frozenset({"SIGNATORY_OF"})

SPLIT_EVAL = "eval"
SPLIT_NEMOTRON_ALL = "nemotron_all"


@dataclass(frozen=True, slots=True)
class BuildResult:
    scenarios: list[str]
    matched: int
    unmatched: int
    failures: int

    @property
    def total(self) -> int:
        return self.matched


def _gold_entity_ids(org_id: uuid.UUID, relation: GoldRelation) -> tuple[uuid.UUID, uuid.UUID]:
    subject_type = "PERSON" if relation.relation in PERSON_SUBJECT_RELATIONS else "ORG"
    return (
        entity_uuid(org_id, relation.subject_canonical, subject_type),
        entity_uuid(org_id, relation.object_canonical, "ORG"),
    )


def build(db: Session, org_id: uuid.UUID, settings: Settings | None = None) -> BuildResult:
    """One eval case per gold relation the pipeline actually extracted.

    A gold relation with no matching insight is *not* a case. It is a recall
    failure of the extractor, which the relation eval already reports;
    counting it here as a routing error would mix two different mistakes
    into one confusion matrix and make both harder to read.
    """
    settings = settings or get_settings()

    scenarios = sorted(
        {
            scenario
            for scenario in db.execute(
                select(Document.meta["scenario"].astext).where(Document.org_id == org_id)
            ).scalars()
            if scenario
        }
    )
    if not scenarios:
        return BuildResult(scenarios=[], matched=0, unmatched=0, failures=0)

    insights = {
        (i.document_id, i.subject_id, i.object_id, i.relation): i
        for i in db.execute(select(Insight).where(Insight.org_id == org_id)).scalars()
    }

    db.execute(
        delete(RoutingEvalCase).where(
            RoutingEvalCase.org_id == org_id, RoutingEvalCase.split == SPLIT_EVAL
        )
    )

    matched = unmatched = failures = 0
    for scenario in scenarios:
        manifest = generate(scenario, seed=settings.pipeline_seed, org_id=org_id)
        for document in manifest.documents:
            for relation in document.relations:
                subject_id, object_id = _gold_entity_ids(org_id, relation)
                insight = insights.get(
                    (document.document_id, subject_id, object_id, relation.relation)
                )
                if insight is None:
                    unmatched += 1
                    continue

                matched += 1
                is_failure = insight.routing.value != relation.routing
                if is_failure:
                    failures += 1

                db.add(
                    RoutingEvalCase(
                        org_id=org_id,
                        insight_id=insight.id,
                        ground_truth=RoutingBucket(relation.routing),
                        predicted=insight.routing,
                        resolved_by=insight.resolved_by,
                        split=SPLIT_EVAL,
                        gate_bypassed=False,
                        is_failure=is_failure,
                        # The hand-written mechanism note travels with the
                        # planted case (plan §0). Everything else gets a
                        # generated-from-fields description, which is why
                        # only the planted one is quoted in the writeup.
                        failure_note=relation.failure_note,
                    )
                )

    db.flush()
    log.info(
        "routing_eval_built",
        org_id=str(org_id),
        scenarios=scenarios,
        matched=matched,
        unmatched=unmatched,
        failures=failures,
    )
    return BuildResult(scenarios=scenarios, matched=matched, unmatched=unmatched, failures=failures)
