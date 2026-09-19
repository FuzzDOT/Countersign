"""Ingest job status. Brief §5.

Two ways to watch a job on purpose. The websocket is the good experience; the
REST endpoint is the one that works on conference wifi. The frontend opens the
socket and falls back to polling this every 2s if the socket has not opened
within 3s (brief §5) — so this endpoint has to stay cheap enough to poll.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import PERM_DOCUMENTS_UPLOAD, PERM_INSIGHTS_READ, PaginationDep, ScopeDep, require_perm
from api.mock import NotImplementedYet, contract
from api.v1.schemas import JobOut, Paginated

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.get(
    "/jobs",
    response_model=Paginated[JobOut],
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Recent ingest jobs",
)
@contract("ingest.jobs.json", stage=2, pending=True)
def list_jobs(
    scope: ScopeDep,
    pagination: PaginationDep,
    active_only: Annotated[bool, Query()] = False,
) -> Paginated[JobOut]:
    raise NotImplementedYet(stage=2)


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Job status and per-stage progress",
)
@contract("ingest.job.relating.json", stage=2, pending=True)
def get_job(scope: ScopeDep, job_id: uuid.UUID) -> JobOut:
    """Single indexed row read. Safe to poll at 2s.

    `stage_progress` carries a fraction per pipeline stage rather than one
    overall percentage, because "relating 56%" is a useful thing to show a
    judge watching a progress bar and "48%" is not.
    """
    raise NotImplementedYet(stage=2)


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobOut,
    dependencies=[Depends(require_perm(PERM_DOCUMENTS_UPLOAD))],
    summary="Retry a failed job",
)
@contract("ingest.job.queued.json", stage=2, pending=True)
def retry_job(scope: ScopeDep, job_id: uuid.UUID) -> JobOut:
    """Backs the retry button on a failed job card (frontend brief §4.3).

    Re-runs the pipeline over the job's existing documents; it does not
    re-upload or re-extract, so a `PIPELINE_FAILED` at the relating stage costs
    seconds to recover from rather than a re-drop.
    """
    raise NotImplementedYet(stage=2)
