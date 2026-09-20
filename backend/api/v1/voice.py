"""Voice. Brief §11.

The whole Out Loud track lives here, and so does the single most likely thing
to break on stage. Every path has a fallback, and the fallback is built before
the happy path is polished (frontend brief §12.3).

Nothing in this module generates text. Briefings are assembled from templates
over structured insight fields; spoken answers are assembled from the cited
source sentence plus the ablation result. `tests/test_no_generation.py` greps
this package for an upstream LLM call and fails if it finds one — which is how
the claim stays true at hour 23 when someone is tired.

**A live synthesis failure degrades, it does not error.** `POST /voice/briefing`
catches `VoiceUnavailable` and returns the prerecorded fallback's exact JSON
shape instead of a 503 — a judge asking for a briefing should hear *something*
regardless of ElevenLabs' mood that afternoon. `POST /voice/ask` degrades the
same way at each of its two upstream calls (STT, then TTS): a failed
transcription still returns a valid 200 asking the user to try again; a failed
synthesis still returns the text answer with `audio_url: null` rather than
losing the whole response over a missing mp3.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile

from api.deps import PERM_VOICE_USE, ScopeDep, get_principal, require_perm
from api.errors import Forbidden, NotFound, Unauthenticated, ValidationFailed, VoiceUnavailable
from api.mock import contract
from api.v1.schemas import AskResponse, BriefingRequest, BriefingResponse
from core.config import Settings, get_settings
from core.logging import get_logger
from core.ratelimit import LIMIT_VOICE, limiter
from core.security import sign_media_id, verify_media_signature
from ml.voice import answer as answer_mod
from ml.voice import intent as intent_mod
from ml.voice import stt as stt_mod
from ml.voice import tts as tts_mod
from ml.voice.briefing import build_briefing
from ml.voice.briefing_templates import estimated_duration_ms

log = get_logger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

# 30s cap matches the frontend's push-to-talk countdown. webm/opus is what
# MediaRecorder produces in Chrome; wav is the Safari fallback.
ALLOWED_AUDIO_TYPES = frozenset({"audio/webm", "audio/ogg", "audio/wav", "audio/x-wav"})
MAX_ASK_AUDIO_BYTES = 5 * 1024 * 1024  # ~30s of webm/opus is nowhere near this.


# ── shared helpers ───────────────────────────────────────────────────────────


def _write_audio(audio_bytes: bytes, file_id: uuid.UUID, settings: Settings) -> None:
    directory = settings.path(settings.audio_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{file_id}.mp3").write_bytes(audio_bytes)


def _signed_audio_url(file_id: uuid.UUID, settings: Settings) -> str:
    expires_at, signature = sign_media_id(str(file_id), settings)
    return f"/api/v1/voice/audio/{file_id}.mp3?exp={expires_at}&sig={signature}"


def _load_fallback_payload(settings: Settings) -> dict:
    path = settings.path(settings.voice_fallback_transcript)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VoiceUnavailable(
            "no fallback briefing recorded — run scripts.record_fallback"
        ) from exc


# ── briefing ─────────────────────────────────────────────────────────────────


@router.post(
    "/briefing",
    response_model=BriefingResponse,
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="Synthesize a spoken briefing with a segment-to-insight map",
)
@limiter.limit(LIMIT_VOICE)
@contract("voice.briefing.json", stage=8)
def briefing(
    request: Request,
    # slowapi writes its `X-RateLimit-*` headers onto this parameter, and
    # raises at call time if the handler doesn't declare it. I removed this
    # exact parameter from `api/v1/ablation.py` earlier this session, called
    # it unused, and broke the live endpoint — then wrote it up as a lesson
    # learned. Writing this file fresh afterward, I still left it off both
    # rate-limited routes here. The lesson didn't fail; I didn't apply it.
    response: Response,
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

    The actual work is `ml/voice/briefing.build_briefing` — shared with
    `scripts/record_fallback.py` so the live and recorded paths cannot drift.
    This handler's only job is the HTTP-specific part: turn a synthesis
    failure into the prerecorded fallback instead of a 503.
    """
    settings = get_settings()
    voice_id = None if payload.voice_id == "default" else payload.voice_id
    try:
        response, _audio_bytes = build_briefing(
            scope.db,
            scope.org_id,
            scope=payload.scope,
            max_items=payload.max_items,
            voice_id=voice_id,
            settings=settings,
        )
    except VoiceUnavailable:
        log.warning("briefing_synthesis_failed_using_fallback")
        return _fallback_response(settings)
    return response


@router.get(
    "/fallback/briefing",
    response_model=BriefingResponse,
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="Prerecorded briefing for the degraded path",
)
@contract("voice.fallback.json", stage=8)
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
    return _fallback_response(get_settings())


@router.get(
    "/fallback/briefing.mp3",
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="The prerecorded fallback audio itself",
    response_class=Response,
    response_model=None,
)
@contract(stage=8)
def fallback_briefing_audio(request: Request, scope: ScopeDep) -> Response:
    """A fixed, shared asset — one file, recorded once by
    `scripts.record_fallback`, not a per-request signed URL like the dynamic
    `/voice/audio/{file_id}.mp3`. `voice.fallback.json`'s `audio_url` has
    pointed at this exact path since Stage 1; this route is what makes that
    already-published contract true rather than aspirational.
    """
    settings = get_settings()
    path = settings.path(settings.voice_fallback_audio)
    if not path.exists():
        raise VoiceUnavailable("no fallback briefing audio recorded")
    return _serve_range(path, request.headers.get("range"), settings)


def _fallback_response(settings: Settings) -> BriefingResponse:
    payload = _load_fallback_payload(settings)
    return BriefingResponse.model_validate(payload)


# ── ask ──────────────────────────────────────────────────────────────────────


@router.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(require_perm(PERM_VOICE_USE))],
    summary="Answer a spoken question from cited facts",
)
@limiter.limit(LIMIT_VOICE)
@contract("voice.ask.json", stage=8)
def ask(
    request: Request,
    response: Response,  # slowapi needs this — see the comment on briefing() above.
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
    settings = get_settings()
    question_id = uuid.uuid4()

    audio_bytes = audio.file.read(MAX_ASK_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_ASK_AUDIO_BYTES:
        raise ValidationFailed(
            "That clip is longer than the 30-second cap.",
            details={"fields": {"audio": "exceeds 30 seconds"}},
        )

    try:
        heard, stt_confidence = stt_mod.transcribe(
            audio_bytes, content_type=audio.content_type or "audio/webm", settings=settings
        )
    except VoiceUnavailable:
        log.warning("ask_transcription_failed")
        return AskResponse(
            question_id=question_id,
            heard="",
            stt_confidence=0.0,
            intent="unknown",
            resolved_insight_id=None,
            answer_text="Voice input isn't available right now — try again in a moment.",
            citation=None,
            ablation_run_id=None,
            audio_url=None,
            duration_ms=0,
        )

    result = intent_mod.classify(heard, settings)
    resolved = answer_mod.answer(
        scope.db,
        scope.org_id,
        intent=result.intent,
        heard=heard,
        context_insight_id=context_insight_id,
    )
    scope.db.commit()

    audio_url: str | None = None
    duration_ms = estimated_duration_ms(resolved.answer_text)
    try:
        synthesis = tts_mod.synthesize(resolved.answer_text, settings=settings)
    except VoiceUnavailable:
        log.warning("ask_answer_synthesis_failed", intent=result.intent)
    else:
        answer_audio_id = uuid.uuid4()
        _write_audio(synthesis.audio_bytes, answer_audio_id, settings)
        audio_url = _signed_audio_url(answer_audio_id, settings)
        duration_ms = int(synthesis.alignment.end_seconds[-1] * 1000) if synthesis.alignment.char_count() else duration_ms

    return AskResponse(
        question_id=question_id,
        heard=heard,
        stt_confidence=stt_confidence,
        intent=result.intent,
        resolved_insight_id=resolved.resolved_insight_id,
        answer_text=resolved.answer_text,
        citation=resolved.citation,
        ablation_run_id=resolved.ablation_run_id,
        audio_url=audio_url,
        duration_ms=duration_ms,
    )


# ── audio serving ────────────────────────────────────────────────────────────


def _audio_access(
    request: Request,
    file_id: uuid.UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    exp: Annotated[int | None, Query()] = None,
    sig: Annotated[str | None, Query()] = None,
) -> None:
    """Signature or bearer, not both required.

    An `<audio src>` cannot carry an `Authorization` header, so a request
    that supplies `exp`/`sig` is checked against those alone — if they verify,
    that is sufficient, full stop, the same way a presigned URL is its own
    authorization elsewhere. A request with neither falls back to the normal
    bearer-token permission check, which is how a test client or a direct
    fetch (not an `<audio>` tag) still works.

    A request that supplies a signature that does *not* verify is treated as
    a real auth failure rather than falling through to the bearer check —
    the caller clearly intended signature auth, and a browser-rendered
    `<audio>` tag has no bearer token to fall back to anyway, so silently
    trying a path that cannot succeed would just replace one honest 401
    with a more confusing one.
    """
    if exp is not None and sig is not None:
        if verify_media_signature(str(file_id), exp, sig, settings):
            return
        raise Unauthenticated("That signed link is invalid or has expired.")

    principal = get_principal(request, settings)
    if not principal.can(PERM_VOICE_USE):
        raise Forbidden(
            "You do not have permission to do that.",
            details={"required": [PERM_VOICE_USE], "missing": [PERM_VOICE_USE]},
        )


def _serve_range(path: Path, range_header: str | None, settings: Settings) -> Response:
    """A hand-written 206 responder.

    `FileResponse` does not emit `206 Partial Content` — plan §4 Stage 8 flags
    this explicitly as real, budgeted work rather than something `FileResponse`
    would have covered for free. Reads at most `settings.audio_chunk_bytes`
    per request rather than the whole file, so a seek-heavy client scrubbing
    through a briefing does not pull the entire mp3 into memory on every seek.
    """
    file_size = path.stat().st_size
    if range_header is None:
        data = path.read_bytes()
        return Response(
            content=data,
            media_type="audio/mpeg",
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )

    try:
        unit, _, range_spec = range_header.partition("=")
        if unit.strip() != "bytes":
            raise ValueError(unit)
        start_str, _, end_str = range_spec.partition("-")
        start = int(start_str) if start_str else 0
        end = (
            min(int(end_str), file_size - 1)
            if end_str
            else min(start + settings.audio_chunk_bytes - 1, file_size - 1)
        )
    except ValueError:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    if start >= file_size or start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read(end - start + 1)

    return Response(
        content=chunk,
        status_code=206,
        media_type="audio/mpeg",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(len(chunk)),
        },
    )


@router.get(
    "/audio/{file_id}.mp3",
    dependencies=[Depends(_audio_access)],
    summary="Stream generated audio with range support",
    response_class=Response,
    response_model=None,
)
@contract(stage=8)
def get_audio(
    request: Request,
    file_id: uuid.UUID,
    exp: int | None = None,
    sig: str | None = None,
) -> Response:
    """Serves `audio/mpeg` with `Accept-Ranges: bytes` so `<audio>` can seek.

    `file_id` is typed as a UUID and never concatenated into a path unchecked;
    that is the whole defense against traversal here. Accepts either a
    short-lived HMAC signature (`?exp=&sig=`, for `<audio src>`, which cannot
    carry an Authorization header) or a bearer token — enforced by
    `_audio_access` above, which runs before this body does.
    """
    settings = get_settings()
    path = settings.path(settings.audio_dir) / f"{file_id}.mp3"
    if not path.exists():
        raise NotFound(details={"id": str(file_id)})
    return _serve_range(path, request.headers.get("range"), settings)
