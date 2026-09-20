"""Correlation statistics for the eval endpoints.

scipy is already a dependency and its `spearmanr` gives the exact two-sided
p-value rather than a normal-tail approximation, which matters here: Stage
5's exit criterion is `p < 0.05` and reporting an approximation as if it were
exact is the kind of thing a judge who has fit a calibration curve before
will ask about.

`data/synth/fixtures.py` carries its own stdlib-only copies of these. That is
deliberate duplication, not an oversight — the fixture generator is a data
authoring tool that must run in a bare checkout, and making it import the ML
stack to compute a rank correlation would be the wrong trade.
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this the tail computation has underflowed and the honest report is an
# upper bound rather than a zero. "p = 0" is not a reportable result.
P_VALUE_FLOOR = 1e-12


@dataclass(frozen=True, slots=True)
class Correlation:
    spearman: float
    pearson: float
    p_value: float
    n: int

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05

    def as_dict(self) -> dict[str, float]:
        return {
            "spearman": round(self.spearman, 4),
            "pearson": round(self.pearson, 4),
            "p_value": self.p_value,
        }


def correlate(xs: list[float], ys: list[float]) -> Correlation:
    """Spearman as the headline, Pearson alongside, exact two-sided p.

    Spearman because the relationship need only be monotonic — claiming a
    linearity we did not test would be the same sin as shipping an
    unvalidated confidence score. Pearson is reported next to it so the
    difference between the two is visible rather than hidden by the choice.
    """
    n = len(xs)
    if n < 3:
        return Correlation(spearman=0.0, pearson=0.0, p_value=1.0, n=n)

    from scipy import stats

    # A constant input makes both coefficients undefined; scipy returns nan
    # and a warning. Zero with p=1 is the honest reading of "one of these
    # variables does not vary".
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return Correlation(spearman=0.0, pearson=0.0, p_value=1.0, n=n)

    rho = stats.spearmanr(xs, ys)
    r = stats.pearsonr(xs, ys)

    return Correlation(
        spearman=float(rho.statistic),
        pearson=float(r.statistic),
        p_value=max(float(rho.pvalue), P_VALUE_FLOOR),
        n=n,
    )


def quartile_bounds(values: list[float]) -> list[tuple[float, float]]:
    """Four ranges over the sorted values, as `[lo, hi]` pairs.

    Computed from the data rather than from fixed cuts, because vacuity is
    not uniformly distributed — on this corpus most of it sits under 0.15 and
    fixed quarter-width bands would leave three of the four buckets empty.
    """
    if not values:
        return [(0.0, 0.0)] * 4
    ordered = sorted(values)
    n = len(ordered)
    cuts = [ordered[min(int(q * n), n - 1)] for q in (0.0, 0.25, 0.5, 0.75)]
    return [
        (cuts[0], cuts[1]),
        (cuts[1], cuts[2]),
        (cuts[2], cuts[3]),
        (cuts[3], ordered[-1]),
    ]


def quartile_of(value: float, values_sorted: list[float]) -> int:
    """1-4, by position in the sorted distribution.

    Ties go to the lower quartile, so the buckets partition the data even
    when a value sits exactly on a cut — which happens constantly here,
    because many insights share a vacuity to four decimal places.
    """
    if not values_sorted:
        return 1
    import bisect

    position = bisect.bisect_left(values_sorted, value)
    quartile = int(4 * position / len(values_sorted)) + 1
    return min(max(quartile, 1), 4)
