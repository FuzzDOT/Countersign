"""Does vacuity rise with difficulty? Plan §0 and §4 Stage 4.

    docker compose exec backend python -m scripts.vacuity_report

This is the measurement the whole project rests on. Claim 2 — "our vacuity
score predicts real adversarial fragility" — cannot be true if vacuity is
flat, and the plan is explicit that a flat distribution is a **Stage 4
problem to stop and fix**, because Stage 5 has nothing to correlate against a
constant.

Difficulty is the generator's band (plan §0): A canonical, B varied, C
indirect, D out-of-distribution — held out of the training split entirely.
If the evidential head is doing its job, mean vacuity rises monotonically
A → D.

Two things this does that the ad-hoc version did not, and both changed the
answer:

- **It scores every gold pair, not only the extracted insights.** Band-D
  relations are the ones the model misses, so measuring over insights alone
  measures the band-D examples that happened to be easy. That is
  survivorship, and it flattens the very effect being looked for.
- **It pools the three servable scenarios**, because `meridian_shell_ring`
  on its own contributes two band-D pairs and any statistic over two points
  is decoration.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from collections import defaultdict
from typing import Any

import torch

from core.config import get_settings
from core.logging import configure_logging, get_logger
from data.synth.scenarios import SERVABLE_SCENARIOS
from ml.evidential.uncertainty import trust_from_logits
from ml.relations.interface import NO_RELATION, RELATION_INDEX
from ml.relations.train import build_examples, flatten, score_example

log = get_logger(__name__)

BANDS = ("A", "B", "C", "D")
NO_RELATION_INDEX = RELATION_INDEX[NO_RELATION]

# Spearman over (band rank, vacuity). Reported with its n so a weak
# correlation over few points reads as weak rather than as a result.
BAND_RANK = {band: index for index, band in enumerate(BANDS)}


def _spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return 0.0

    def rank(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
                end += 1
            shared = (position + end) / 2.0
            for index in range(position, end + 1):
                ranks[order[index]] = shared
            position = end + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    mean_x, mean_y = statistics.fmean(rx), statistics.fmean(ry)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry, strict=True))
    denominator = (sum((a - mean_x) ** 2 for a in rx) * sum((b - mean_y) ** 2 for b in ry)) ** 0.5
    return numerator / denominator if denominator else 0.0


def collect() -> dict[str, list[dict[str, float]]]:
    """Every gold-aligned pair across the servable scenarios, scored."""
    from ml.relations.infer import get_extractor

    settings = get_settings()
    extractor = get_extractor(settings)

    by_band: dict[str, list[dict[str, float]]] = defaultdict(list)
    for scenario in SERVABLE_SCENARIOS:
        examples = flatten(build_examples(scenario, seed=settings.pipeline_seed))
        for example in examples:
            if not example.pairs:
                continue
            with torch.no_grad():
                logits = score_example(extractor.model, example)
            confidence, vacuity, dissonance = trust_from_logits(logits)

            for index, band in enumerate(example.bands):
                if example.labels[index] == NO_RELATION_INDEX or not band:
                    continue
                by_band[band].append(
                    {
                        "vacuity": float(vacuity[index]),
                        "confidence": float(confidence[index]),
                        "dissonance": float(dissonance[index]),
                        "correct": float(int(logits[index].argmax()) == example.labels[index]),
                    }
                )
    return by_band


def summarize(by_band: dict[str, list[dict[str, float]]]) -> dict[str, Any]:
    per_band: dict[str, Any] = {}
    xs: list[float] = []
    ys: list[float] = []

    for band in BANDS:
        rows = by_band.get(band, [])
        if not rows:
            continue
        vacuities = [row["vacuity"] for row in rows]
        per_band[band] = {
            "n": len(rows),
            "mean_vacuity": round(statistics.fmean(vacuities), 4),
            "median_vacuity": round(statistics.median(vacuities), 4),
            "p90_vacuity": round(sorted(vacuities)[int(0.9 * (len(vacuities) - 1))], 4),
            "mean_confidence": round(statistics.fmean(row["confidence"] for row in rows), 4),
            "accuracy": round(statistics.fmean(row["correct"] for row in rows), 4),
        }
        for row in rows:
            xs.append(float(BAND_RANK[band]))
            ys.append(row["vacuity"])

    means = [per_band[band]["mean_vacuity"] for band in BANDS if band in per_band]
    return {
        "per_band": per_band,
        "n_pairs": len(xs),
        "spearman_band_vs_vacuity": round(_spearman(xs, ys), 4),
        "monotonic": all(a <= b for a, b in itertools.pairwise(means)),
        "separation_a_to_d": (
            round(per_band["D"]["mean_vacuity"] - per_band["A"]["mean_vacuity"], 4)
            if "A" in per_band and "D" in per_band
            else None
        ),
        "note": (
            "Scored over every gold-aligned candidate pair in the three servable "
            "scenarios, not over extracted insights: band-D relations are the ones "
            "the model misses, so measuring over insights alone measures the "
            "band-D examples that happened to be easy."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Vacuity against difficulty band.")
    parser.add_argument("--write", action="store_true", help="Write ml/evals/vacuity_report.json")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)

    report = summarize(collect())
    print(json.dumps(report, indent=2))

    if args.write:
        path = settings.path("ml/evals") / "vacuity_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
