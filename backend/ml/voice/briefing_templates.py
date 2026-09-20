"""Spoken-briefing sentence construction. Brief §11, plan §4 Stage 8.

Every sentence here is built by string interpolation over fields the
pipeline already computed — a relation, two canonical names, a confidence
score, a routing bucket. Nothing is generated. `tests/test_no_generation.py`
enforces that this package never imports anything that could write a
sentence for us; this module is the reason it can enforce that at all —
there is nowhere else user-visible text comes from.

**This is the canonical phrasing, not a copy of it.** `data/synth/fixtures.py`
built the mock briefing fixture *before* this module existed and had its own
private `_spoken_relation`/`_spoken_routing` — real, working prose, just
living in the wrong place. Stage 1's fixture builder now imports the two
functions below instead of keeping its own copy, which is what makes "the
frontend built against the same wording the real endpoint produces"
structural rather than a coincidence that held up until someone edited one
copy and not the other.
"""

from __future__ import annotations

from dataclasses import dataclass

ORDINALS: tuple[str, ...] = (
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
)

# Characters per second for a natural narration pace. Used to estimate a
# segment's spoken duration when synthesis fails and the segment falls back
# to a template-only, no-audio answer (`ml/voice/answer.py`) — while ElevenLabs
# is reachable, the *real* duration comes from its own timing data instead;
# see plan §4 Stage 8: "approximate is fine, the sync just has to look right."
CHARS_PER_SECOND = 13.5

SEVERITY_RANK: dict[str, int] = {"auto_file": 0, "flag_for_review": 1, "escalate_now": 2}


def rank_by_severity(insights: list, *, limit: int) -> list:
    """Most severe first, ties broken by confidence.

    A listener who stops after one sentence should have heard the worst
    news — brief §11's own framing for why this is severity-first and not
    recency-first. `insights` is any sequence of objects exposing `.routing`
    and `.confidence`; kept duck-typed so both the real ORM `Insight` and the
    fixture builder's own row type can share this one sort.
    """
    return sorted(
        insights,
        key=lambda insight: (SEVERITY_RANK.get(str(insight.routing), 0), insight.confidence),
        reverse=True,
    )[:limit]


def spoken_relation(relation: str) -> str:
    return {
        "WIRED_FUNDS_TO": "routed a payment through",
        "OWNED_BY": "is owned by",
        "INVOICED": "invoiced",
        "SHARES_ADDRESS_WITH": "shares a registered address with",
        "SIGNATORY_OF": "is a signatory of",
    }.get(relation, "is linked to")


def spoken_routing(routing: str) -> str:
    return {
        "escalate_now": "Escalated",
        "flag_for_review": "Flagged for review",
        "auto_file": "Filed",
    }.get(routing, "Filed")


def opening_line(n_items: int) -> str:
    if n_items == 0:
        return "Nothing needs your attention right now."
    noun = "thing" if n_items == 1 else "things"
    return f"{n_items} {noun} need your attention."


def narrate_insight(
    *, subject: str, relation: str, object_: str, confidence: float, routing: str
) -> str:
    """One spoken sentence for one insight, without its leading ordinal.

    The ordinal is applied by the caller (`ml/voice/answer.py` numbers a
    ranked list; the fixture builder does the same over its own rows) so
    this function has exactly one job: turn structured fields into a
    sentence, the same job every other template module in this codebase
    does.
    """
    return (
        f"{subject} {spoken_relation(relation)} {object_}. "
        f"Confidence {round(confidence * 100)} percent. "
        f"{spoken_routing(routing)}."
    )


def estimated_duration_ms(text: str) -> int:
    return int(len(text) / CHARS_PER_SECOND * 1000)


@dataclass(frozen=True, slots=True)
class ExplainFlagContext:
    subject: str
    relation: str
    object_: str
    document_title: str
    src_token: str
    dst_token: str
    confidence_points: int


def narrate_explain_flag(context: ExplainFlagContext) -> str:
    """The spoken answer to "why is this flagged" — a citation plus the
    specific causal claim the ablation run supports, not a summary of the
    insight in general. `ml/voice/answer.py` is what runs the ablation (or
    reads a recent one) and turns its result into this context object;
    this function's only job is the sentence.

    Wording matches `data/synth/fixtures.py`'s mock answer character for
    character — the frontend was built against that fixture, so a rewrite
    that "improved" the phrasing here would be a silent contract break.
    """
    return (
        f"{context.subject} is flagged because "
        f"{spoken_relation(context.relation)} "
        f"{context.object_} on {context.document_title}. "
        f"The dependency link between '{context.src_token}' and "
        f"'{context.dst_token}' accounts for {context.confidence_points} "
        "points of confidence — removing it downgrades the flag, so that "
        "connection is what's driving this."
    )
