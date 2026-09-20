"""The ablation engine. Brief §8, plan §1.3 and §4 Stage 7.

Mask a named edge, re-infer, report what actually moved. This is the demo's
strongest thirty seconds and it is the one claim that needed a design
decision at hour 6 to still be possible at hour 19: the relation model owns
`edge_mask` as a first-class argument (plan §1.2), so the counterfactual is
"this syntactic link is gone" rather than "here is a different graph".

The engine talks to `RelationModel`, never to the GAT. Both implementations
honour the mask — the GAT by zeroing attention logits and renormalizing, the
rule extractor by deleting the arcs before its path search — so flipping
`RELATION_MODEL=rules` at hour 9 would not have taken this away.

**Everything here is reconstructed, not cached.** The sentence graph is
rebuilt from `documents.raw_text` through the same parse and the same
tagger, which is slower than storing it and is the only way the thing being
ablated is provably the thing that produced the citation. Plan §6 records
the full forward pass as real debt; at 30 requests a minute over a ~40-node
graph it is fine, and the latency is measured rather than assumed.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.errors import ValidationFailed
from core.config import Settings, get_settings
from core.logging import get_logger
from db.models import AblationRun, Document, Entity, Insight, RoutingBucket
from ml.ablation import templates
from ml.cascade.routing import ClaimContext, GraphContext
from ml.cascade.routing import score as route_claim
from ml.entities.coref import EntityResolver
from ml.relations.infer import get_extractor
from ml.relations.interface import EdgeMask, MaskMode, RelationOutput, SentenceGraph
from ml.tagger.infer import TaggedMention, get_tagger
from ml.text.parse import parse

log = get_logger(__name__)

# Edges the client may name in one request. Mirrors the request model's
# bound; repeated here because the engine is also called from the voice
# layer, which does not go through the request model.
MAX_MASKED_EDGES = 32


class InsightNotAblatable(ValidationFailed):
    """The insight cannot be re-inferred from what is stored.

    A distinct error because the fix is different from a bad request: it
    means the document, the sentence or the entity pair no longer lines up,
    which is a data problem rather than a client one.
    """


@dataclass(frozen=True, slots=True)
class AblationResult:
    insight_id: uuid.UUID
    masked_edges: list[str]
    mode: MaskMode
    before: RelationOutput
    after: RelationOutput
    routing_before: RoutingBucket
    routing_after: RoutingBucket
    interpretation: str
    load_bearing: bool
    latency_ms: int

    @property
    def delta_confidence(self) -> float:
        return round(self.after.confidence - self.before.confidence, 6)

    @property
    def delta_vacuity(self) -> float:
        return round(self.after.vacuity - self.before.vacuity, 6)

    @property
    def routing_changed(self) -> bool:
        return self.routing_before != self.routing_after


@dataclass(frozen=True, slots=True)
class Rebuilt:
    """A stored insight, back in the form the model can re-infer from."""

    graph: SentenceGraph
    pair: object
    lemmas: tuple[str, ...]


def rebuild(db: Session, insight: Insight, settings: Settings | None = None) -> Rebuilt:
    """Reconstruct the sentence graph and candidate pair for one insight.

    The sentence is located by its recorded offsets, never by searching the
    document for the stored text (plan §3) — a document that states the same
    sentence twice would otherwise ablate the wrong one.
    """
    settings = settings or get_settings()

    document = db.execute(
        select(Document).where(
            Document.id == insight.document_id, Document.org_id == insight.org_id
        )
    ).scalar_one_or_none()
    if document is None:
        raise InsightNotAblatable(
            "The source document for this insight is gone.",
            details={"insight_id": str(insight.id)},
        )

    doc = parse(document.raw_text)
    tagging = get_tagger(settings).tag(doc)

    sentence_index = next(
        (
            index
            for index, encoding in enumerate(tagging.sentences)
            if encoding.unit.span.char_start == insight.char_start
            and encoding.unit.span.char_end == insight.char_end
        ),
        None,
    )
    if sentence_index is None:
        raise InsightNotAblatable(
            "The citation span no longer matches a sentence in the source document.",
            details={
                "insight_id": str(insight.id),
                "char_start": insight.char_start,
                "char_end": insight.char_end,
            },
        )

    resolver = _resolver(db, insight.org_id, settings)
    mentions = tagging.mentions_in_sentence(sentence_index)
    subject = _mention_for(mentions, insight.subject_id, resolver)
    obj = _mention_for(mentions, insight.object_id, resolver)
    if subject is None or obj is None:
        raise InsightNotAblatable(
            "The parties named by this insight are no longer both in its sentence.",
            details={"insight_id": str(insight.id)},
        )

    from ml.relations.infer import sentence_context

    context = sentence_context(doc, tagging, sentence_index, subject, obj)
    return Rebuilt(graph=context.graph, pair=context.pair, lemmas=context.lemmas)


def _resolver(db: Session, org_id: uuid.UUID, settings: Settings) -> EntityResolver:
    resolver = EntityResolver(org_id, threshold=settings.entity_coref_threshold)
    for entity in db.execute(select(Entity).where(Entity.org_id == org_id)).scalars():
        resolver.add_existing(
            entity_id=entity.id,
            canonical=entity.canonical,
            entity_type=entity.entity_type,
            aliases=list(entity.aliases or []),
            mention_count=entity.mention_count,
            embedding=list(entity.embedding) if entity.embedding is not None else None,
        )
    return resolver


def _mention_for(
    mentions: tuple[TaggedMention, ...], entity_id: uuid.UUID, resolver: EntityResolver
) -> TaggedMention | None:
    for mention in mentions:
        record = resolver.lookup(mention.surface, mention.entity_type)
        if record is not None and record.id == entity_id:
            return mention
    return None


def ablate(
    db: Session,
    insight: Insight,
    masked_edges: list[str],
    *,
    mode: MaskMode = "zero",
    settings: Settings | None = None,
    persist: bool = True,
) -> AblationResult:
    """Run the counterfactual and, by default, record it."""
    settings = settings or get_settings()
    started = time.perf_counter()

    rebuilt = rebuild(db, insight, settings)
    known = rebuilt.graph.known_edge_ids()
    unknown = [edge for edge in masked_edges if edge not in known]
    if unknown:
        # A named edge that is not in the graph would silently ablate
        # nothing and report "not load-bearing", which is the most
        # misleading possible answer.
        raise ValidationFailed(
            "Those edges are not in this insight's sentence graph.",
            details={
                "fields": {"masked_edges": f"unknown: {unknown[:8]}"},
                "known_edge_count": len(known),
            },
        )

    extractor = get_extractor(settings)
    before = extractor.infer(rebuilt.graph, rebuilt.pair, None, lemmas=rebuilt.lemmas)  # type: ignore[arg-type]
    after = extractor.infer(
        rebuilt.graph,
        rebuilt.pair,  # type: ignore[arg-type]
        EdgeMask.of(masked_edges, mode=mode),
        lemmas=rebuilt.lemmas,
    )

    context = _graph_context(db, insight.org_id)
    routing_before = _route(insight, before.confidence, context, settings)
    routing_after = _route(insight, after.confidence, context, settings)

    delta = after.confidence - before.confidence
    load_bearing = (
        abs(delta) > settings.ablation_load_bearing_delta or routing_before != routing_after
    )
    interpretation = templates.render(
        templates.Verdict(
            delta_confidence=delta,
            routing_changed=routing_before != routing_after,
            relation_changed=before.relation != after.relation,
            n_edges=len(masked_edges),
            mode=mode,
        ),
        relation_before=before.relation,
        relation_after=after.relation,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    result = AblationResult(
        insight_id=insight.id,
        masked_edges=list(masked_edges),
        mode=mode,
        before=before,
        after=after,
        routing_before=routing_before,
        routing_after=routing_after,
        interpretation=interpretation,
        load_bearing=load_bearing,
        latency_ms=latency_ms,
    )

    if persist:
        db.add(
            AblationRun(
                insight_id=insight.id,
                masked_edges=list(masked_edges),
                mode=mode,
                confidence_before=before.confidence,
                confidence_after=after.confidence,
                vacuity_before=before.vacuity,
                vacuity_after=after.vacuity,
                routing_before=routing_before,
                routing_after=routing_after,
                relation_before=before.relation,
                relation_after=after.relation,
                load_bearing=load_bearing,
                interpretation=interpretation,
                latency_ms=latency_ms,
            )
        )
        db.flush()

    log.info(
        "ablation_run",
        insight_id=str(insight.id),
        edges=len(masked_edges),
        mode=mode,
        delta_confidence=round(delta, 4),
        routing_changed=routing_before != routing_after,
        load_bearing=load_bearing,
        latency_ms=latency_ms,
    )
    return result


def _graph_context(db: Session, org_id: uuid.UUID) -> GraphContext:
    """The org's structural context, so routing is recomputed the same way
    the pipeline computed it."""
    rows = (
        db.execute(
            select(
                Insight.subject_id, Insight.object_id, Insight.relation, Insight.confidence
            ).where(Insight.org_id == org_id)
        )
        .tuples()
        .all()
    )
    return GraphContext.build(
        [
            ClaimContext(subject, obj, relation, confidence)
            for subject, obj, relation, confidence in rows
        ]
    )


def _route(
    insight: Insight, confidence: float, context: GraphContext, settings: Settings
) -> RoutingBucket:
    """What the classical router would say at this confidence.

    Recomputed rather than read off the row, because the whole question is
    what *would* happen if the edge were absent. The graph context is held
    fixed: ablating one sentence's arc does not remove the ownership cycle
    the rest of the corpus asserts.
    """
    return route_claim(
        ClaimContext(
            subject_id=insight.subject_id,
            object_id=insight.object_id,
            relation=insight.relation,
            confidence=confidence,
        ),
        context,
        settings,
    ).bucket


def top_edges(insight: Insight, n: int = 1) -> list[str]:
    """The `n` highest-attention edges on a stored insight, heaviest first.

    Used by the voice layer (Stage 8), which runs an ablation on demand when
    an insight has none, and by the demo script to pick which edges to click.

    `n=1` used to be the whole story. `ml/evals/ablation_report.json` says
    otherwise: across the demo corpus, masking a single edge tops out at
    |Δconf| 0.0806 — under the 0.10 load-bearing bar every time. Masking the
    top four crosses it on 40% of insights and produces routing flips. So the
    unit of causal explanation this model actually supports is a small set of
    edges, not one, and callers should ask for `n=4` as the default rather
    than `n=1` unless they have a specific reason not to.

    `scripts/ablation_report.py --demo` finds the smallest `n` that makes a
    *specific* insight load-bearing, for anyone who wants the exact number
    for the pinned demo insight rather than the corpus-wide default.
    """
    edges = insight.attention or []
    if not edges:
        return []
    ranked = sorted(edges, key=lambda edge: -edge.get("weight", 0.0))
    return [edge["edge_id"] for edge in ranked[: max(n, 0)]]


def top_edge(insight: Insight) -> str | None:
    """The single highest-attention edge. Kept for callers that predate
    `top_edges` — prefer `top_edges(insight, n=4)` for anything demo-facing,
    since one edge alone is not load-bearing on this model (see `top_edges`).
    """
    picked = top_edges(insight, n=1)
    return picked[0] if picked else None
