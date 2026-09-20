"""Live recalibration. Brief §10, plan §4 Stage 9.

Owner-only, 5/hour, and the last thing in the demo. Everything about this
endpoint is shaped by one fact: a judge is going to double-click the button.

**What is calibrated** is the evidential head's confidence over the six
relation classes, not the three routing buckets — `ml/evidential/
calibration.py`'s module docstring explains why conflating them would be a
category error rather than a shortcut.

**Idempotency is the whole design, not a flourish.** A repeated
`Idempotency-Key` returns the stored snapshot without refitting, so the
second click is a read. The unique index that enforces it is partial
(`calibration_snapshots`, §2.2) so the many NULL-key rows do not collide.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.exc import IntegrityError

from api.deps import PERM_CALIBRATION_RUN, ScopeDep, require_perm
from api.errors import Conflict, ValidationFailed
from api.mock import contract
from api.v1.schemas import (
    CalibrationImprovement,
    CalibrationMetrics,
    RecalibrateRequest,
    RecalibrateResponse,
)
from core.logging import get_logger
from core.ratelimit import LIMIT_RECALIBRATE, limiter, org_rate_limit_key
from db.models import CalibrationSnapshot
from ml.evidential import calibration as calib
from ml.evidential import temperature as temperature_mod

log = get_logger(__name__)

router = APIRouter(prefix="/calibration", tags=["calibration"])

LABEL_BASELINE = "baseline"
LABEL_POST = "post_recalibration"


def _metrics_payload(metrics: calib.Metrics) -> CalibrationMetrics:
    return CalibrationMetrics.model_validate(metrics.as_dict())


def _snapshot_response(
    before: CalibrationSnapshot, after: CalibrationSnapshot, n_hard_negatives: int, elapsed_ms: int
) -> RecalibrateResponse:
    """Build the response from two persisted rows.

    Reading back from the snapshots rather than from the in-memory metrics is
    deliberate: it means the replayed (idempotent) path and the freshly-fit
    path construct the response through exactly the same code, so a second
    click cannot return a subtly different shape from the first.
    """
    return RecalibrateResponse(
        before=CalibrationMetrics(
            temperature=before.temperature,
            ece=before.ece,
            mce=before.mce,
            brier=before.brier,
            bins=before.bins,
        ),
        after=CalibrationMetrics(
            temperature=after.temperature,
            ece=after.ece,
            mce=after.mce,
            brier=after.brier,
            bins=after.bins,
        ),
        improvement=CalibrationImprovement(
            # Sign convention matches the Stage 1 fixture exactly:
            # `after - before`, so an improvement is NEGATIVE. The frontend
            # was built against that fixture; flipping the sign here to read
            # more intuitively would silently invert an arrow in the UI.
            ece_absolute=round(after.ece - before.ece, 4),
            ece_relative=round((after.ece - before.ece) / before.ece, 4) if before.ece else 0.0,
        ),
        n_hard_negatives=n_hard_negatives,
        elapsed_ms=elapsed_ms,
        snapshot_id=after.id,
    )


@router.post(
    "/recalibrate",
    response_model=RecalibrateResponse,
    dependencies=[Depends(require_perm(PERM_CALIBRATION_RUN))],
    summary="Fit temperature scaling and report ECE before/after",
)
# Keyed per org, not per user: brief §13 specifies 5/hour/org, and a refit
# mutates tenant-wide state (the served temperature) rather than anything
# belonging to the caller.
@limiter.limit(LIMIT_RECALIBRATE, key_func=org_rate_limit_key)
@contract("calibration.recalibrate.json", stage=9)
def recalibrate(
    request: Request,
    # slowapi writes its `X-RateLimit-*` headers onto this parameter and
    # raises at call time without it. Third occurrence of that omission in
    # this project; see api/v1/voice.py::briefing for the long version.
    response: Response,
    scope: ScopeDep,
    payload: RecalibrateRequest,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=200,
            description="Replaying a key returns the same snapshot instead of refitting.",
        ),
    ] = None,
) -> RecalibrateResponse:
    """A one-parameter optimization over a few hundred logged cases. Target <4s.

    Temperature is fit on the logged hard negatives — cases where Nemotron's
    high-confidence routing disagreed with the classical model's
    high-confidence prediction. That biases toward the tail, which is where
    miscalibration lives but is not a production calibration strategy. A sharp
    judge will ask; the answer is ready, and it also ships in the response's
    own `interpretation` string rather than depending on anyone recalling it.
    """
    started = time.monotonic()

    if idempotency_key:
        replayed = _replay(scope, idempotency_key)
        if replayed is not None:
            return replayed

    cases = calib.load_cases(scope.db, scope.org_id)
    if not cases:
        raise ValidationFailed(
            "No labeled cases to calibrate against. Seed a scenario and ingest it first.",
            details={"fields": {"org": "no insights with gold relations"}},
        )

    negatives = calib.hard_negatives(cases)

    # Baseline is measured at the temperature the org is *actually* serving,
    # not at a hardcoded 1.0 — recalibrating twice must compare against what
    # the pipeline is doing now, otherwise the second run reports an
    # improvement it already banked.
    current = temperature_mod.current_temperature(scope.db, scope.org_id)
    before = calib.metrics(cases, current)

    # No disagreement set means nothing to fit. Reporting the baseline twice
    # with a zero improvement is the honest outcome; inventing a temperature
    # from the full distribution would quietly change what this endpoint
    # claims to do.
    fitted = calib.fit_temperature(negatives) if negatives else current

    # When the temperature is unchanged the "after" series *is* the "before"
    # series, so it is reused rather than recomputed. Rescoring at an unchanged
    # T round-trips the logits back through `trust_of` and lands within ~4e-9
    # of the stored confidence, not exactly on it, which made "no fit happened,
    # so nothing moved" almost-but-not-quite true. An endpoint whose no-op path
    # reports a nonzero ECE delta is reporting float noise as a calibration
    # result.
    after = before if fitted == current else calib.metrics(calib.rescored(cases, fitted), fitted)

    baseline_row = _persist(scope, LABEL_BASELINE, before, key=None, is_current=False)
    post_row = _persist(scope, LABEL_POST, after, key=idempotency_key, is_current=True)
    # Commit is the handler's job (`db/session.py`) — every other write
    # endpoint in this project does it explicitly, and omitting it here made
    # the endpoint return a `snapshot_id` for a row that was rolled back at
    # the end of the request. The idempotency replay then found nothing, so
    # a second click refit and returned a different id: the exact bug the
    # partial unique index exists to prevent, reintroduced above it.
    scope.db.commit()

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "recalibrated",
        org_id=str(scope.org_id),
        method=payload.method,
        n_cases=len(cases),
        n_hard_negatives=len(negatives),
        temperature=fitted,
        ece_before=round(before.ece, 4),
        ece_after=round(after.ece, 4),
        elapsed_ms=elapsed_ms,
        interpretation=calib.interpretation(before, after, len(negatives)),
    )
    return _snapshot_response(baseline_row, post_row, len(negatives), elapsed_ms)


def _replay(scope: ScopeDep, key: str) -> RecalibrateResponse | None:
    """The stored result for a repeated key, or None if it is a new key.

    Pairs the `post_recalibration` row with the `baseline` row written
    immediately before it, so the replay reports the same before/after it
    reported the first time rather than re-measuring a baseline that the
    recalibration itself has since changed.
    """
    post = scope.db.execute(
        scope.query(CalibrationSnapshot).where(
            CalibrationSnapshot.idempotency_key == key,
            CalibrationSnapshot.label == LABEL_POST,
        )
    ).scalar_one_or_none()
    if post is None:
        return None

    baseline = scope.db.execute(
        scope.query(CalibrationSnapshot)
        .where(
            CalibrationSnapshot.label == LABEL_BASELINE,
            CalibrationSnapshot.created_at <= post.created_at,
        )
        .order_by(CalibrationSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    # A post row always has a baseline written in the same transaction, so
    # this is defensive rather than expected — but returning the post row as
    # its own baseline would report a zero improvement and look like a bug in
    # the fit rather than in the lookup.
    if baseline is None:
        log.warning("calibration_replay_missing_baseline", snapshot_id=str(post.id))
        return None

    log.info("calibration_replayed", org_id=str(scope.org_id), snapshot_id=str(post.id))
    return _snapshot_response(baseline, post, post.n_cases, 0)


def _persist(
    scope: ScopeDep,
    label: str,
    metrics: calib.Metrics,
    *,
    key: str | None,
    is_current: bool,
) -> CalibrationSnapshot:
    if is_current:
        # Exactly one current snapshot per org: `temperature.current_temperature`
        # orders by `created_at DESC` and takes one, so a stale `is_current`
        # row would not break it — but it would make the table lie about
        # which temperature is live, and that table is what the eval endpoint
        # renders.
        for stale in scope.db.execute(
            scope.query(CalibrationSnapshot).where(CalibrationSnapshot.is_current.is_(True))
        ).scalars():
            stale.is_current = False

    row = CalibrationSnapshot(
        org_id=scope.org_id,
        label=label,
        temperature=metrics.temperature,
        ece=metrics.ece,
        mce=metrics.mce,
        brier=metrics.brier,
        bins=[b.as_dict() for b in metrics.bins],
        n_cases=metrics.n_cases,
        idempotency_key=key,
        is_current=is_current,
    )
    scope.db.add(row)
    try:
        scope.db.flush()
    except IntegrityError as exc:
        # Two clicks arriving close enough together that both passed the
        # replay check. The partial unique index is the real guarantee; this
        # turns the race into a 409 rather than a 500.
        scope.db.rollback()
        raise Conflict(
            "That idempotency key is already being processed. Retry the request.",
            details={"idempotency_key": key or ""},
        ) from exc
    return row
