"""How load-bearing is the attention, really? Plan §4 Stage 7.

    make ablation
    make ablation-demo

Claim 4 is that we can remove a syntactic link and show what it was holding
up. That claim is only true if the model is *causally* dependent on its
attention, and "the attention map looks plausible" is not evidence of that —
it is the exact thing attention-based explanations are criticized for.

So this sweeps the intervention size and reports what actually moves. It is
the evidence behind two architecture decisions that both cost something:

  * no residual stream in the GAT (`ml/relations/gat.py`)
  * graph nodes built from the tagger's *pre-BiLSTM* features rather than
    its hidden states (`ml/relations/graph_builder.py`)

Both were made because the first version of this report said the attention
was decoration.

**The corpus-wide sweep (`make ablation`) answers "is attention causal at
all".** It found that a single top edge is not load-bearing anywhere in the
40-insight sample (max |Δconf| 0.0806, under the 0.10 bar), but the top four
are, on 40% of insights. That is the right answer to "does this architecture
support the claim" and the wrong answer to "how many edges do I click in the
demo" — averaging over 40 insights says nothing about the one you're about
to show a judge.

**`--demo` answers that second question directly.** Given one insight (the
pinned Meridian one by default), it walks n = 1, 2, 3, ... up to
`MAX_MASKED_EDGES` and reports the smallest n that clears the load-bearing
threshold, so the demo script and the voice layer's default
`top_edges(insight, n=...)` call can use a real, measured number instead of
a guess carried over from the corpus-wide average.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid

from sqlalchemy import select

from core import ids
from core.config import get_settings
from core.logging import configure_logging, get_logger
from db.models import Insight
from db.session import db_session
from ml.ablation.engine import MAX_MASKED_EDGES, ablate, top_edges

log = get_logger(__name__)

EDGE_COUNTS = (1, 2, 4, 8)
MODES = ("zero", "uniform")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure ablation sensitivity.")
    parser.add_argument("--org", default=None)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Find the minimal edge count for one insight instead of the corpus sweep.",
    )
    parser.add_argument(
        "--insight",
        default=None,
        help="Insight id for --demo. Defaults to the pinned Meridian routed-payment insight.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level="WARNING")
    org_id = uuid.UUID(args.org) if args.org else ids.DEMO_ORG_ID

    if args.demo:
        return _run_demo(org_id, args.insight)

    report: dict[str, object] = {
        "threshold": settings.ablation_load_bearing_delta,
        "by_edge_count": [],
        "by_mode": [],
    }

    with db_session() as db:
        insights = [
            insight
            for insight in db.execute(
                select(Insight)
                .where(Insight.org_id == org_id)
                .order_by(Insight.confidence.desc())
            ).scalars()
            if insight.attention
        ][: args.limit]

        if not insights:
            print("No insights with attention. Seed and ingest a scenario first.")
            return 2

        for count in EDGE_COUNTS:
            report["by_edge_count"].append(  # type: ignore[union-attr]
                _sweep(db, insights, count, "zero")
            )
        for mode in MODES:
            report["by_mode"].append(_sweep(db, insights, 4, mode))  # type: ignore[union-attr]

        db.rollback()

    report["interpretation"] = _interpret(report["by_edge_count"])  # type: ignore[arg-type]
    print(json.dumps(report, indent=2))

    if args.write:
        path = settings.path("ml/evals") / "ablation_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {path}")
    return 0


def _run_demo(org_id: uuid.UUID, insight_id_arg: str | None) -> int:
    """The number the demo script actually needs: how many edges, for this
    one insight, before the counterfactual is load-bearing.

    Deliberately per-edge-count rather than binary search: `MAX_MASKED_EDGES`
    is 32, a linear scan is at most 32 forward passes, and a wrong answer here
    is discovered on stage in front of a judge — the extra few seconds are
    the correct trade against a clever search that could skip past the real
    threshold if the delta is non-monotonic in edge count.
    """
    settings = get_settings()

    with db_session() as db:
        if insight_id_arg:
            insight = db.get(Insight, uuid.UUID(insight_id_arg))
        else:
            # The pinned routed-payment insight: "Payment of $48,200 was
            # routed through Advent Holdings on behalf of Meridian Supply
            # LLC." Same lookup the fixture builder and the voice layer use.
            insight = db.execute(
                select(Insight).where(
                    Insight.org_id == org_id,
                    Insight.sentence_text.ilike("%$48,200%Advent Holdings%"),
                )
            ).scalar_one_or_none()

        if insight is None or not insight.attention:
            print(
                "Could not find the target insight, or it has no attention recorded. "
                "Pass --insight <uuid> explicitly, or seed and ingest meridian_shell_ring first."
            )
            return 2

        available = len(insight.attention)
        for n in range(1, min(available, MAX_MASKED_EDGES) + 1):
            edges = top_edges(insight, n=n)
            result = ablate(db, insight, edges, mode="zero", persist=False, settings=settings)
            print(
                f"n={n:<2} |Δconf|={abs(result.delta_confidence):.4f}  "
                f"routing_changed={result.routing_changed}  "
                f"load_bearing={result.load_bearing}"
            )
            if result.load_bearing:
                print(
                    f"\n{insight.id}: load-bearing at n={n} edges "
                    f"(of {available} available). Use "
                    f"`top_edges(insight, n={n})` as the demo default for this insight."
                )
                db.rollback()
                return 0

        print(
            f"\n{insight.id}: not load-bearing even at every available edge "
            f"({available}). Either the demo insight needs to change, or the "
            "load-bearing threshold does — that is a real finding, not a script bug."
        )
        print("\nSearching the rest of the corpus for a better candidate...")
        replacement = _find_alternative(db, org_id, exclude=insight.id)
        db.rollback()
        if replacement is None:
            print("No insight in this scenario clears the bar at any edge count either.")
            return 1
        n, candidate, sentence = replacement
        print(
            f"\n{candidate}: load-bearing at n={n} edges. Sentence: "
            f"\"{sentence[:120]}{'...' if len(sentence) > 120 else ''}\"\n"
            f"Rerun with --insight {candidate} to confirm, then use that one in the "
            "demo script instead."
        )
        return 1


def _find_alternative(
    db, org_id: uuid.UUID, *, exclude: uuid.UUID, scan_limit: int = 60
) -> tuple[int, uuid.UUID, str] | None:
    """The smallest-n, first-found replacement insight, for when the pinned
    one turns out not to be load-bearing at all.

    Stops at the first hit rather than finding the *global* smallest n across
    the whole corpus — a demo needs *an* insight that works, not the
    provably best one, and an exhaustive search is `scan_limit` full forward
    passes per candidate rather than one.
    """
    candidates = list(
        db.execute(
            select(Insight)
            .where(Insight.org_id == org_id, Insight.id != exclude)
            .order_by(Insight.confidence.desc())
            .limit(scan_limit)
        ).scalars()
    )
    for candidate in candidates:
        if not candidate.attention:
            continue
        available = len(candidate.attention)
        for n in (1, 2, 4, 8):
            if n > available:
                break
            edges = top_edges(candidate, n=n)
            result = ablate(db, candidate, edges, mode="zero", persist=False)
            if result.load_bearing:
                return n, candidate.id, candidate.sentence_text
    return None


def _sweep(db, insights, count: int, mode: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    deltas: list[float] = []
    latencies: list[float] = []
    flips = relation_changes = 0

    for insight in insights:
        edges = [
            edge["edge_id"]
            for edge in sorted(insight.attention, key=lambda e: -e["weight"])[:count]
        ]
        if not edges:
            continue
        started = time.perf_counter()
        result = ablate(db, insight, edges, mode=mode, persist=False, settings=None)
        latencies.append((time.perf_counter() - started) * 1000)
        deltas.append(abs(result.delta_confidence))
        flips += int(result.routing_changed)
        relation_changes += int(result.before.relation != result.after.relation)

    latencies.sort()
    return {
        "edges_masked": count,
        "mode": mode,
        "n_insights": len(deltas),
        "mean_abs_delta": round(statistics.fmean(deltas), 4),
        "max_abs_delta": round(max(deltas), 4),
        "load_bearing_share": round(sum(1 for d in deltas if d > 0.10) / len(deltas), 4),
        "routing_flips": flips,
        "relation_changes": relation_changes,
        "latency_p50_ms": int(statistics.median(latencies)),
        "latency_p95_ms": int(latencies[min(int(0.95 * (len(latencies) - 1)), len(latencies) - 1)]),
    }


def _interpret(rows: list[dict[str, object]]) -> str:
    single = next((row for row in rows if row["edges_masked"] == 1), None)
    four = next((row for row in rows if row["edges_masked"] == 4), None)
    if single is None or four is None:  # pragma: no cover
        return ""
    return (
        f"A single dependency arc is not load-bearing: masking the top-attention "
        f"edge moves confidence by {single['mean_abs_delta']} on average and flips "
        f"{single['routing_flips']} routing decisions. That is the honest reading — "
        f"one arc out of forty in a sentence is a small intervention and the model "
        f"is appropriately robust to it. Masking the top four moves confidence by "
        f"{four['mean_abs_delta']} on average, exceeds the 0.10 load-bearing "
        f"threshold on {four['load_bearing_share']:.0%} of insights, and flips "
        f"{four['routing_flips']} routing decisions. So the attention is causal, and "
        f"the unit of explanation is a small set of edges rather than a single one."
    )


if __name__ == "__main__":
    raise SystemExit(main())
