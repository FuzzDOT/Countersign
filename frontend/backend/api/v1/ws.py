"""Job progress websocket. Brief §5.

In `MOCK_MODE` this streams the four job fixtures with realistic delays, which
is deliberate: the live progress bar and the socket-to-polling fallback are
fiddly to build, and frontend dev 2 should be able to get them right at hour 4
rather than discovering the transitions at hour 14.

**Auth note, stated rather than hidden.** The token arrives as a query
parameter because browsers cannot set headers on a WebSocket handshake. A JWT
in a URL can land in access logs, which is why prod runs uvicorn with
`--no-access-log`. A 60-second single-purpose ticket endpoint would be stricter,
but the contract in brief §5 is `?token=<access_jwt>` and frontend dev 2 codes
against it at hour 3 — changing it costs coordination that buys very little on
a 15-minute token over a localhost demo. It is in the debt ledger
(docs/03-BACKEND-PLAN.md §5) rather than quietly ignored.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, WebSocket
from starlette.websockets import WebSocketDisconnect

from api.deps import PERM_INSIGHTS_READ
from api.errors import AppError
from api.mock import load_fixture, register_streamed_fixtures
from core.config import get_settings
from core.logging import get_logger, new_request_id, set_request_id
from core.security import decode_access_token

log = get_logger(__name__)

router = APIRouter(prefix="/ws", tags=["ws"])

# Close codes. 1000 normal, 1008 policy violation (auth), 1011 server error.
WS_NORMAL = 1000
WS_POLICY_VIOLATION = 1008
WS_INTERNAL = 1011

# The mock progression, in order. Terminal frame is `done`, then the server
# closes with 1000 — which is exactly the sequence the real pipeline emits.
# Live polling cadence. The pipeline commits on every stage transition and
# every 5 documents (brief §5), so a poll this frequent with change detection
# reproduces exactly the push schedule the contract promises — without the
# pipeline needing a handle on the socket, which would couple a background
# thread to a connection that may already be gone.
POLL_INTERVAL_SECONDS = 0.4

# A socket that outlives any plausible job is a leak. `invoice_flood` is 120
# documents; ten minutes is generous by an order of magnitude.
MAX_STREAM_SECONDS = 600.0

MOCK_SEQUENCE: tuple[tuple[str, float], ...] = (
    ("ingest.job.queued.json", 0.4),
    ("ingest.job.tagging.json", 1.2),
    ("ingest.job.relating.json", 1.6),
    ("ingest.job.done.json", 1.1),
)

# These are served by this module rather than by a decorated handler, so they
# have to be declared explicitly or the fixture generator would consider two of
# them orphans and refuse to write them.
register_streamed_fixtures(*(name for name, _ in MOCK_SEQUENCE))


@router.websocket("/jobs/{job_id}")
async def job_progress(
    websocket: WebSocket,
    job_id: uuid.UUID,
    token: Annotated[str, Query(min_length=16, max_length=4096)],
) -> None:
    """Pushes a `JobOut` object on every state transition and every 5 documents."""
    set_request_id(new_request_id())
    settings = get_settings()

    try:
        principal = decode_access_token(token, settings)
    except AppError:
        # Closing before accept rejects the handshake outright, so an
        # unauthenticated client never gets an open socket.
        await websocket.close(code=WS_POLICY_VIOLATION, reason="invalid or expired token")
        return

    if not principal.can(PERM_INSIGHTS_READ):
        await websocket.close(code=WS_POLICY_VIOLATION, reason="insufficient permissions")
        return

    await websocket.accept()
    log.info("ws_opened", job_id=str(job_id), user_id=str(principal.user_id))

    try:
        if settings.mock_mode:
            await _stream_mock_progress(websocket, job_id)
        else:
            closed = await _stream_live_progress(websocket, job_id, principal.org_id)
            if closed:
                return
        await websocket.close(code=WS_NORMAL)
    except WebSocketDisconnect:
        # The frontend navigating away is normal, not an error.
        log.info("ws_client_disconnected", job_id=str(job_id))


async def _stream_mock_progress(websocket: WebSocket, job_id: uuid.UUID) -> None:
    for fixture_name, delay in MOCK_SEQUENCE:
        await asyncio.sleep(delay)
        frame: dict[str, Any] = dict(load_fixture(fixture_name))
        # Echo the id the client asked about so its query keys line up.
        frame["id"] = str(job_id)
        await websocket.send_json(frame)


def _read_job(job_id: uuid.UUID, org_id: uuid.UUID) -> dict[str, Any] | None:
    """Read one job row, scoped to the caller's organization.

    Synchronous on purpose: the session is sync (plan §1.1) and the caller
    hands this to `asyncio.to_thread`, which is the whole reason the handler
    can be `async def` without an async database driver.

    Returns None for a job that does not exist *for this org* — a job id
    belonging to another tenant is indistinguishable from one that was never
    created, which is the same answer `get_or_404` gives over HTTP.
    """
    from api.v1.ingest import job_out
    from db.models import IngestJob
    from db.session import db_session

    with db_session() as db:
        job = db.get(IngestJob, job_id)
        if job is None or job.org_id != org_id:
            return None
        return job_out(job).model_dump(mode="json")


def _signature(frame: dict[str, Any]) -> tuple[Any, ...]:
    """What counts as a change worth pushing.

    Progress is included, so the "every 5 documents" frames arrive; timestamps
    are not, so a job that is merely still running does not generate a frame
    per poll.
    """
    return (
        frame["state"],
        frame["docs_done"],
        frame["insights_found"],
        tuple(sorted(frame["stage_progress"].items())),
    )


async def _stream_live_progress(websocket: WebSocket, job_id: uuid.UUID, org_id: uuid.UUID) -> bool:
    """Poll the job row and push on change. True if the socket was closed here.

    Polling rather than a pub/sub channel: the pipeline runs in a background
    thread in this same process, and the alternative — handing it a reference
    to the socket — means a disconnected client can make an ingest job raise.
    A 0.4s poll of one indexed row is cheaper than that coupling.
    """
    last: tuple[Any, ...] | None = None
    deadline = time.monotonic() + MAX_STREAM_SECONDS

    while time.monotonic() < deadline:
        frame = await asyncio.to_thread(_read_job, job_id, org_id)
        if frame is None:
            await websocket.close(code=WS_POLICY_VIOLATION, reason="unknown job")
            return True

        signature = _signature(frame)
        if signature != last:
            await websocket.send_json(frame)
            last = signature

        if frame["state"] in ("done", "failed"):
            return False

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    log.warning("ws_stream_timeout", job_id=str(job_id))
    await websocket.close(code=WS_INTERNAL, reason="job did not finish in time")
    return True
