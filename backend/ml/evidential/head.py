"""The evidential output layer and its loss. Brief §15.

    loss = expected cross-entropy under the Dirichlet
         + λ · KL( Dir(α̃) ‖ Dir(1) )        on the wrong classes only

The KL term is what makes vacuity mean something. Without it the model can
minimize the first term by piling evidence onto every class, and vacuity
collapses to near zero everywhere — which is exactly the failure plan §0 says
has no fix at hour 13, because a constant has nothing to correlate against.

λ is annealed from 0 to 1 over the first `annealing_epochs`. Applying it from
step one suppresses evidence before the model has learned to produce any, and
training converges to "I know nothing about everything".
"""

from __future__ import annotations

import torch
from torch import Tensor, nn

from ml.evidential.uncertainty import alpha


class EvidentialHead(nn.Module):
    """A linear layer whose outputs are read as evidence rather than logits.

    Kept as its own module because both relation models feed it — the GAT's
    graph readout and the rule extractor's hand-crafted feature vector — and
    the ablation contract (plan §1.3) depends on the two producing
    commensurable uncertainty.
    """

    def __init__(self, in_features: int, n_classes: int, *, hidden: int = 0) -> None:
        super().__init__()
        self.n_classes = n_classes
        if hidden:
            self.net: nn.Module = nn.Sequential(
                nn.Linear(in_features, hidden),
                nn.ELU(),
                nn.Linear(hidden, n_classes),
            )
        else:
            self.net = nn.Linear(in_features, n_classes)

    def forward(self, features: Tensor) -> Tensor:
        return self.net(features)


def kl_to_uniform(a: Tensor) -> Tensor:
    """KL( Dir(α) ‖ Dir(1) ), per row."""
    ones = torch.ones_like(a)
    strength = a.sum(dim=-1, keepdim=True)

    term = (
        torch.lgamma(strength).squeeze(-1)
        - torch.lgamma(a).sum(dim=-1)
        - torch.lgamma(ones.sum(dim=-1))
        + torch.lgamma(ones).sum(dim=-1)
    )
    digamma = (a - ones) * (torch.digamma(a) - torch.digamma(strength))
    return term + digamma.sum(dim=-1)


def evidential_loss(
    logits: Tensor,
    targets: Tensor,
    *,
    epoch: int = 0,
    annealing_epochs: int = 10,
    max_lambda: float = 1.0,
    class_weights: Tensor | None = None,
) -> Tensor:
    """Mean loss over the batch.

    `max_lambda` scales the regularizer's final strength. Above 1 the model
    pays more for evidence it cannot justify, which is the lever on how
    readily vacuity rises — see `scripts/vacuity_report.py`, which is the
    measurement that decides whether the setting is earning its keep.

    `class_weights` exists because candidate generation produces far more
    NO_RELATION pairs than anything else — every co-occurring entity pair in
    every sentence is a candidate. Unweighted, the model learns to say
    NO_RELATION and is right most of the time, which is a useless model with a
    good-looking accuracy.
    """
    a = alpha(logits)
    strength = a.sum(dim=-1, keepdim=True)
    one_hot = torch.zeros_like(a).scatter_(1, targets.unsqueeze(1), 1.0)

    # Expected cross-entropy under the Dirichlet: E_p~Dir(α)[ -Σ y log p ].
    expected_ce = (one_hot * (torch.digamma(strength) - torch.digamma(a))).sum(dim=-1)

    # Evidence on the *correct* class is not penalized; the KL is computed
    # over α with the true class's evidence removed. That is the whole trick:
    # it drives wrong-class evidence to zero without capping right-class
    # confidence.
    wrong = one_hot + (1.0 - one_hot) * a
    regularizer = kl_to_uniform(wrong)

    lam = max_lambda * min(1.0, max(epoch, 0) / max(annealing_epochs, 1))
    per_example = expected_ce + lam * regularizer

    if class_weights is not None:
        weights = class_weights[targets]
        return (per_example * weights).sum() / weights.sum().clamp_min(1e-8)
    return per_example.mean()
