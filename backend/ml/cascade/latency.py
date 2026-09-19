"""Measuring the classical path, rather than guessing at it. Brief §9.

`routing.summary.latency` promises p50 and p95 for both arms. The Nemotron
side is easy — `nemotron_runs.latency_ms` is a real per-call measurement.
The classical side is not stored anywhere: insights are written in a batch
and timing the batch gives a mean, not a percentile, and reporting a mean
under a field called `p95` would be a small lie in a response whose whole
purpose is to be checkable.

So it is measured on demand: re-infer a sample of the organization's own
insights, time each one, and take real percentiles. About 300 ms for twelve
samples, cached for a minute, on an eval page that is not on the demo's hot
path. The honest cost of a number that means what it says.
"""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger
from db.models import Insight

log = get_logger(__name__)

SAMPLE_SIZE = 12
CACHE_SECONDS = 60.0

# What we report when there is nothing to measure. Zero rather than a
# plausible-looking default: an empty organization has no latency, and
# inventing one would be the exact failure this module exists to avoid.
EMPTY = (0, 0)


@dataclass(frozen=True, slots=True)
class Percentiles:
    p50: int
    p95: int
    samples: int


_CACHE: dict[uuid.UUID, tuple[float, Percentiles]] = {}


def classical_percentiles(
    db: Session, org_id: uuid.UUID, settings: Settings | None = None
) -> Percentiles:
    settings = settings or get_settings()

    cached = _CACHE.get(org_id)
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    sentences = list(
        db.execute(
            select(Insight.sentence_text)
            .where(Insight.org_id == org_id)
            .order_by(Insight.created_at.desc())
            .limit(SAMPLE_SIZE)
        ).scalars()
    )
    measured = Percentiles(*EMPTY, samples=0) if not sentences else _measure(sentences, settings)

    _CACHE[org_id] = (time.monotonic(), measured)
    return measured


def _measure(sentences: list[str], settings: Settings) -> Percentiles:
    from ml.relations.infer import get_extractor
    from ml.tagger.infer import get_tagger
    from ml.text.parse import parse

    tagger = get_tagger(settings)
    extractor = get_extractor(settings)

    timings: list[float] = []
    for sentence in sentences:
        started = time.perf_counter()
        # `use_cache=False`: a cached parse would measure a dictionary lookup
        # rather than the classical path, and the cold path is the one a
        # judge's upload actually takes.
        doc = parse(sentence, use_cache=False)
        tagging = tagger.tag(doc)
        extractor.extract(doc, tagging)
        timings.append((time.perf_counter() - started) * 1000.0)

    ordered = sorted(timings)
    return Percentiles(
        p50=int(statistics.median(ordered)),
        p95=int(ordered[min(int(0.95 * (len(ordered) - 1)), len(ordered) - 1)]),
        samples=len(ordered),
    )


def percentiles_of(values: list[int]) -> Percentiles:
    """p50/p95 over values already measured — the Nemotron side."""
    if not values:
        return Percentiles(*EMPTY, samples=0)
    ordered = sorted(values)
    return Percentiles(
        p50=int(statistics.median(ordered)),
        p95=int(ordered[min(int(0.95 * (len(ordered) - 1)), len(ordered) - 1)]),
        samples=len(ordered),
    )


def reset_cache() -> None:
    _CACHE.clear()
