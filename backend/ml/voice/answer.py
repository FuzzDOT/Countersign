"""Turning a resolved intent into an `AskResponse`. Brief §11.

Every branch here reads structured fields and fills a template; none of
them write a sentence from scratch. The one intent that looks like it might
— `explain_flag` — is the one this module works hardest to keep honest: the
answer names a specific attention edge and a specific confidence delta,
both read off a real ablation run rather than asserted, because "the model
is somehow suspicious of this" is not the kind of claim this project makes
anywhere else and voice should not be where that standard slips.

**Entity resolution is deliberately simple.** A case-insensitive substring
match of each of the org's entity names and aliases against the heard text,
picking the longest match. That is the same tradeoff `ml/entities/coref.py`
already made and stated (`entity_coref_threshold`, plan §1.5): "will
occasionally merge two things a human wouldn't" is known, real debt, not a
bug discovered later. Five rehearsed phrasings of one company's name is
exactly the case this handles well; two companies with very similar names
mentioned in the same breath is exactly the case it would not, and nothing
here pretends otherwise.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import AblationRun, Document, Entity, Insight, RoutingBucket
from ml.ablation.engine import ablate, top_edges
from ml.voice.intent import (
    CONFIDENCE_QUERY,
    DISMISS,
    ENTITY_SUMMARY,
    EXPLAIN_FLAG,
    LIST_FLAGGED,
    SHOW_SOURCE,
    UNKNOWN,
)

# How many of an entity's edges to mask when no ablation exists yet for the
# insight in question. Plan §4 Stage 8: "running one on the top-attention
# edge if none exists" — brief's original phrasing, from before
# `ml/evals/ablation_report.json` (Stage 7) measured that a single edge is
# not load-bearing on this model (max |Δconf| 0.0806 across the demo corpus,
# under the 0.10 bar every time). Masking one edge and truthfully reporting
# whatever small delta it produces would technically satisfy the brief's
# words while repeating the mistake Stage 7 found and fixed. Four is the
# smallest count the measured report showed crossing the bar on a meaningful
# share of insights — see docs/STATE.md, "Stage 7, round 2".
FALLBACK_ABLATION_EDGE_COUNT = 4

SEVERITY_RANK: dict[str, int] = {"auto_file": 0, "flag_for_review": 1, "escalate_now": 2}

REPHRASE_TEMPLATE = (
    "I didn't catch a clear question there. You can ask me to explain why "
    "something's flagged, show the source, list what's flagged, summarize a "
    "company, or ask how confident a call is."
)


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    resolved_insight_id: uuid.UUID | None
    answer_text: str
    citation: dict[str, Any] | None
    ablation_run_id: uuid.UUID | None


# Legal suffixes are common across many unrelated entities in one corpus —
# matching on "LLC" alone would treat every LLC in the org as a hit for
# whichever one happens to be resolved first. Excluded from the word-level
# match below so only the actually-distinguishing words count.
_LEGAL_SUFFIXES = frozenset({"llc", "inc", "ltd", "corp", "lp", "co", "company"})


def resolve_entity(db: Session, org_id: uuid.UUID, heard: str) -> Entity | None:
    """The entity whose name shares the most (non-suffix) words with `heard`.

    **Word-level, not substring.** A first version checked whether the
    entity's *full* canonical name appeared inside `heard` — backward: a
    person says "why is Meridian flagged," not "why is Meridian Supply LLC
    flagged." Checking the full name against the utterance meant every one
    of the plan's five rehearsed phrasings failed to resolve at all, since
    none of them contain a company's complete legal name — caught by
    `tests/test_voice_api.py`'s own exit-criterion test, which is exactly
    what that test is for.

    Longest total matched-word length wins, so "Meridian Supply" (if ever
    said together) would beat a coincidental one-word hit elsewhere — still
    deliberately simple, see module docstring.
    """
    heard_words = set(re.findall(r"[a-z0-9]+", heard.lower()))
    best: tuple[int, Entity] | None = None
    for entity in db.execute(select(Entity).where(Entity.org_id == org_id)).scalars():
        for candidate in (entity.canonical, *entity.aliases):
            if not candidate:
                continue
            candidate_words = [
                w for w in re.findall(r"[a-z0-9]+", candidate.lower()) if w not in _LEGAL_SUFFIXES
            ]
            matched = [w for w in candidate_words if w in heard_words]
            if not matched:
                continue
            score = sum(len(w) for w in matched)
            if best is None or score > best[0]:
                best = (score, entity)
    return best[1] if best else None


def _most_relevant_insight(db: Session, org_id: uuid.UUID, entity: Entity) -> Insight | None:
    """The insight most worth talking about for this entity: most severe,
    then most confident, then most recent. Deterministic given a fixed
    corpus, which is what "five phrasings resolve to the same insight"
    actually requires."""
    insights = list(
        db.execute(
            select(Insight).where(
                Insight.org_id == org_id,
                (Insight.subject_id == entity.id) | (Insight.object_id == entity.id),
            )
        ).scalars()
    )
    if not insights:
        return None
    return max(
        insights,
        key=lambda i: (
            SEVERITY_RANK.get(str(i.routing), 0),
            i.confidence,
            i.created_at,
        ),
    )


def _citation(insight: Insight, db: Session) -> dict[str, Any]:
    document = db.get(Document, insight.document_id)
    return {
        "document_id": str(insight.document_id),
        "document_title": document.title if document else "",
        "char_start": insight.char_start,
        "char_end": insight.char_end,
        "sentence_text": insight.sentence_text,
    }


def _recent_ablation(db: Session, insight_id: uuid.UUID) -> AblationRun | None:
    return db.execute(
        select(AblationRun)
        .where(AblationRun.insight_id == insight_id)
        .order_by(AblationRun.created_at.desc())
        .limit(1)
    ).scalars().first()


def _ensure_ablation(db: Session, insight: Insight) -> AblationRun:
    """The insight's most recent ablation, or a fresh one over its top edges.

    See `FALLBACK_ABLATION_EDGE_COUNT` for why this masks four edges rather
    than the one the brief's original prose named.
    """
    existing = _recent_ablation(db, insight.id)
    if existing is not None:
        return existing

    edges = top_edges(insight, n=FALLBACK_ABLATION_EDGE_COUNT)
    ablate(db, insight, edges, mode="zero")
    db.flush()
    run = _recent_ablation(db, insight.id)
    if run is None:  # pragma: no cover - ablate() always persists on success
        raise RuntimeError("ablation did not persist a run")
    return run


def _most_severe_insight(db: Session, org_id: uuid.UUID) -> Insight | None:
    """The single most severe, most confident insight org-wide — the same
    "top" pick `_list_flagged` already uses, reused here as the fallback for
    a contextless "why is this flagged"."""
    insights = list(db.execute(select(Insight).where(Insight.org_id == org_id)).scalars())
    if not insights:
        return None
    return max(insights, key=lambda i: (SEVERITY_RANK.get(str(i.routing), 0), i.confidence))


def _explain_flag(
    db: Session, org_id: uuid.UUID, heard: str, context_insight_id: uuid.UUID | None = None
) -> ResolvedAnswer:
    from ml.voice.briefing_templates import ExplainFlagContext, narrate_explain_flag

    entity = resolve_entity(db, org_id, heard)

    if entity is not None:
        insight = _most_relevant_insight(db, org_id, entity)
        if insight is None:
            return ResolvedAnswer(
                resolved_insight_id=None,
                answer_text=f"I don't have anything flagged involving {entity.canonical} right now.",
                citation=None,
                ablation_run_id=None,
            )
    elif context_insight_id is not None:
        # "Why is *this* flagged" — no name spoken because the frontend
        # already has a specific insight open; that context, not a guess
        # from the heard text, is what "this" refers to.
        candidate = db.get(Insight, context_insight_id)
        insight = candidate if candidate is not None and candidate.org_id == org_id else None
    else:
        # Neither a name nor an open insight to anchor "this" to. Rather
        # than a flat refusal, fall back to the single most severe insight
        # org-wide — the same default `_list_flagged` already uses for "give
        # me the headline" — since a contextless "why is this flagged" is
        # closer to "what's the worst thing right now" than to a genuine
        # unresolvable reference.
        insight = _most_severe_insight(db, org_id)

    if insight is None:
        return ResolvedAnswer(
            resolved_insight_id=None,
            answer_text=(
                "I didn't catch which company or person you meant, and nothing's "
                'flagged right now to fall back to. Try naming it directly, like '
                '"why is Meridian flagged".'
            ),
            citation=None,
            ablation_run_id=None,
        )

    document = db.get(Document, insight.document_id)
    run = _ensure_ablation(db, insight)

    edge_lookup = {edge.get("edge_id"): edge for edge in (insight.attention or [])}
    top_edge_id = run.masked_edges[0] if run.masked_edges else None
    top_edge = edge_lookup.get(top_edge_id, {})

    context = ExplainFlagContext(
        subject=_canonical(db, insight.subject_id),
        relation=insight.relation,
        object_=_canonical(db, insight.object_id),
        document_title=document.title if document else "",
        src_token=top_edge.get("src_token", "?"),
        dst_token=top_edge.get("dst_token", "?"),
        confidence_points=round(abs(run.confidence_after - run.confidence_before) * 100),
    )
    return ResolvedAnswer(
        resolved_insight_id=insight.id,
        answer_text=narrate_explain_flag(context),
        citation=_citation(insight, db),
        ablation_run_id=run.id,
    )


def _show_source(db: Session, org_id: uuid.UUID, heard: str, context_insight_id: uuid.UUID | None) -> ResolvedAnswer:
    insight = _resolve_context_insight(db, org_id, heard, context_insight_id)
    if insight is None:
        return ResolvedAnswer(
            resolved_insight_id=None,
            answer_text="I don't have a specific source to show for that.",
            citation=None,
            ablation_run_id=None,
        )
    return ResolvedAnswer(
        resolved_insight_id=insight.id,
        answer_text=f"Here's the source sentence: \"{insight.sentence_text}\"",
        citation=_citation(insight, db),
        ablation_run_id=None,
    )


def _list_flagged(db: Session, org_id: uuid.UUID) -> ResolvedAnswer:
    insights = list(
        db.execute(
            select(Insight).where(
                Insight.org_id == org_id, Insight.routing != RoutingBucket.auto_file
            )
        ).scalars()
    )
    if not insights:
        return ResolvedAnswer(
            resolved_insight_id=None,
            answer_text="Nothing is flagged right now.",
            citation=None,
            ablation_run_id=None,
        )
    escalated = sum(1 for i in insights if i.routing is RoutingBucket.escalate_now)
    flagged = len(insights) - escalated
    parts = []
    if escalated:
        parts.append(f"{escalated} escalated")
    if flagged:
        parts.append(f"{flagged} flagged for review")
    top = max(insights, key=lambda i: (SEVERITY_RANK.get(str(i.routing), 0), i.confidence))
    return ResolvedAnswer(
        resolved_insight_id=top.id,
        answer_text=(
            f"There are {' and '.join(parts)}. The most severe one involves "
            f"{_canonical(db, top.subject_id)} and {_canonical(db, top.object_id)}."
        ),
        citation=_citation(top, db),
        ablation_run_id=None,
    )


def _entity_summary(db: Session, org_id: uuid.UUID, heard: str) -> ResolvedAnswer:
    entity = resolve_entity(db, org_id, heard)
    if entity is None:
        return ResolvedAnswer(
            resolved_insight_id=None,
            answer_text="I didn't catch which company or person you meant.",
            citation=None,
            ablation_run_id=None,
        )
    insights = list(
        db.execute(
            select(Insight).where(
                Insight.org_id == org_id,
                (Insight.subject_id == entity.id) | (Insight.object_id == entity.id),
            )
        ).scalars()
    )
    escalated = sum(1 for i in insights if i.routing is RoutingBucket.escalate_now)
    top = _most_relevant_insight(db, org_id, entity)
    summary = (
        f"{entity.canonical} appears in {len(insights)} "
        f"{'insight' if len(insights) == 1 else 'insights'}"
        f"{f', {escalated} escalated' if escalated else ''}."
    )
    return ResolvedAnswer(
        resolved_insight_id=top.id if top else None,
        answer_text=summary,
        citation=_citation(top, db) if top else None,
        ablation_run_id=None,
    )


def _confidence_query(db: Session, org_id: uuid.UUID, heard: str, context_insight_id: uuid.UUID | None) -> ResolvedAnswer:
    insight = _resolve_context_insight(db, org_id, heard, context_insight_id)
    if insight is None:
        return ResolvedAnswer(
            resolved_insight_id=None,
            answer_text="I don't have a specific insight to give you a confidence number for.",
            citation=None,
            ablation_run_id=None,
        )
    return ResolvedAnswer(
        resolved_insight_id=insight.id,
        answer_text=(
            f"Confidence is {round(insight.confidence * 100)} percent, with a vacuity "
            f"score of {round(insight.vacuity, 2)}."
        ),
        citation=_citation(insight, db),
        ablation_run_id=None,
    )


def _dismiss() -> ResolvedAnswer:
    return ResolvedAnswer(
        resolved_insight_id=None,
        answer_text="Okay, dismissed.",
        citation=None,
        ablation_run_id=None,
    )


def _unknown() -> ResolvedAnswer:
    return ResolvedAnswer(
        resolved_insight_id=None,
        answer_text=REPHRASE_TEMPLATE,
        citation=None,
        ablation_run_id=None,
    )


def _resolve_context_insight(
    db: Session, org_id: uuid.UUID, heard: str, context_insight_id: uuid.UUID | None
) -> Insight | None:
    """A caller-supplied insight id wins outright — it means "the one I'm
    already looking at" — and only falls back to entity matching when none
    was given."""
    if context_insight_id is not None:
        insight = db.get(Insight, context_insight_id)
        if insight is not None and insight.org_id == org_id:
            return insight
    entity = resolve_entity(db, org_id, heard)
    return _most_relevant_insight(db, org_id, entity) if entity else None


def _canonical(db: Session, entity_id: uuid.UUID) -> str:
    entity = db.get(Entity, entity_id)
    return entity.canonical if entity else "?"


def answer(
    db: Session,
    org_id: uuid.UUID,
    *,
    intent: str,
    heard: str,
    context_insight_id: uuid.UUID | None = None,
) -> ResolvedAnswer:
    """Dispatch to the one function that knows how to answer this intent."""
    if intent == EXPLAIN_FLAG:
        return _explain_flag(db, org_id, heard, context_insight_id)
    if intent == SHOW_SOURCE:
        return _show_source(db, org_id, heard, context_insight_id)
    if intent == LIST_FLAGGED:
        return _list_flagged(db, org_id)
    if intent == ENTITY_SUMMARY:
        return _entity_summary(db, org_id, heard)
    if intent == CONFIDENCE_QUERY:
        return _confidence_query(db, org_id, heard, context_insight_id)
    if intent == DISMISS:
        return _dismiss()
    if intent == UNKNOWN:
        return _unknown()
    raise ValueError(f"unhandled intent: {intent!r}")  # pragma: no cover - exhaustive above
