"""Temperature scaling. Plan §4 Stage 9, used from Stage 3 onward.

One parameter, applied to the evidence logits before the Dirichlet reads
them. `T = 1` is the identity, which is what a freshly seeded database has —
so this is wired into the scoring stage from the start rather than bolted on
at hour 22, and Stage 9's recalibration becomes "write a number to a row"
instead of "change the pipeline".

Scaling the *logits* rather than the probabilities is what makes it coherent
with the evidential head: dividing by `T > 1` shrinks the evidence, which
lowers the Dirichlet strength, which raises vacuity — the model becomes less
certain in exactly the way "it was overconfident" means.
"""

from __future__ import annotations

import uuid

import torch
from sqlalchemy import select
from sqlalchemy.orm import Session
from torch import Tensor

from core.logging import get_logger
from db.models import CalibrationSnapshot

log = get_logger(__name__)

# No calibration snapshot yet, or none marked current.
IDENTITY = 1.0

# A temperature outside this range is a fitting failure, not a calibration.
# Clamped rather than trusted: a bad L-BFGS run at hour 22 must not be able
# to flatten every confidence in the demo to 1/K.
MIN_TEMPERATURE = 0.25
MAX_TEMPERATURE = 5.0


def apply(logits: Tensor, temperature: float) -> Tensor:
    if temperature == IDENTITY:
        return logits
    return logits / clamp(temperature)


def clamp(temperature: float) -> float:
    return min(max(temperature, MIN_TEMPERATURE), MAX_TEMPERATURE)


def current_temperature(db: Session, org_id: uuid.UUID) -> float:
    """The organization's live temperature, or 1.0.

    Read once per ingest job rather than per insight: it cannot change
    mid-job, and a query per insight would be the second-most-expensive thing
    in the pipeline.
    """
    value = db.execute(
        select(CalibrationSnapshot.temperature)
        .where(
            CalibrationSnapshot.org_id == org_id,
            CalibrationSnapshot.is_current.is_(True),
        )
        .order_by(CalibrationSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if value is None:
        return IDENTITY
    return clamp(float(value))


def rescale(logits: list[float], temperature: float) -> Tensor:
    return apply(torch.tensor(logits, dtype=torch.float32), temperature)
