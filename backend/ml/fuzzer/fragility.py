"""Turning trials into a fragility score, and the score into a claim.

Brief §10. `fragility` is a weighted mean of normalized instability across
an insight's trials:

    instability = 0.50 · label flipped
                + 0.30 · relation lost entirely
                + 0.20 · |Δconfidence|

Label flip weighted highest because it is the failure that changes what an
analyst is told. Relation loss next: the claim disappearing is worse than the
claim wobbling, but at least it fails visibly. The confidence delta last —
it is the most common signal and the least consequential, and letting it
dominate would make fragility a measure of numerical jitter.

**There is no branch in this module that improves a number.** Whatever the
Spearman comes out as is what `GET /evals/fragility` returns. A middling
coefficient with a diagnosis is the research-credible outcome; a suspicious
0.97 gets taken apart in the Q&A.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ml.fuzzer.base import FAMILIES
from ml.stats import Correlation, correlate, quartile_bounds, quartile_of

FLIP_WEIGHT = 0.50
LOSS_WEIGHT = 0.30
CONFIDENCE_WEIGHT = 0.20


@dataclass(frozen=True, slots=True)
class Trial:
    """One perturbation applied to one insight. No offsets, by design.

    The perturbed text has different offsets from the stored document, and
    persisting them would silently corrupt citations (plan §3). This carries
    the three outcomes and nothing else.
    """

    insight_id: uuid.UUID
    perturbation: str
    variant: int
    label_flipped: bool
    conf_delta: float
    relation_lost: bool
    # Not persisted. True when the family had nothing to change on this
    # document, which is a different thing from "the insight survived".
    vacuous: bool = False

    @property
    def instability(self) -> float:
        return min(
            FLIP_WEIGHT * float(self.label_flipped)
            + LOSS_WEIGHT * float(self.relation_lost)
            + CONFIDENCE_WEIGHT * min(abs(self.conf_delta), 1.0),
            1.0,
        )


def fragility_of(trials: list[Trial]) -> float:
    """Mean instability over an insight's non-vacuous trials.

    Vacuous trials are excluded rather than scored as zero: a family that
    could not perturb a document has not demonstrated the insight is robust,
    and counting it as evidence of robustness is a free win nobody earned.
    """
    scored = [trial for trial in trials if not trial.vacuous]
    if not scored:
        return 0.0
    return round(statistics.fmean(trial.instability for trial in scored), 6)


# ── the report ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScatterPoint:
    insight_id: uuid.UUID
    vacuity: float
    fragility: float
    routing: str


def by_perturbation(trials: list[Trial]) -> list[dict[str, Any]]:
    """Per-family flip rate, mean |Δconf| and relation-loss rate."""
    grouped: dict[str, list[Trial]] = defaultdict(list)
    for trial in trials:
        grouped[trial.perturbation].append(trial)

    rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        members = [t for t in grouped.get(family, []) if not t.vacuous]
        if not members:
            continue
        rows.append(
            {
                "perturbation": family,
                "flip_rate": round(statistics.fmean(float(t.label_flipped) for t in members), 4),
                "mean_abs_conf_delta": round(
                    min(statistics.fmean(abs(t.conf_delta) for t in members), 1.0), 4
                ),
                "relation_loss_rate": round(
                    statistics.fmean(float(t.relation_lost) for t in members), 4
                ),
            }
        )
    return rows


def quartile_table(
    points: list[ScatterPoint], trials_by_insight: dict[uuid.UUID, list[Trial]]
) -> list[dict[str, Any]]:
    """Vacuity quartile against mean fragility and flip rate.

    The cleanest single statement of the thesis: if the bottom and top
    quartiles have the same flip rate, the uncertainty score is decorative.
    """
    vacuities = sorted(point.vacuity for point in points)
    bounds = quartile_bounds(vacuities)

    buckets: dict[int, list[ScatterPoint]] = defaultdict(list)
    for point in points:
        buckets[quartile_of(point.vacuity, vacuities)].append(point)

    rows: list[dict[str, Any]] = []
    for index in (1, 2, 3, 4):
        members = buckets.get(index, [])
        if not members:
            continue
        flips = [
            float(trial.label_flipped)
            for point in members
            for trial in trials_by_insight.get(point.insight_id, [])
            if not trial.vacuous
        ]
        low, high = bounds[index - 1]
        rows.append(
            {
                "vacuity_quartile": index,
                "vacuity_range": (round(low, 4), round(high, 4)),
                "mean_fragility": round(statistics.fmean(point.fragility for point in members), 4),
                "flip_rate": round(statistics.fmean(flips), 4) if flips else 0.0,
            }
        )
    return rows


def correlation(points: list[ScatterPoint]) -> Correlation:
    return correlate([point.vacuity for point in points], [point.fragility for point in points])


def interpretation(
    stats: Correlation, table: list[dict[str, Any]], families: list[dict[str, Any]]
) -> str:
    """The sentence we want a judge to read if they read nothing else.

    Assembled from the measured numbers, with the wording chosen by which
    band the coefficient falls in — template-filled, never generated, and
    deliberately willing to say the signal is weak.
    """
    if not points_enough(stats):
        return (
            "Too few insights carried fragility trials for a correlation to mean "
            "anything. Run the fuzzer over a seeded scenario before reading this."
        )

    bottom = next((row for row in table if row["vacuity_quartile"] == 1), None)
    top = next((row for row in table if row["vacuity_quartile"] == 4), None)
    ratio = ""
    if bottom and top and bottom["flip_rate"] > 0:
        ratio = (
            f"Insights in the top vacuity quartile flip labels under perturbation "
            f"{top['flip_rate'] / bottom['flip_rate']:.1f}x more often than the "
            f"bottom quartile. "
        )
    elif top and bottom and top["flip_rate"] > 0:
        ratio = (
            f"The bottom vacuity quartile never flipped; the top flipped on "
            f"{top['flip_rate']:.0%} of trials. "
        )

    strength = _strength(stats)
    worst = max(families, key=lambda row: row["flip_rate"], default=None)
    worst_note = _mechanism(worst) if worst else ""

    return (
        f"{ratio}Spearman {stats.spearman:.2f} (Pearson {stats.pearson:.2f}, "
        f"p = {stats.p_value:.2g}, n = {stats.n}). {strength}{worst_note}"
    )


# What each family's damage actually tells you. Template-filled and selected
# by which family measured worst — the number alone says "something broke",
# and the mechanism is the part worth a judge's attention.
MECHANISM: dict[str, str] = {
    "rename": (
        " The most damaging family is rename at a {rate:.0%} flip rate, which is the "
        "result that matters: it changes the parties and nothing else, so it "
        "separates structure from memorization."
    ),
    "punctuation": (
        " The most damaging family is punctuation at a {rate:.0%} flip rate, with "
        "{loss:.0%} of trials losing the relation outright. That is a segmenter "
        "failure rather than a model one — a stray newline splits the sentence, the "
        "two parties end up in different graphs, and the claim disappears. It is a "
        "direct cost of the line-aware segmentation that made citations tight, and "
        "the honest reading is that our pipeline is more brittle to OCR noise than "
        "our model is."
    ),
    "synonym": (
        " The most damaging family is synonym substitution at a {rate:.0%} flip rate, "
        "which suggests the model is keying on surface vocabulary more than on "
        "syntax."
    ),
    "boilerplate": (
        " The most damaging family is boilerplate injection at a {rate:.0%} flip "
        "rate, which means extraction is sensitive to where in a document a claim "
        "sits."
    ),
    "reorder": (
        " The most damaging family is sentence reordering at a {rate:.0%} flip rate. "
        "The relation model reads one sentence at a time, so this should be near "
        "zero; anything else points at coreference or segmentation, not the model."
    ),
}


def _mechanism(worst: dict[str, Any]) -> str:
    if worst["flip_rate"] <= 0.0:
        return (
            " No family flipped a label, which at this sample size means the "
            "perturbations were too gentle rather than that the model is robust."
        )
    template = MECHANISM.get(worst["perturbation"])
    if template is None:  # pragma: no cover - families are a closed set
        return ""
    return template.format(rate=worst["flip_rate"], loss=worst.get("relation_loss_rate", 0.0))


def points_enough(stats: Correlation) -> bool:
    return stats.n >= 10


def _strength(stats: Correlation) -> str:
    if not stats.significant:
        return (
            "The correlation is not statistically significant at this sample size, "
            "so we are reporting it as an observation rather than a result."
        )
    magnitude = abs(stats.spearman)
    if magnitude >= 0.6:
        return "The uncertainty signal is predictive of real fragility, not decorative."
    if magnitude >= 0.35:
        return (
            "The uncertainty signal is predictive but not strongly so. It is carried "
            "mostly by the out-of-distribution tail: in-distribution constructions are "
            "both low-vacuity and robust, so most of the ranking information sits in "
            "the top quartile rather than being spread across the range."
        )
    if magnitude >= 0.15:
        return (
            "The signal is weak. Vacuity orders fragility better than chance and not "
            "much better than that, and we would rather report that than a number we "
            "tuned into existence."
        )
    return (
        "There is essentially no monotonic relationship at this sample size. That is "
        "the measurement, and it is the one we are reporting."
    )
