"""Turning evidence into the three numbers the UI shows. Brief §15, §4.2.

A Dirichlet over the relation classes, parameterized by non-negative evidence:

    e_k = softplus(logit_k)      evidence for class k
    α_k = e_k + 1                Dirichlet parameter
    S   = Σ α_k                  Dirichlet strength
    b_k = e_k / S                belief mass in class k
    u   = K / S                  vacuity — belief mass assigned to "I do not know"

The point of the whole apparatus is that `u` and a low `confidence` mean
different things, and the product depends on the difference:

  * **vacuity** is high when there is little evidence for anything. A held-out
    company name in a construction the model has never seen produces it. This
    is the number the cascade gate reads, and the one Stage 5 correlates
    against adversarial fragility.
  * **dissonance** is high when there is a lot of evidence, split between
    classes that contradict each other. "This is clearly either INVOICED or
    WIRED_FUNDS_TO and I cannot tell which" is a different failure from "I
    have never seen anything like this", and routing them the same way would
    waste the distinction.
  * **confidence** is `max(α)/S`, which is the posterior mean of the winning
    class — bounded below by `1/K` and pulled toward it as vacuity rises.

Dissonance follows the standard subjective-logic conflict measure (Jøsang):
each belief is weighted by how *balanced* it is against the others, so two
beliefs of 0.45 are maximally dissonant while 0.9 against 0.05 is barely.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor
from torch.nn import functional as F

# Guards a division by zero when every belief is zero (total vacuity).
EPSILON = 1e-10


@dataclass(frozen=True, slots=True)
class Trust:
    """The trust triple, as the API returns it."""

    confidence: float
    vacuity: float
    dissonance: float

    def as_dict(self) -> dict[str, float]:
        return {
            "confidence": self.confidence,
            "vacuity": self.vacuity,
            "dissonance": self.dissonance,
        }


def evidence(logits: Tensor) -> Tensor:
    """Non-negative evidence. `softplus` rather than `relu` or `exp`.

    `relu` kills the gradient for every class the model currently rejects,
    which is most of them, and training stalls. `exp` is unbounded and makes
    a single confident example dominate the strength. `softplus` is the
    standard choice for exactly these two reasons.
    """
    return F.softplus(logits)


def alpha(logits: Tensor) -> Tensor:
    return evidence(logits) + 1.0


def dissonance(belief: Tensor) -> Tensor:
    """Jøsang's conflict measure over a [..., K] belief vector.

        diss = Σ_i b_i · ( Σ_{j≠i} b_j · Bal(b_i, b_j) ) / ( Σ_{j≠i} b_j )
        Bal(b_i, b_j) = 1 − |b_i − b_j| / (b_i + b_j),  0 when either is 0

    Vectorized rather than looped: the fuzzer calls this 1,070 times per run
    and a Python double loop over K=6 would be the slowest line in Stage 5.
    """
    left = belief.unsqueeze(-1)  # [..., K, 1]
    right = belief.unsqueeze(-2)  # [..., 1, K]
    total = left + right
    balance = 1.0 - (left - right).abs() / total.clamp_min(EPSILON)
    # Bal is only defined for pairs where both beliefs are positive, and the
    # diagonal is excluded by definition.
    balance = torch.where(total > EPSILON, balance, torch.zeros_like(balance))
    eye = torch.eye(belief.size(-1), device=belief.device, dtype=belief.dtype)
    balance = balance * (1.0 - eye)

    weighted = (right * balance).sum(dim=-1)
    others = belief.sum(dim=-1, keepdim=True) - belief
    return (belief * weighted / others.clamp_min(EPSILON)).sum(dim=-1)


def trust_from_logits(logits: Tensor) -> tuple[Tensor, Tensor, Tensor]:
    """`(confidence, vacuity, dissonance)` for a [..., K] batch.

    All three are in [0, 1] by construction, which is what lets the database
    carry CHECK constraints on them rather than hoping.
    """
    e = evidence(logits)
    n_classes = logits.size(-1)
    strength = e.sum(dim=-1) + n_classes

    a = e + 1.0
    confidence = a.max(dim=-1).values / strength
    vacuity = n_classes / strength
    belief = e / strength.unsqueeze(-1)

    return (
        confidence.clamp(0.0, 1.0),
        vacuity.clamp(0.0, 1.0),
        dissonance(belief).clamp(0.0, 1.0),
    )


def trust_of(logits: Tensor) -> Trust:
    """Single-example convenience, returning plain floats for the ORM."""
    confidence, vacuity, diss = trust_from_logits(logits.reshape(1, -1))
    return Trust(
        confidence=float(confidence[0]),
        vacuity=float(vacuity[0]),
        dissonance=float(diss[0]),
    )


def probabilities(logits: Tensor) -> Tensor:
    """Expected class probabilities under the Dirichlet, α/S.

    Not a softmax: the softmax of the logits would ignore the evidence
    framing entirely and report a confident distribution for an example the
    model has no evidence about.
    """
    a = alpha(logits)
    return a / a.sum(dim=-1, keepdim=True)
