"""Documents. Brief §5.

The upload limits, the media-type allowlist and the RBAC guards are the
server's authority — the frontend's drop zone mirrors them (frontend brief
§13) but does not define them.

Two things in here are load-bearing beyond the obvious:

**Extraction happens exactly once, here.** The string this module writes to
`documents.raw_text` is the coordinate system for every `char_start` and
`char_end` in the database (plan §3). Nothing downstream re-decodes a PDF or
re-joins a CSV, because a second extraction that differed by one character
would move every citation in the demo.

**Ingest is a background task, not a request.** `POST /documents` returns 202
with a job id after the documents are durable; the pipeline runs after the
response is sent. In-process `BackgroundTasks` rather than Celery is a
deliberate, documented shortcut (plan §6) — at demo volume it is correct, not
lazy, and the failure mode it gives up (a job lost to a process restart) costs
one click of the retry button.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from api.deps import (
    PERM_DOCUMENTS_UPLOAD,
    PERM_INSIGHTS_READ,
    PaginationDep,
    Scope,
    ScopeDep,
    iso_cursor_value,
    keyset_page,
    parse_iso_cursor_value,
    require_perm,
)
from api.errors import DocumentNotFound, PayloadTooLarge, ValidationFailed
from api.mock import contract
from api.v1.schemas import (
    DocumentDetail,
    DocumentIngested,
    DocumentSummary,
    MentionOut,
    Paginated,
    SeedRequest,
    SpanOut,
    UploadResponse,
)
from core.config import Settings, get_settings
from core.logging import get_logger
from core.ratelimit import LIMIT_SEED, LIMIT_UPLOAD, limiter
from db.models import DocSource, Document, Entity, Insight, Mention
from ml.text.extract import extract
from ml.text.parse import content_sha
from workers.pipeline import create_job, run_job
from workers.seed import seed_documents

log = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# Brief §5. `message/rfc822` is the .eml export a bookkeeper actually has.
ALLOWED_MEDIA_TYPES = frozenset(
    {
        "text/plain",
        "text/csv",
        "application/pdf",
        "message/rfc822",
    }
)


def upload_limits(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, int]:
    """Surfaced in the OpenAPI description so the frontend can mirror them."""
    return {
        "max_files": settings.max_upload_files,
        "max_bytes_per_file": settings.max_upload_bytes,
        "max_pdf_pages": settings.max_pdf_pages,
    }


def _parse_received_at(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationFailed(
            "`received_at` must be an ISO 8601 timestamp.",
            details={"fields": {"received_at": f"unparseable: {raw!r}"}},
        ) from None
    # Naive timestamps are treated as UTC rather than rejected: a bookkeeper
    # pasting `2026-09-14T08:31:00` means eight in the morning, not an error.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_perm(PERM_DOCUMENTS_UPLOAD))],
    summary="Upload documents and start an ingest job",
)
@limiter.limit(LIMIT_UPLOAD)
@contract("documents.upload.json", stage=2)
def upload_documents(
    request: Request,
    # slowapi writes its `X-RateLimit-*` headers onto this. With
    # `headers_enabled=True` the decorator raises at call time if the handler
    # does not take one, which is why every rate-limited route in this
    # codebase has it (see api/v1/auth.py).
    response: Response,
    scope: ScopeDep,
    settings: Annotated[Settings, Depends(get_settings)],
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File(description="<=50 files, <=2 MB each")],
    source: Annotated[DocSource, Form()],
    received_at: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    """Accepts a batch, returns a job id, extracts text in the background.

    Returns 202 rather than 201: the documents exist but no insight does yet,
    and the client is expected to follow the job (or the websocket) for
    progress. Duplicate documents are skipped by `content_sha` per org rather
    than rejected, so re-dropping a folder is safe.
    """
    if not files:
        raise ValidationFailed(
            "Upload at least one file.", details={"fields": {"files": "required"}}
        )
    if len(files) > settings.max_upload_files:
        raise ValidationFailed(
            f"At most {settings.max_upload_files} files per upload.",
            details={"fields": {"files": f"{len(files)} files, limit {settings.max_upload_files}"}},
        )

    stamp = _parse_received_at(received_at)
    known = set(
        scope.db.execute(
            select(Document.content_sha).where(Document.org_id == scope.org_id)
        ).scalars()
    )

    created: list[Document] = []
    duplicates = 0

    for upload in files:
        filename = upload.filename or "untitled"
        data = upload.file.read()
        # Checked after the read rather than from the Content-Length header,
        # which a client controls. The aggregate cap is enforced by
        # RequestSizeLimitMiddleware before any of this buffers (plan §5).
        if len(data) > settings.max_upload_bytes:
            raise PayloadTooLarge(
                f"{filename!r} is larger than the {settings.max_upload_bytes // 1024} KB limit.",
                details={"filename": filename, "bytes": len(data)},
            )

        extracted = extract(filename, data, content_type=upload.content_type, settings=settings)
        sha = content_sha(extracted.raw_text)
        if sha in known:
            duplicates += 1
            continue
        known.add(sha)

        document = Document(
            org_id=scope.org_id,
            source=source,
            title=extracted.title,
            raw_text=extracted.raw_text,
            content_sha=sha,
            received_at=stamp,
            meta={**extracted.meta, "uploaded_by": str(scope.user_id)},
        )
        scope.db.add(document)
        created.append(document)

    scope.db.flush()
    job = create_job(scope.db, org_id=scope.org_id, document_ids=[d.id for d in created])
    scope.db.commit()

    background.add_task(run_job, job.id)
    log.info(
        "documents_uploaded",
        job_id=str(job.id),
        created=len(created),
        duplicates_skipped=duplicates,
    )

    return UploadResponse(
        job_id=job.id,
        documents=[
            DocumentIngested(
                id=document.id,
                title=document.title,
                source=document.source,
                chars=len(document.raw_text),
            )
            for document in created
        ],
        duplicates_skipped=duplicates,
    )


@router.post(
    "/seed",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_perm(PERM_DOCUMENTS_UPLOAD))],
    summary="Load a named synthetic scenario",
)
@limiter.limit(LIMIT_SEED)
@contract("documents.seed.json", stage=2)
def seed_scenario(
    request: Request,
    response: Response,
    scope: ScopeDep,
    settings: Annotated[Settings, Depends(get_settings)],
    background: BackgroundTasks,
    payload: SeedRequest,
) -> UploadResponse:
    """**This is the demo button.**

    Only the three servable scenarios are in the enum. `train_corpus` — the
    tagger and relation-model training split — is deliberately not reachable
    through the API: serving an insight extracted from training data would
    contaminate every number the project reports, and "was your eval set in
    your training set" is the first question a judge who does this for a
    living will ask.
    """
    result = seed_documents(
        scope.db, org_id=scope.org_id, scenario=payload.scenario, settings=settings
    )

    # Re-seeding an already-seeded org creates no documents but should still
    # re-run the pipeline over them — pressing the demo button twice has to do
    # something visible, and a job over zero documents finishes instantly with
    # an empty feed.
    document_ids = result.document_ids
    if not document_ids:
        document_ids = list(
            scope.db.execute(
                select(Document.id)
                .where(Document.org_id == scope.org_id)
                .where(Document.meta["scenario"].astext == payload.scenario)
            ).scalars()
        )

    job = create_job(
        scope.db,
        org_id=scope.org_id,
        document_ids=document_ids,
        scenario=payload.scenario,
    )
    scope.db.commit()

    background.add_task(run_job, job.id)

    return UploadResponse(
        job_id=job.id,
        documents=[
            DocumentIngested(
                id=document.id,
                title=document.title,
                source=document.source,
                chars=len(document.raw_text),
            )
            for document in result.created
        ],
        duplicates_skipped=result.duplicates_skipped,
    )


@router.get(
    "",
    response_model=Paginated[DocumentSummary],
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="List ingested documents",
)
@contract("documents.list.json", stage=2)
def list_documents(
    scope: ScopeDep,
    pagination: PaginationDep,
    source: Annotated[list[DocSource] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Paginated[DocumentSummary]:
    """Newest first, keyset-paginated on `received_at`.

    `q` matches the title, not the body: the document list is a navigation
    aid, and full-text search over `raw_text` belongs to the insight feed's
    `q`, which has the trigram index behind it.
    """
    stmt = scope.query(Document)
    if source:
        stmt = stmt.where(Document.source.in_(source))
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{q}%"))

    page = keyset_page(
        scope.db,
        stmt,
        pagination,
        sort_column=Document.received_at,
        id_column=Document.id,
        sort_value=iso_cursor_value,
        parse_sort_value=parse_iso_cursor_value,
    )

    counts = _insight_counts(scope, [document.id for document in page.rows])
    return Paginated[DocumentSummary](
        data=[
            DocumentSummary(
                id=document.id,
                title=document.title,
                source=document.source,
                received_at=document.received_at,
                chars=len(document.raw_text),
                insight_count=counts.get(document.id, 0),
            )
            for document in page.rows
        ],
        pagination=page.envelope(),
    )


def _insight_counts(scope: Scope, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Insight counts for one page of documents.

    One grouped query over the page rather than a correlated subquery per
    row: the page is at most 100 documents and this keeps the list endpoint a
    two-query operation regardless of how many insights exist.
    """
    if not document_ids:
        return {}
    # `.tuples()` rather than the default `Row` sequence: it is what makes
    # the result a plain `Sequence[tuple[UUID, int]]`, which both `dict()` and
    # the type checker accept.
    rows = (
        scope.db.execute(
            select(Insight.document_id, func.count(Insight.id))
            .where(Insight.org_id == scope.org_id, Insight.document_id.in_(document_ids))
            .group_by(Insight.document_id)
        )
        .tuples()
        .all()
    )
    return dict(rows)


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Full document text with citation spans",
)
@contract("documents.detail.json", stage=2)
def get_document(scope: ScopeDep, document_id: uuid.UUID) -> DocumentDetail:
    """The citation reader's data source.

    `raw_text` is returned exactly as stored — every `char_start`/`char_end` in
    the response indexes into this precise string. The frontend slices it and
    renders into `textContent`; it must not normalize whitespace or pass it
    through a markdown renderer (brief §5, §13).

    A document belonging to another organization returns 404, not 403: a 403
    would confirm the id exists somewhere.
    """
    document = scope.get_or_404(Document, document_id, error=DocumentNotFound)

    # `entity_type` lives on the entity, not the mention — a mention is a
    # span, and its type is whatever the entity it resolved to turned out to
    # be. Joined here rather than denormalized onto `mentions` so a later
    # coreference correction cannot leave the two disagreeing.
    mentions = scope.db.execute(
        select(Mention, Entity.entity_type)
        .join(Entity, Entity.id == Mention.entity_id)
        .where(Mention.document_id == document.id, Entity.org_id == scope.org_id)
        .order_by(Mention.char_start, Mention.char_end)
    ).all()

    insights = scope.db.execute(
        scope.query(Insight).where(Insight.document_id == document.id).order_by(Insight.char_start)
    ).scalars()

    return DocumentDetail(
        id=document.id,
        title=document.title,
        source=document.source,
        received_at=document.received_at,
        raw_text=document.raw_text,
        spans=[
            SpanOut(
                insight_id=insight.id,
                char_start=insight.char_start,
                char_end=insight.char_end,
                relation=insight.relation,
                confidence=insight.confidence,
                routing=insight.routing,
            )
            for insight in insights
        ],
        mentions=[
            MentionOut(
                entity_id=mention.entity_id,
                surface=mention.surface,
                entity_type=entity_type,
                char_start=mention.char_start,
                char_end=mention.char_end,
                tagger_conf=mention.tagger_conf,
            )
            for mention, entity_type in mentions
        ],
    )
