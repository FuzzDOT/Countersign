"""Ingest job status. Brief §5.

Two ways to watch a job on purpose. The websocket is the good experience; the
REST endpoint is the one that works on conference wifi. The frontend opens the
socket and falls back to polling this every 2s if the socket has not opened
within 3s (brief §5) — so this endpoint has to stay cheap enough to poll.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from api.deps import (
    PERM_DOCUMENTS_UPLOAD,
    PERM_INSIGHTS_READ,
    PaginationDep,
    ScopeDep,
    iso_cursor_value,
    keyset_page,
    parse_iso_cursor_value,
    require_perm,
)
from api.errors import Conflict, NotFound
from api.mock import contract
from api.v1.schemas import JobOut, Paginated, StageProgress
from core.logging import get_logger
from db.models import IngestJob, JobState
from workers.pipeline import STAGE_KEYS, run_job

log = get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

TERMINAL_STATES = (JobState.done, JobState.failed)


def job_out(job: IngestJob) -> JobOut:
    """One place that turns a row into the response object.

    `stage_progress` is filled from `STAGE_KEYS` rather than from whatever
    happens to be in the JSONB column, so a job written before a stage existed
    still returns all five keys and the frontend's progress bar does not have
    to handle a missing one.
    """
    stored = job.stage_progress or {}
    return JobOut(
        id=job.id,
        state=job.state.value,
        docs_total=job.docs_total,
        docs_done=job.docs_done,
        insights_found=job.insights_found,
        stage_progress=StageProgress(**{key: stored.get(key, 0.0) for key in STAGE_KEYS}),
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
    )


@router.get(
    "/jobs",
    response_model=Paginated[JobOut],
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Recent ingest jobs",
)
@contract("ingest.jobs.json", stage=2)
def list_jobs(
    scope: ScopeDep,
    pagination: PaginationDep,
    active_only: Annotated[bool, Query()] = False,
) -> Paginated[JobOut]:
    stmt = scope.query(IngestJob)
    if active_only:
        stmt = stmt.where(IngestJob.state.notin_(TERMINAL_STATES))

    page = keyset_page(
        scope.db,
        stmt,
        pagination,
        sort_column=IngestJob.created_at,
        id_column=IngestJob.id,
        sort_value=iso_cursor_value,
        parse_sort_value=parse_iso_cursor_value,
    )
    return Paginated[JobOut](data=[job_out(job) for job in page.rows], pagination=page.envelope())


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Job status and per-stage progress",
)
@contract("ingest.job.relating.json", stage=2)
def get_job(scope: ScopeDep, job_id: uuid.UUID) -> JobOut:
    """Single indexed row read. Safe to poll at 2s.

    `stage_progress` carries a fraction per pipeline stage rather than one
    overall percentage, because "relating 56%" is a useful thing to show a
    judge watching a progress bar and "48%" is not.
    """
    return job_out(scope.get_or_404(IngestJob, job_id))


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobOut,
    dependencies=[Depends(require_perm(PERM_DOCUMENTS_UPLOAD))],
    summary="Retry a failed job",
)
@contract("ingest.job.queued.json", stage=2)
def retry_job(scope: ScopeDep, job_id: uuid.UUID, background: BackgroundTasks) -> JobOut:
    """Backs the retry button on a failed job card (frontend brief §4.3).

    Re-runs the pipeline over the job's existing documents; it does not
    re-upload or re-extract, so a `PIPELINE_FAILED` at the relating stage costs
    seconds to recover from rather than a re-drop.

    Only a terminal job can be retried. Restarting a running one would give
    two threads the same rows and the same `stage_progress` column, and the
    resulting progress bar would move backwards in front of a judge.
    """
    job = scope.get_or_404(IngestJob, job_id)
    if job.state not in TERMINAL_STATES:
        raise Conflict(
            "That job is still running.",
            details={"state": job.state.value, "id": str(job.id)},
        )
    if not job.doc_ids:
        raise NotFound("That job has no documents to re-run.", details={"id": str(job.id)})

    job.state = JobState.queued
    job.docs_done = 0
    job.insights_found = 0
    job.error = None
    job.error_stage = None
    job.started_at = None
    job.finished_at = None
    job.stage_progress = dict.fromkeys(STAGE_KEYS, 0.0)
    scope.db.commit()

    background.add_task(run_job, job.id)
    log.info("ingest_job_retried", job_id=str(job.id), documents=len(job.doc_ids))
    return job_out(job)
