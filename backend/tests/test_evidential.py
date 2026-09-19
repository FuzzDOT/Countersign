"""The Dirichlet head and its three numbers. Brief §15, §4.2.

The whole project rests on vacuity meaning something, so these tests pin the
*semantics* rather than the arithmetic: no evidence must produce total
vacuity, split evidence must produce dissonance and not vacuity, and the
regularizer must actually push wrong-class evidence down.
"""

from __future__ import annotations

import pytest
import torch

from ml.evidential.head import EvidentialHead, evidential_loss, kl_to_uniform
from ml.evidential.uncertainty import (
    alpha,
    dissonance,
    evidence,
    probabilities,
    trust_from_logits,
    trust_of,
)

K = 6


def logits(*values: float) -> torch.Tensor:
    return torch.tensor([list(values)], dtype=torch.float32)


# ── the semantics ────────────────────────────────────────────────────────────


def test_no_evidence_is_total_vacuity_and_floor_confidence() -> None:
    """The state the model should be in on a construction it has never seen."""
    trust = trust_of(torch.full((K,), -30.0))
    assert trust.vacuity == pytest.approx(1.0, abs=1e-3)
    assert trust.confidence == pytest.approx(1 / K, abs=1e-3)
    assert trust.dissonance == pytest.approx(0.0, abs=1e-3)


def test_evidence_for_one_class_lowers_vacuity_and_raises_confidence() -> None:
    weak = trust_of(logits(-30, 1.0, -30, -30, -30, -30)[0])
    strong = trust_of(logits(-30, 8.0, -30, -30, -30, -30)[0])
    assert strong.vacuity < weak.vacuity
    assert strong.confidence > weak.confidence
    assert strong.dissonance == pytest.approx(0.0, abs=1e-3)


def test_split_evidence_is_dissonant_not_vacuous() -> None:
    """ "I have lots of evidence and it disagrees" is a different failure from
    "I have never seen anything like this", and routing them the same way
    would waste the distinction."""
    conflicted = trust_of(logits(-30, 6.0, 6.0, -30, -30, -30)[0])
    ignorant = trust_of(logits(-30, -30, -30, -30, -30, -30)[0])

    assert conflicted.dissonance > 0.5
    assert conflicted.vacuity < 0.5
    assert ignorant.dissonance < 0.01
    assert ignorant.vacuity > 0.9


def test_three_way_split_is_more_dissonant_than_a_lopsided_one() -> None:
    balanced = trust_of(logits(-30, 5.0, 5.0, 5.0, -30, -30)[0])
    lopsided = trust_of(logits(-30, 9.0, 0.5, 0.5, -30, -30)[0])
    assert balanced.dissonance > lopsided.dissonance


def test_every_score_is_in_the_unit_interval() -> None:
    """The database carries CHECK constraints on all three."""
    torch.manual_seed(0)
    batch = torch.randn(256, K) * 12
    confidence, vacuity, diss = trust_from_logits(batch)
    for tensor in (confidence, vacuity, diss):
        assert float(tensor.min()) >= 0.0
        assert float(tensor.max()) <= 1.0


def test_evidence_is_non_negative_and_smooth() -> None:
    """softplus, not relu: relu kills the gradient for every class the model
    currently rejects, which is most of them, and training stalls."""
    values = torch.linspace(-20, 20, 64)
    e = evidence(values)
    assert float(e.min()) >= 0.0
    assert torch.all(e[1:] > e[:-1])


def test_expected_probabilities_sum_to_one() -> None:
    assert float(probabilities(logits(1, 2, 3, 4, 5, 6)).sum()) == pytest.approx(1.0)


def test_alpha_is_never_below_one() -> None:
    assert float(alpha(torch.full((K,), -50.0)).min()) >= 1.0


def test_dissonance_of_a_single_belief_is_zero() -> None:
    belief = torch.tensor([[0.9, 0.0, 0.0, 0.0, 0.0, 0.0]])
    assert float(dissonance(belief)[0]) == pytest.approx(0.0, abs=1e-6)


# ── the loss ─────────────────────────────────────────────────────────────────


def test_kl_of_a_uniform_dirichlet_is_zero() -> None:
    assert float(kl_to_uniform(torch.ones(1, K))[0]) == pytest.approx(0.0, abs=1e-5)


def test_kl_grows_with_wrong_class_evidence() -> None:
    mild = kl_to_uniform(torch.tensor([[1.0, 2.0, 1.0, 1.0, 1.0, 1.0]]))
    severe = kl_to_uniform(torch.tensor([[1.0, 9.0, 1.0, 1.0, 1.0, 1.0]]))
    assert float(severe[0]) > float(mild[0])


def test_annealing_increases_the_penalty_on_wrong_evidence() -> None:
    """Applying the regularizer from step one suppresses evidence before the
    model has learned to produce any, and training converges to "I know
    nothing about everything"."""
    wrong = logits(-30, 2.0, 7.0, -30, -30, -30)
    target = torch.tensor([1])
    early = float(evidential_loss(wrong, target, epoch=0, annealing_epochs=10))
    late = float(evidential_loss(wrong, target, epoch=10, annealing_epochs=10))
    assert late > early


def test_correct_confident_evidence_is_cheaper_than_wrong_evidence() -> None:
    target = torch.tensor([1])
    right = float(evidential_loss(logits(-30, 8, -30, -30, -30, -30), target, epoch=10))
    wrong = float(evidential_loss(logits(-30, -30, 8, -30, -30, -30), target, epoch=10))
    assert right < wrong


def test_class_weights_change_the_loss() -> None:
    batch = torch.randn(4, K)
    targets = torch.tensor([0, 1, 0, 0])
    flat = float(evidential_loss(batch, targets, epoch=5))
    weighted = float(
        evidential_loss(
            batch,
            targets,
            epoch=5,
            class_weights=torch.tensor([0.2, 4.0, 1.0, 1.0, 1.0, 1.0]),
        )
    )
    assert flat != pytest.approx(weighted)


def test_the_head_can_overfit_a_handful_of_examples() -> None:
    """A head that cannot fit eight examples has a broken gradient."""
    torch.manual_seed(0)
    head = EvidentialHead(12, K, hidden=16)
    features = torch.randn(8, 12)
    targets = torch.randint(0, K, (8,))
    optimizer = torch.optim.Adam(head.parameters(), lr=0.05)

    first = float(evidential_loss(head(features), targets, epoch=0))
    for step in range(120):
        optimizer.zero_grad()
        loss = evidential_loss(head(features), targets, epoch=min(step // 12, 10))
        loss.backward()
        optimizer.step()

    assert (head(features).argmax(dim=1) == targets).all()
    assert float(loss) < first


# ── temperature ──────────────────────────────────────────────────────────────


def test_temperature_above_one_raises_vacuity() -> None:
    """Dividing the logits shrinks the evidence, which lowers the Dirichlet
    strength, which is exactly what "it was overconfident" should mean."""
    from ml.evidential import temperature

    raw = logits(-5, 6.0, -5, -5, -5, -5)[0]
    before = trust_of(raw)
    after = trust_of(temperature.apply(raw, 2.5))
    assert after.vacuity > before.vacuity
    assert after.confidence < before.confidence


def test_temperature_of_one_is_the_identity() -> None:
    from ml.evidential import temperature

    raw = logits(1, 2, 3, 4, 5, 6)[0]
    assert torch.equal(temperature.apply(raw, 1.0), raw)


def test_temperature_is_clamped_to_a_sane_band() -> None:
    """A bad L-BFGS run at hour 22 must not flatten every confidence in the
    demo to 1/K."""
    from ml.evidential import temperature

    assert temperature.clamp(0.0) == temperature.MIN_TEMPERATURE
    assert temperature.clamp(1_000.0) == temperature.MAX_TEMPERATURE
