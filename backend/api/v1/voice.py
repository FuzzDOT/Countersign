"""Voice. Brief §11.

The whole Out Loud track lives here, and so does the single most likely thing
to break on stage. Every path has a fallback, and the fallback is built before
the happy path is polished (frontend brief §12.3).

Nothing in this module generates text. Briefings are assembled from templates
over structured insight fields; spoken answers are assembled from the cited
source sentence plus the ablation result. `tests/test_no_generation.py` greps
this package for an upstream LLM call and fails if it finds one — which is how
the claim stays true at hour 23 when someone is tired.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from api.deps import PERM_VOICE_USE, ScopeDep, require_perm
from api.mock import NotImplementedYet, contract
from api.v1.schemas import AskResponse, BriefingRequest, BriefingResponse
from core.ratelimit import LIMIT_VOICE, limiter

router = APIRouter(prefix="/voice", tags=["voice"])

# 30s cap matches the frontend's push-to-talk countdown. webm/opus is what
# MediaRecorder produces in Chrome; wav is the Safari fallback.
ALLOWED_AUDIO_TYPES = frozenset({"audio/webm", "audio/ogg", "audio/wav", "audio/x-wav"})


@router.post(
    "/briefing",
    response_model=BriefingResponse,
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="Synthesize a spoken briefing with a segment-to-insight map",
)
@limiter.limit(LIMIT_VOICE)
@contract("voice.briefing.json", stage=8, pending=True)
def briefing(
    request: Request,
    scope: ScopeDep,
    payload: BriefingRequest,
) -> BriefingResponse:
    """Transcript segments carry `insight_id`, and that is the whole demo beat.

    The frontend highlights the corresponding feed row as each segment plays,
    so a judge *hears* "Meridian Supply routed a payment through Advent
    Holdings, confidence eighty-one percent" while watching that exact insight
    light up with its citation.

    Items are ranked by routing severity, not recency: the most severe thing
    goes first because a listener who stops after one sentence should have
    heard the worst news.
    """
    raise NotImplementedYet(stage=8)


@router.get(
    "/fallback/briefing",
    response_model=BriefingResponse,
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="Prerecorded briefing for the degraded path",
)
@contract("voice.fallback.json", stage=8, pending=True)
def fallback_briefing(scope: ScopeDep) -> BriefingResponse:
    """Returns the **same JSON shape** as a live briefing, transcript and all.

    This resolves a gap between the two briefs (plan §1.10): backend §11 says
    "returns a pre-recorded briefing", frontend §12.3 fetches it and plays it.
    Had it returned raw audio, the transcript-sync moment would die exactly
    when it is most needed — on bad wifi. Instead, degraded mode is visually
    identical to live mode, with `is_fallback: true` so the UI can show its
    honest note. Segment `insight_id` values are deterministic UUID5s that
    survive a database reset (core/ids.py).
    """
    raise NotImplementedYet(stage=8)


@router.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="Answer a spoken question from cited facts",
)
@limiter.limit(LIMIT_VOICE)
@contract("voice.ask.json", stage=8, pending=True)
def ask(
    request: Request,
    scope: ScopeDep,
    audio: Annotated[UploadFile, File(description="webm/opus or wav, <=30s")],
    context_insight_id: Annotated[uuid.UUID | None, Form()] = None,
) -> AskResponse:
    """STT, then a classical intent classifier, then a structured lookup.

    The intent classifier is TF-IDF plus a linear SVM over a small labeled set.
    No LLM: voice is the delivery channel for retrieved facts, not a text
    generator. On `unknown` intent the answer is a template asking for a
    rephrase — never a guess, because a confident wrong answer in a product
    about provenance is the worst possible failure.

    `heard` and `stt_confidence` are returned so the UI can show what it
    thought it heard. Admitting a mishearing is better than pretending it never
    happens.
    """
    raise NotImplementedYet(stage=8)


@router.get(
    "/audio/{file_id}.mp3",
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="Stream generated audio with range support",
    # `response_class=None` was wrong: FastAPI expects a Response subclass and
    # would fail when instantiating it. `response_model=None` is also required
    # for the reason documented on POST /auth/logout — annotation inference
    # through the @contract wrapper produces a truthy string.
    response_class=Response,
    response_model=None,
)
@contract(stage=8, pending=True)
def get_audio(
    scope: ScopeDep,
    file_id: uuid.UUID,
    exp: int | None = None,
    sig: str | None = None,
) -> Response:
    """Serves `audio/mpeg` with `Accept-Ranges: bytes` so `<audio>` can seek.

    `FileResponse` does not emit 206 Partial Content, so Stage 8 includes a
    small hand-written range responder — worth budgeting for rather than
    assuming.

    `file_id` is typed as a UUID and never concatenated into a path unchecked;
    that is the whole defense against traversal here. Accepts either a
    short-lived HMAC signature (`?exp=&sig=`, for `<audio src>`, which cannot
    carry an Authorization header) or a bearer token.
    """
    raise NotImplementedYet(stage=8)
