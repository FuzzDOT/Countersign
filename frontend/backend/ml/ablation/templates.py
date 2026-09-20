"""Ablation interpretations, template-filled and never generated. Brief §8.

The voice layer reads `ablation_runs.interpretation` verbatim, so this file
is load-bearing for the project's no-hallucination guarantee:
`tests/test_no_generation.py` greps the ablation and voice paths for any
upstream LLM call and fails if it finds one. That test is how the claim
stays true at hour 23 when someone is tired.

A template is chosen by three facts and nothing else: the sign of the
confidence delta, its magnitude bucket, and whether the routing bucket
moved. That is a small enough space to write out by hand, and writing it out
by hand is what makes every sentence in the response something a person
decided to say.

**The negative result gets its own wording, deliberately.** When ablation
changes nothing, the honest sentence is "the pretty heat map was lying" —
and having that sentence pre-written is the difference between reporting it
and quietly not mentioning it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Brief §8 / plan §4 Stage 7: |Δconf| over this, or a routing change, makes
# an edge load-bearing. Mirrors `settings.ablation_load_bearing_delta`.
NEGLIGIBLE = 0.02
MODERATE = 0.10
LARGE = 0.25


@dataclass(frozen=True, slots=True)
class Verdict:
    """The facts a template is selected by."""

    delta_confidence: float
    routing_changed: bool
    relation_changed: bool
    n_edges: int
    mode: str

    @property
    def magnitude(self) -> str:
        size = abs(self.delta_confidence)
        if size >= LARGE:
            return "large"
        if size >= MODERATE:
            return "moderate"
        if size >= NEGLIGIBLE:
            return "slight"
        return "negligible"

    @property
    def direction(self) -> str:
        return "down" if self.delta_confidence < 0 else "up"


def _edges(count: int) -> str:
    return "this dependency edge" if count == 1 else f"these {count} dependency edges"


def _masking(mode: str, count: int) -> str:
    if mode == "uniform":
        return f"Flattening attention across {_edges(count)}"
    return f"Removing {_edges(count)}"


def render(verdict: Verdict, *, relation_before: str, relation_after: str) -> str:
    """One sentence, sometimes two. Assembled, never written by a model."""
    lead = _masking(verdict.mode, verdict.n_edges)
    delta = abs(verdict.delta_confidence)

    if verdict.relation_changed:
        return (
            f"{lead} changes the extracted relation from {relation_before} to "
            f"{relation_after}, with confidence moving {verdict.direction} by "
            f"{delta:.2f}. The claim does not survive without it."
        )

    if verdict.routing_changed:
        return (
            f"{lead} moves confidence {verdict.direction} by {delta:.2f}, which is "
            f"enough to change the routing decision. This edge is carrying the "
            f"conclusion, not decorating it."
        )

    if verdict.magnitude == "large":
        return (
            f"{lead} moves confidence {verdict.direction} by {delta:.2f} without "
            f"changing the routing bucket. The evidence is concentrated here, but "
            f"there is enough left over for the decision to stand."
        )

    if verdict.magnitude == "moderate":
        return (
            f"{lead} moves confidence {verdict.direction} by {delta:.2f}. It "
            f"contributes to the conclusion without being load-bearing on its own."
        )

    if verdict.magnitude == "slight":
        return (
            f"{lead} moves confidence {verdict.direction} by {delta:.2f} — a small "
            f"effect. The relation is supported by other parts of the sentence."
        )

    return (
        f"{lead} changes almost nothing: confidence moves by {delta:.3f} and the "
        f"routing is unaffected. High attention here did not mean the model was "
        f"relying on it, which is the failure mode attention maps are known for "
        f"and the reason we run the counterfactual instead of showing the heat map."
    )
