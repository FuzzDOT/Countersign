"""Calibration measurement and fitting. Brief §10, plan §4 Stage 9.

**What is being calibrated, precisely.** `insights.confidence` is the
evidential head's `max(α)/S` over the **six relation classes**
(`ml/relations/interface.RELATIONS`), and `insights.evidence_logits` is the
matching `[N_RELATIONS]` evidence-logit vector. So calibration here asks
"when the extractor says it is 0.9 sure the relation is `WIRED_FUNDS_TO`,
is it right 90% of the time?" — a relation-classification question.

That distinction is worth stating because the obvious misreading is to
calibrate against `routing`, which has only three buckets and is a
*downstream* decision made by thresholds and, for escalated cases, by
Nemotron. Feeding 6-wide relation logits to a 3-bucket objective is not a
temperature fit, it is a category error, and it would produce a number that
looks plausible and means nothing. `ml/evidential/temperature.apply`
divides these same relation logits before the Dirichlet reads them, so the
fit and the application have to be over the same space or the temperature
written to the snapshot is meaningless.

Ground truth comes from the generator manifest's gold relations, the same
source `ml/cascade/evalset.py` uses. Generator-authored, which the writeup
states as a limitation.

**The caveat, stated here as well as in the plan, because this is where
someone reads it:** temperature is fit on the hard-negative set, which
biases toward the tail. Defensible for the demo claim, not a production
calibration strategy. `interpretation()` puts that sentence in the API
response rather than leaving it to be remembered under stage lights.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import torch
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger
from data.synth.generate import generate
from db.models import Document, Insight, Resolver, RoutingBucket
from ml.cascade.evalset import gold_entity_ids
from ml.evidential import temperature as temperature_mod
from ml.evidential.uncertainty import trust_of
from ml.relations.interface import N_RELATIONS, RELATION_INDEX

log = get_logger(__name__)

# Plan §4 Stage 9: "ECE (10 equal-width bins)". Equal-width, not equal-mass —
# a judge reading the reliability diagram expects confidence on the x axis in
# even steps, and equal-mass bins would move the bin edges between the before
# and after series, which is exactly the comparison the diagram exists for.
N_BINS = 10

# "High-confidence" for the disagreement set. Both sides must be confident for
# a disagreement to be informative: a hesitant classical prediction losing to
# Nemotron is the cascade working as designed, not miscalibration.
HIGH_CONFIDENCE = 0.70

# One scalar, convex-ish objective: this converges in single digits. 50 is
# generous and still finishes in milliseconds, which keeps the <4s exit
# criterion about database round-trips rather than the optimizer.
MAX_ITERATIONS = 50


@dataclass(frozen=True, slots=True)
class Bin:
    bin_lo: float
    bin_hi: float
    avg_conf: float
    accuracy: float
    count: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "bin_lo": round(self.bin_lo, 4),
            "bin_hi": round(self.bin_hi, 4),
            "avg_conf": round(self.avg_conf, 4),
            "accuracy": round(self.accuracy, 4),
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class Metrics:
    temperature: float
    ece: float
    mce: float
    brier: float
    bins: tuple[Bin, ...]
    n_cases: int

    def as_dict(self) -> dict[str, object]:
        return {
            "temperature": self.temperature,
            "ece": round(self.ece, 4),
            "mce": round(self.mce, 4),
            "brier": round(self.brier, 4),
            "bins": [b.as_dict() for b in self.bins],
        }


@dataclass(frozen=True, slots=True)
class Case:
    """One scored insight with a gold relation label.

    `logits` is `None` on rows the rules model wrote, or any row predating
    Stage 3's logging. Those cases still count toward ECE — they have a real
    confidence and a real answer — but cannot be *rescaled*, so
    `fit_temperature` and `rescored` skip them. Keeping them in the
    measurement and out of the fit is deliberate: dropping them from the
    baseline too would flatter the before/after comparison.
    """

    insight_id: uuid.UUID
    confidence: float
    predicted_relation: str
    gold_relation: str
    logits: list[float] | None
    resolved_by: Resolver
    routing: RoutingBucket
    classical_routing: RoutingBucket | None

    @property
    def correct(self) -> bool:
        return self.predicted_relation == self.gold_relation


# ── measurement ──────────────────────────────────────────────────────────────


def bin_edges(n_bins: int = N_BINS) -> list[tuple[float, float]]:
    width = 1.0 / n_bins
    return [(index * width, (index + 1) * width) for index in range(n_bins)]


def compute_bins(
    confidences: list[float], correct: list[bool], n_bins: int = N_BINS
) -> tuple[Bin, ...]:
    """Ten bins, every one present even when empty.

    An empty bin is reported with `count: 0` rather than omitted: the exit
    criterion is that bin counts sum to the case total, and a diagram whose
    x axis silently loses a step is harder to read than one with a visible
    gap. Confidence of exactly 1.0 lands in the last bin rather than falling
    off the end — the one place the half-open convention is broken, and
    breaking it is what makes the counts sum correctly.
    """
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for confidence, is_correct in zip(confidences, correct, strict=True):
        index = min(int(confidence * n_bins), n_bins - 1)
        buckets[index].append((confidence, is_correct))

    out: list[Bin] = []
    for (lo, hi), bucket in zip(bin_edges(n_bins), buckets, strict=True):
        if not bucket:
            out.append(Bin(bin_lo=lo, bin_hi=hi, avg_conf=0.0, accuracy=0.0, count=0))
            continue
        confs = [c for c, _ in bucket]
        hits = [1.0 if ok else 0.0 for _, ok in bucket]
        out.append(
            Bin(
                bin_lo=lo,
                bin_hi=hi,
                avg_conf=sum(confs) / len(confs),
                accuracy=sum(hits) / len(hits),
                count=len(bucket),
            )
        )
    return tuple(out)


def expected_calibration_error(bins: tuple[Bin, ...], total: int) -> float:
    """Count-weighted mean gap between confidence and accuracy."""
    if total == 0:
        return 0.0
    return sum(b.count * abs(b.avg_conf - b.accuracy) for b in bins) / total


def maximum_calibration_error(bins: tuple[Bin, ...]) -> float:
    """The worst single populated bin.

    Empty bins are excluded explicitly rather than incidentally. ECE weights
    by count, so empties contribute nothing there automatically; MCE takes a
    max, where an empty bin's zero gap could only ever understate. Excluding
    them in both keeps a reader from having to work out whether the two
    functions agree.
    """
    populated = [b for b in bins if b.count > 0]
    if not populated:
        return 0.0
    return max(abs(b.avg_conf - b.accuracy) for b in populated)


def brier_score(confidences: list[float], correct: list[bool]) -> float:
    """Mean squared error of the confidence against the outcome.

    One-vs-rest over the *predicted* class, not the full multiclass Brier:
    `confidence` is already the head's posterior mean for the class it chose,
    which is the number the reliability diagram plots and the number a user
    sees. Computing a different quantity than the displayed one would make
    the two disagree for no benefit.
    """
    if not confidences:
        return 0.0
    squared = [
        (confidence - (1.0 if is_correct else 0.0)) ** 2
        for confidence, is_correct in zip(confidences, correct, strict=True)
    ]
    return sum(squared) / len(squared)


def metrics(cases: list[Case], temperature: float, n_bins: int = N_BINS) -> Metrics:
    confidences = [case.confidence for case in cases]
    correct = [case.correct for case in cases]
    bins = compute_bins(confidences, correct, n_bins)
    return Metrics(
        temperature=temperature,
        ece=expected_calibration_error(bins, len(cases)),
        mce=maximum_calibration_error(bins),
        brier=brier_score(confidences, correct),
        bins=bins,
        n_cases=len(cases),
    )


# ── the case set ─────────────────────────────────────────────────────────────


def load_cases(
    db: Session, org_id: uuid.UUID, settings: Settings | None = None
) -> list[Case]:
    """Every extracted insight paired with its gold relation.

    Keyed on `(document_id, subject_id, object_id)` — deliberately *without*
    the relation, unlike `ml/cascade/evalset.build`, which keys on the
    relation too. That difference is the whole point: evalset only wants
    pairs the extractor got right, because a missed relation is a recall
    failure it reports elsewhere. Calibration needs the **wrong** ones most
    of all — a confidently-misclassified relation is exactly the
    overconfidence ECE is supposed to catch, and keying on the relation
    would silently drop every such case and report a flatteringly low error.
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
        return []

    by_pair: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], Insight] = {}
    for insight in db.execute(select(Insight).where(Insight.org_id == org_id)).scalars():
        by_pair[(insight.document_id, insight.subject_id, insight.object_id)] = insight

    cases: list[Case] = []
    seen: set[uuid.UUID] = set()
    for scenario in scenarios:
        manifest = generate(scenario, seed=settings.pipeline_seed, org_id=org_id)
        for document in manifest.documents:
            for relation in document.relations:
                subject_id, object_id = gold_entity_ids(org_id, relation)
                insight = by_pair.get((document.document_id, subject_id, object_id))
                if insight is None or insight.id in seen:
                    continue
                seen.add(insight.id)
                cases.append(
                    Case(
                        insight_id=insight.id,
                        confidence=float(insight.confidence),
                        predicted_relation=insight.relation,
                        gold_relation=relation.relation,
                        logits=insight.evidence_logits,
                        resolved_by=insight.resolved_by,
                        routing=insight.routing,
                        classical_routing=insight.classical_routing,
                    )
                )
    return cases


def hard_negatives(cases: list[Case]) -> list[Case]:
    """Cases where a confident Nemotron overrode a confident classical model.

    Plan §4 Stage 9's definition, applied to columns Stage 3 already stores
    rather than to a `hard_negatives` table — there isn't one, and there
    doesn't need to be. Three conditions, all necessary:

    * `resolved_by is nemotron` — the cascade escalated *and* got an answer.
      A degraded row has no Nemotron opinion to disagree with.
    * `classical_routing` present and different from `routing` — a real
      disagreement, not an agreement that happened to be escalated.
    * `confidence >= HIGH_CONFIDENCE` — the extractor was confident in the
      claim underlying the prediction that lost.

    Returns `[]` when nothing qualifies, and the caller treats that as "no
    refit possible" — the honest outcome on a corpus with no disagreement,
    where temperature correctly stays at 1.0 rather than being fit to noise.
    """
    return [
        case
        for case in cases
        if case.resolved_by is Resolver.nemotron
        and case.classical_routing is not None
        and case.classical_routing != case.routing
        and case.confidence >= HIGH_CONFIDENCE
    ]


# ── fitting ──────────────────────────────────────────────────────────────────


def fit_temperature(cases: list[Case]) -> float:
    """One-parameter L-BFGS over the logged relation logits. Returns clamped T.

    Minimizes NLL of the gold relation under `logits / T` — the standard
    temperature-scaling objective (Guo et al. 2017), over the same
    6-dimensional relation space `temperature_mod.apply` divides at
    inference time.

    Parameterized as `T = exp(log_t)` so the optimizer cannot reach `T <= 0`,
    which would flip the sign of every logit and is not a temperature. The
    result is still clamped: the reparameterization prevents nonsense, the
    clamp prevents a technically-valid extreme from flattening every
    confidence in the demo to 1/K.
    """
    usable = [case for case in cases if case.logits and case.gold_relation in RELATION_INDEX]
    if not usable:
        return temperature_mod.IDENTITY

    widths = {len(case.logits) for case in usable if case.logits}
    if widths != {N_RELATIONS}:
        # Vectors from different output spaces are not commensurable;
        # stacking them would be nonsense. Declining the fit is correct, and
        # loud, rather than silently averaging across two models.
        log.warning(
            "calibration_logit_width_mismatch",
            widths=sorted(widths),
            expected=N_RELATIONS,
        )
        return temperature_mod.IDENTITY

    logits = torch.tensor([case.logits for case in usable], dtype=torch.float32)
    targets = torch.tensor(
        [RELATION_INDEX[case.gold_relation] for case in usable], dtype=torch.long
    )

    log_t = torch.zeros(1, requires_grad=True)  # T = exp(0) = 1.0
    optimizer = torch.optim.LBFGS([log_t], max_iter=MAX_ITERATIONS)
    objective = torch.nn.CrossEntropyLoss()

    def closure() -> torch.Tensor:
        optimizer.zero_grad()
        loss = objective(logits / torch.exp(log_t), targets)
        loss.backward()
        return loss

    optimizer.step(closure)  # type: ignore[arg-type]

    fitted = float(torch.exp(log_t.detach()).item())
    if not _finite(fitted):
        log.warning("calibration_fit_diverged", fitted=fitted)
        return temperature_mod.IDENTITY

    clamped = temperature_mod.clamp(fitted)
    log.info(
        "temperature_fitted",
        fitted=round(fitted, 4),
        clamped=round(clamped, 4),
        n_fit_cases=len(usable),
    )
    return clamped


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def rescored(cases: list[Case], temperature: float) -> list[Case]:
    """The same cases with confidence recomputed under a new temperature.

    Confidence is recomputed through the **evidential head**, not a softmax:
    `confidence` is `max(α)/S` over `α = softplus(logits) + 1`
    (`ml/evidential/uncertainty.py`). Using a softmax here would produce a
    different, larger number than the pipeline itself would report for the
    same temperature, and the "after" column would not match what a re-ingest
    actually gives you — the single most misleading thing this endpoint could
    do.

    `correct` is deliberately not recomputed, and cannot change: temperature
    scaling is strictly monotonic, so it cannot move the argmax and cannot
    change whether the prediction was right. Calibration improves the
    *confidence*, not the decision, and the response must not be able to
    imply otherwise.
    """
    out: list[Case] = []
    for case in cases:
        if not case.logits:
            out.append(case)
            continue
        scaled = temperature_mod.rescale(case.logits, temperature)
        trust = trust_of(scaled)
        out.append(
            Case(
                insight_id=case.insight_id,
                confidence=trust.confidence,
                predicted_relation=case.predicted_relation,
                gold_relation=case.gold_relation,
                logits=case.logits,
                resolved_by=case.resolved_by,
                routing=case.routing,
                classical_routing=case.classical_routing,
            )
        )
    return out


def interpretation(before: Metrics, after: Metrics, n_hard_negatives: int) -> str:
    """The rehearsed caveat, in the response, template-filled.

    Plan §4 Stage 9 prepares this answer for a judge who asks. Putting it in
    the payload means it does not depend on anyone recalling it at hour 23.
    """
    if n_hard_negatives == 0:
        return (
            "No hard negatives: no case had a confident Nemotron override of a "
            "confident classical prediction, so there was nothing to fit and the "
            f"temperature is unchanged at {after.temperature:.1f}. The baseline ECE "
            f"of {before.ece:.4f} over {before.n_cases} labeled cases is still real."
        )
    direction = "reduces" if after.ece < before.ece else "does not reduce"
    return (
        f"Temperature {after.temperature:.3f}, fit by L-BFGS on {n_hard_negatives} hard "
        f"negatives — cases where a confident Nemotron routing overrode a confident "
        f"classical one. It {direction} ECE from {before.ece:.4f} to {after.ece:.4f} "
        f"over {before.n_cases} labeled relation classifications. Fitting on the "
        "disagreement set biases toward the tail, which is where miscalibration lives "
        "but is not a production calibration strategy: for production you would fit on "
        "a held-out sample of the full distribution, and the improvement would be "
        "smaller and more honest."
    )
