"""The uncertainty gate. Brief §9, plan §4 Stage 4.

This is the cascade's whole argument in one function: **the classical model
knows what it does not know, so only the insights it is uncertain about are
worth an LLM call.** Everything else is answered classically, for free, in
milliseconds.

    escalate = vacuity >= threshold
            or (classical says escalate_now and confidence is not high)
            or dissonance is high

Three conditions rather than one, because they catch different failures:

  * **vacuity** is the headline: the model has little evidence either way,
    which is exactly when a second opinion is worth paying for.
  * a **classical escalate_now** on a shaky reading should be checked before
    it reaches an analyst's queue — it is the expensive kind of wrong.
  * **dissonance** means plenty of evidence pointing in conflicting
    directions. Rare in this corpus, but it is a different failure from
    vacuity and routing them identically would waste the distinction.

The threshold is **tuned, not asserted**. The brief's 0.45 is a guess, and
the definition of done requires an escalation rate in 8–20%.
`scripts/tune_gate.py` sweeps it over the demo scenario, picks the value
landing nearest the target, and verifies on `clean_baseline` that the system
does not then cry wolf on a corpus with no fraud in it.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import Settings, get_settings
from db.models import RoutingBucket

# Reported as `routing.summary.gate.policy` so the number on the eval page
# has a name attached to the rule that produced it.
POLICY = "vacuity_gate_v2"

# A classical escalate_now below this confidence is checked rather than
# trusted: acting on a shaky escalation is the expensive kind of wrong.
ESCALATION_REVIEW_CONFIDENCE = 0.75

# Conflicting evidence, as opposed to absent evidence.
DISSONANCE_THRESHOLD = 0.35


@dataclass(frozen=True, slots=True)
class GateDecision:
    escalate: bool
    reason: str

    @property
    def handled_classically(self) -> bool:
        return not self.escalate


def should_escalate(
    *,
    vacuity: float,
    dissonance: float,
    confidence: float,
    classical: RoutingBucket,
    settings: Settings | None = None,
) -> GateDecision:
    settings = settings or get_settings()

    if vacuity >= settings.vacuity_gate_threshold:
        return GateDecision(True, "vacuity")
    if dissonance >= DISSONANCE_THRESHOLD:
        return GateDecision(True, "dissonance")
    if classical is RoutingBucket.escalate_now and confidence < ESCALATION_REVIEW_CONFIDENCE:
        return GateDecision(True, "uncertain_escalation")
    return GateDecision(False, "confident_classical")


def escalation_rate(decisions: list[GateDecision]) -> float:
    if not decisions:
        return 0.0
    return sum(1 for decision in decisions if decision.escalate) / len(decisions)
