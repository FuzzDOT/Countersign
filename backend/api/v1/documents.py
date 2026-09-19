"""Documents. Brief §5.

Contract published now, bodies land in Stage 2. The signatures here are not
sketches — the upload limits, the media-type allowlist and the RBAC guards are
final, because the frontend's drop zone validates against exactly these
(frontend brief §13) and the server is the authority.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status

from api.deps import (
    PERM_DOCUMENTS_UPLOAD,
    PERM_INSIGHTS_READ,
    PaginationDep,
    ScopeDep,
    require_perm,
)
from api.mock import NotImplementedYet, contract
from api.v1.schemas import (
    DocumentDetail,
    DocumentSummary,
    Paginated,
    SeedRequest,
    UploadResponse,
)
from core.config import Settings, get_settings
from core.ratelimit import LIMIT_SEED, LIMIT_UPLOAD, limiter
from db.models import DocSource

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


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_perm(PERM_DOCUMENTS_UPLOAD))],
    summary="Upload documents and start an ingest job",
)
@limiter.limit(LIMIT_UPLOAD)
@contract("documents.upload.json", stage=2, pending=True)
def upload_documents(
    request: Request,
    scope: ScopeDep,
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
    raise NotImplementedYet(stage=2)


@router.post(
    "/seed",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_perm(PERM_DOCUMENTS_UPLOAD))],
    summary="Load a named synthetic scenario",
)
@limiter.limit(LIMIT_SEED)
@contract("documents.seed.json", stage=2, pending=True)
def seed_scenario(request: Request, scope: ScopeDep, payload: SeedRequest) -> UploadResponse:
    """**This is the demo button.**

    Only the three servable scenarios are in the enum. `train_corpus` — the
    tagger and relation-model training split — is deliberately not reachable
    through the API: serving an insight extracted from training data would
    contaminate every number the project reports, and "was your eval set in
    your training set" is the first question a judge who does this for a living
    will ask.
    """
    raise NotImplementedYet(stage=2)


@router.get(
    "",
    response_model=Paginated[DocumentSummary],
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="List ingested documents",
)
@contract("documents.list.json", stage=2, pending=True)
def list_documents(
    scope: ScopeDep,
    pagination: PaginationDep,
    source: Annotated[list[DocSource] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Paginated[DocumentSummary]:
    raise NotImplementedYet(stage=2)


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    dependencies=[Depends(require_perm(PERM_INSIGHTS_READ))],
    summary="Full document text with citation spans",
)
@contract("documents.detail.json", stage=2, pending=True)
def get_document(scope: ScopeDep, document_id: uuid.UUID) -> DocumentDetail:
    """The citation reader's data source.

    `raw_text` is returned exactly as stored — every `char_start`/`char_end` in
    the response indexes into this precise string. The frontend slices it and
    renders into `textContent`; it must not normalize whitespace or pass it
    through a markdown renderer (brief §5, §13).

    A document belonging to another organization returns 404, not 403: a 403
    would confirm the id exists somewhere.
    """
    raise NotImplementedYet(stage=2)
