"""Building one briefing. Brief §11.

The actual logic behind `POST /voice/briefing`, factored out so
`scripts/record_fallback.py` can call the exact same code path rather than
maintain a second copy that could quietly drift from the live one — the
same reason `ml/voice/briefing_templates.py` holds the sentence wording
instead of `api/v1/voice.py` or the fixture builder each keeping their own.

Takes a plain `db`/`org_id` rather than the API layer's `Scope`, matching
`ml/ablation/engine.py` and `ml/voice/answer.py`: this is business logic
called from both an HTTP handler and a standalone script, so it cannot
depend on anything FastAPI-specific.

Raises `VoiceUnavailable` on a synthesis failure rather than catching it —
the two callers want different things when that happens. The live route
falls back to the prerecorded briefing; `scripts/record_fallback.py` is
*building* the prerecorded briefing, so there is nothing to fall back to
and the failure should just surface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.v1.schemas import BriefingResponse, TranscriptSegment
from core.config import Settings, get_settings
from core.security import sign_media_id
from db.models import Entity, Insight, RoutingBucket
from ml.voice import tts as tts_mod
from ml.voice.briefing_templates import narrate_insight, opening_line, rank_by_severity


def _candidate_insights(db: Session, org_id: uuid.UUID, request_scope: str) -> list[Insight]:
    query = select(Insight).where(Insight.org_id == org_id)
    if request_scope == "escalated":
        query = query.where(Insight.routing == RoutingBucket.escalate_now)
    elif request_scope == "flagged":
        query = query.where(Insight.routing != RoutingBucket.auto_file)
    # "all_new" applies no routing filter at all.
    return list(db.execute(query).scalars())


def _entity_name(db: Session, entity_id: uuid.UUID) -> str:
    entity = db.get(Entity, entity_id)
    return entity.canonical if entity else "?"


def build_briefing(
    db: Session,
    org_id: uuid.UUID,
    *,
    scope: str = "flagged",
    max_items: int = 3,
    voice_id: str | None = None,
    write_audio: bool = True,
    settings: Settings | None = None,
) -> tuple[BriefingResponse, bytes]:
    """Return the response *and* the raw audio bytes.

    The route writes those bytes to `data/audio/{briefing_id}.mp3` itself
    (`write_audio=True`, the default); `scripts/record_fallback.py` wants
    the bytes to write somewhere else entirely (the fallback path, not the
    per-request audio directory) and passes `write_audio=False` to skip that
    side effect while still getting everything else this function computes.
    """
    settings = settings or get_settings()
    candidates = _candidate_insights(db, org_id, scope)
    ranked = rank_by_severity(candidates, limit=max_items)

    opening = opening_line(len(ranked))
    sentences = [
        narrate_insight(
            subject=_entity_name(db, insight.subject_id),
            relation=insight.relation,
            object_=_entity_name(db, insight.object_id),
            confidence=insight.confidence,
            routing=str(insight.routing),
        )
        for insight in ranked
    ]
    full_text = " ".join([opening, *sentences])

    # Raises VoiceUnavailable on failure — deliberately not caught here, see
    # module docstring for why each caller needs to handle that differently.
    synthesis = tts_mod.synthesize(full_text, voice_id=voice_id, settings=settings)

    briefing_id = uuid.uuid4()
    if write_audio:
        directory = settings.path(settings.audio_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{briefing_id}.mp3").write_bytes(synthesis.audio_bytes)

    opening_end = tts_mod.segment_timing_ms(synthesis.alignment, full_text, opening)[1]
    segments = [
        TranscriptSegment(segment_id="s0", start_ms=0, end_ms=opening_end, text=opening, insight_id=None)
    ]
    position = len(opening) + 1  # +1 for the joining space before the first sentence
    for index, (insight, sentence) in enumerate(zip(ranked, sentences, strict=True)):
        start_ms, end_ms = tts_mod.segment_timing_ms(
            synthesis.alignment, full_text, sentence, start_from=position
        )
        segments.append(
            TranscriptSegment(
                segment_id=f"s{index + 1}",
                start_ms=start_ms,
                end_ms=end_ms,
                text=sentence,
                insight_id=insight.id,
            )
        )
        position = full_text.find(sentence, position) + len(sentence)

    from datetime import UTC, datetime

    expires_at, signature = sign_media_id(str(briefing_id), settings)
    response = BriefingResponse(
        briefing_id=briefing_id,
        audio_url=f"/api/v1/voice/audio/{briefing_id}.mp3?exp={expires_at}&sig={signature}",
        duration_ms=segments[-1].end_ms if segments else 0,
        transcript=segments,
        insight_ids=[insight.id for insight in ranked],
        generated_at=datetime.now(UTC),
        is_fallback=False,
    )
    return response, synthesis.audio_bytes
