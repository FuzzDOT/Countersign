"""Tune the routing bands and the vacuity gate against the corpus.

    docker compose exec backend python -m scripts.tune_gate
    docker compose exec backend python -m scripts.tune_gate --write

The brief's 0.45 is a guess and the definition of done requires an escalation
rate in 8–20%. So the thresholds are swept over the *demo* scenario, the
value landing nearest the target is chosen, and the choice is then verified
on `clean_baseline` — a corpus with no fraud in it, which is what catches a
gate tuned so aggressively that it cries wolf.

**Nothing here is fitted to the ground-truth routing labels.** Tuning a
decision threshold on the labels it is about to be scored against is marking
your own homework, and `GET /evals/routing` would report an accuracy that
means nothing. The objective is the *rate* the definition of done specifies;
the accuracy that falls out is reported honestly in Stage 6.

`--write` prints the env lines to paste into `.env`; it does not edit the
file, because a script that rewrites configuration behind your back is how a
demo machine ends up in a state nobody can reproduce.
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from core.config import Settings, get_settings
from core.logging import configure_logging, get_logger
from db.models import Insight
from db.session import db_session
from ml.cascade.gate import POLICY, escalation_rate, should_escalate
from ml.cascade.routing import ClaimContext, GraphContext, score
from workers.pipeline import IngestPipeline, create_job
from workers.seed import seed_documents

log = get_logger(__name__)

# Definition of done, brief §16. The midpoint is the target rather than an
# edge, so a corpus that shifts slightly does not fall out of band.
TARGET_RATE = 0.13
RATE_BAND = (0.08, 0.20)

# `clean_baseline` has no fraud. Escalating more than this share of it means
# the gate is crying wolf, whatever it does on the demo scenario.
CLEAN_ESCALATION_CEILING = 0.08

# A review queue holding this much of a corpus with no fraud in it is a queue
# nobody works. Softer than the escalation ceiling because flagging is cheap
# and the definition of done does not name a number for it.
CLEAN_FLAG_TARGET = 0.20

FLAG_CANDIDATES = tuple(round(0.20 + 0.025 * step, 3) for step in range(17))
ESCALATE_CANDIDATES = tuple(round(0.45 + 0.025 * step, 3) for step in range(17))
VACUITY_CANDIDATES = tuple(round(0.20 + 0.025 * step, 3) for step in range(25))


@dataclass(frozen=True, slots=True)
class Sample:
    """One insight reduced to what the thresholds act on."""

    subject_id: uuid.UUID
    object_id: uuid.UUID
    relation: str
    confidence: float
    vacuity: float
    dissonance: float


def load_samples(scenario: str, org_id: uuid.UUID, settings: Settings) -> list[Sample]:
    """Ingest a scenario into a scratch org and read back what it produced."""
    with db_session() as db:
        result = seed_documents(db, org_id=org_id, scenario=scenario, settings=settings)
        document_ids = result.document_ids or list(
            db.execute(select(Insight.document_id).where(Insight.org_id == org_id)).scalars()
        )
        job = create_job(db, org_id=org_id, document_ids=document_ids, scenario=scenario)
        db.commit()
        IngestPipeline(db, job, settings).run()

        rows = db.execute(select(Insight).where(Insight.org_id == org_id)).scalars().all()
        return [
            Sample(
                subject_id=row.subject_id,
                object_id=row.object_id,
                relation=row.relation,
                confidence=row.confidence,
                vacuity=row.vacuity,
                dissonance=row.dissonance,
            )
            for row in rows
        ]


def routing_rates(samples: list[Sample], settings: Settings) -> tuple[float, float, float]:
    """`(auto_file, flag, escalate)` shares under the current thresholds."""
    claims = [ClaimContext(s.subject_id, s.object_id, s.relation, s.confidence) for s in samples]
    context = GraphContext.build(claims)
    counts = {"auto_file": 0, "flag_for_review": 0, "escalate_now": 0}
    for claim in claims:
        counts[score(claim, context, settings).bucket.value] += 1
    total = max(len(claims), 1)
    return (
        counts["auto_file"] / total,
        counts["flag_for_review"] / total,
        counts["escalate_now"] / total,
    )


def gate_rate(samples: list[Sample], settings: Settings) -> float:
    claims = [ClaimContext(s.subject_id, s.object_id, s.relation, s.confidence) for s in samples]
    context = GraphContext.build(claims)
    decisions = [
        should_escalate(
            vacuity=sample.vacuity,
            dissonance=sample.dissonance,
            confidence=sample.confidence,
            classical=score(claim, context, settings).bucket,
            settings=settings,
        )
        for sample, claim in zip(samples, claims, strict=True)
    ]
    return escalation_rate(decisions)


def sweep_routing(demo: list[Sample], clean: list[Sample], settings: Settings) -> dict[str, float]:
    """Pick the bands whose escalation rate lands nearest the target.

    Ties break toward the *higher* flag threshold, which produces a smaller
    review queue for the same escalation rate — an analyst's attention is
    the scarce resource, not the model's.
    """
    best: tuple[float, float, float] | None = None
    best_score = float("inf")

    for escalate_at in ESCALATE_CANDIDATES:
        for flag_at in FLAG_CANDIDATES:
            if flag_at >= escalate_at:
                continue
            candidate = settings.model_copy(
                update={
                    "routing_flag_threshold": flag_at,
                    "routing_escalate_threshold": escalate_at,
                }
            )
            _, flagged, escalated = routing_rates(demo, candidate)
            _, clean_flagged, clean_escalated = routing_rates(clean, candidate)

            if not RATE_BAND[0] <= escalated <= RATE_BAND[1]:
                continue
            if clean_escalated > CLEAN_ESCALATION_CEILING:
                continue

            # Distance from the target rate, then a mild preference for a
            # review queue that is not most of the feed.
            penalty = (
                abs(escalated - TARGET_RATE)
                + 0.25 * max(flagged - 0.35, 0.0)
                + 0.50 * max(clean_flagged - CLEAN_FLAG_TARGET, 0.0)
            )
            if penalty < best_score:
                best_score, best = penalty, (flag_at, escalate_at, escalated)

    if best is None:
        raise SystemExit(
            "no threshold pair put escalation inside the 8-20% band while keeping "
            "clean_baseline quiet. The corpus or the risk rule needs attention, "
            "not the thresholds."
        )
    return {"flag": best[0], "escalate": best[1], "rate": round(best[2], 4)}


def sweep_vacuity(demo: list[Sample], settings: Settings) -> dict[str, float]:
    """Pick the vacuity gate so the cascade calls the LLM on ~13% of insights."""
    best_threshold = settings.vacuity_gate_threshold
    best_penalty = float("inf")
    best_rate = 0.0

    for threshold in VACUITY_CANDIDATES:
        candidate = settings.model_copy(update={"vacuity_gate_threshold": threshold})
        rate = gate_rate(demo, candidate)
        if not RATE_BAND[0] <= rate <= RATE_BAND[1]:
            continue
        penalty = abs(rate - TARGET_RATE)
        if penalty < best_penalty:
            best_penalty, best_threshold, best_rate = penalty, threshold, rate

    if best_penalty == float("inf"):
        raise SystemExit(
            "no vacuity threshold put the gate inside the 8-20% band. Either the "
            "head is miscalibrated or the corpus has no epistemic spread — both "
            "are Stage 4 problems, not Stage 6 ones (plan §4)."
        )
    return {"threshold": best_threshold, "rate": round(best_rate, 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Tune the routing bands and the gate.")
    parser.add_argument("--write", action="store_true", help="Print .env lines to paste.")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)

    # Scratch organizations, so tuning never touches the demo tenant's data.
    demo_org, clean_org = uuid.uuid4(), uuid.uuid4()
    from scripts.seed import ensure_org, reset_org

    with db_session() as db:
        ensure_org(
            db, demo_org, "Tuning (demo)", f"tune-{demo_org.hex[:8]}@example.com", uuid.uuid4()
        )
        ensure_org(
            db, clean_org, "Tuning (clean)", f"tune-{clean_org.hex[:8]}@example.com", uuid.uuid4()
        )

    demo = load_samples("meridian_shell_ring", demo_org, settings)
    clean = load_samples("clean_baseline", clean_org, settings)

    bands = sweep_routing(demo, clean, settings)
    tuned = settings.model_copy(
        update={
            "routing_flag_threshold": bands["flag"],
            "routing_escalate_threshold": bands["escalate"],
        }
    )
    gate = sweep_vacuity(demo, tuned)

    auto, flagged, escalated = routing_rates(demo, tuned)
    clean_auto, clean_flagged, clean_escalated = routing_rates(clean, tuned)

    report = {
        "policy": POLICY,
        "n_demo_insights": len(demo),
        "n_clean_insights": len(clean),
        "routing_flag_threshold": bands["flag"],
        "routing_escalate_threshold": bands["escalate"],
        "vacuity_gate_threshold": gate["threshold"],
        "demo": {
            "auto_file": round(auto, 4),
            "flag_for_review": round(flagged, 4),
            "escalate_now": round(escalated, 4),
            "gate_escalation_rate": gate["rate"],
        },
        "clean_baseline": {
            "auto_file": round(clean_auto, 4),
            "flag_for_review": round(clean_flagged, 4),
            "escalate_now": round(clean_escalated, 4),
        },
        "target_band": list(RATE_BAND),
        "note": (
            "Tuned against the escalation *rate* the definition of done specifies, "
            "never against the ground-truth routing labels — those are what "
            "GET /evals/routing scores, and fitting the thresholds to them would "
            "make that number meaningless."
        ),
    }

    path = settings.path("ml/evals") / "gate_tuning.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if args.write:
        print("\n# paste into .env")
        print(f"ROUTING_FLAG_THRESHOLD={bands['flag']}")
        print(f"ROUTING_ESCALATE_THRESHOLD={bands['escalate']}")
        print(f"VACUITY_GATE_THRESHOLD={gate['threshold']}")

    with db_session() as db:
        reset_org(db, demo_org)
        reset_org(db, clean_org)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
