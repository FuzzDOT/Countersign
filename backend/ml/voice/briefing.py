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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import ids
from core.config import Settings, get_settings
from core.security import sign_media_id
from db.models import Entity, Insight, RoutingBucket
from ml.voice import tts as tts_mod
from ml.voice.briefing_templates import (
    estimated_duration_ms,
    narrate_insight,
    opening_line,
    rank_by_severity,
)

# `api.v1.schemas` is imported lazily, never at module scope. Importing it here
# closes a cycle: `api.v1.__init__` builds the aggregator router, which imports
# `api.v1.voice`, which imports this module. That is invisible when the process
# starts at `api.main` (api.v1 is initialised first) and fatal when it starts
# here — `python -m scripts.record_fallback` died on exactly that. Annotations
# are strings under `from __future__ import annotations`, so only the two call
# sites that construct these models need the runtime import.
if TYPE_CHECKING:
    from api.v1.schemas import BriefingResponse, TranscriptSegment


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


@dataclass(frozen=True, slots=True)
class BriefingText:
    """Everything a briefing says, before anything is spoken.

    Split out from `build_briefing` so the same wording can be produced with
    zero upstream calls. That is what makes the last-resort fallback in
    `api/v1/voice.py` possible: a briefing with no audio is still a briefing,
    and the transcript-to-insight sync (frontend brief §12.1) does not need
    ElevenLabs to have answered.
    """

    ranked: list[Insight]
    opening: str
    sentences: list[str]
    full_text: str


def compose_briefing_text(
    db: Session,
    org_id: uuid.UUID,
    *,
    scope: str = "flagged",
    max_items: int = 3,
) -> BriefingText:
    """Rank the insights and render the sentences. No network, no synthesis."""
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
    return BriefingText(
        ranked=list(ranked),
        opening=opening,
        sentences=sentences,
        full_text=" ".join([opening, *sentences]),
    )


def build_text_only_briefing(
    db: Session,
    org_id: uuid.UUID,
    *,
    scope: str = "flagged",
    max_items: int = 3,
    settings: Settings | None = None,
) -> BriefingResponse:
    """A briefing with a real transcript and no audio.

    The last resort behind `GET /voice/fallback/briefing`: live synthesis
    failed *and* nothing was ever recorded. Before this existed that
    combination returned 503, which made the degraded path a dead end —
    precisely the case brief §11 wrote "Conference wifi will fail. The demo
    will not." about.

    Segment timings come from `estimated_duration_ms` (a measured
    characters-per-second rate) rather than from the API's character-level
    alignment, because there is no API response to align against. They are
    approximate and nothing plays against them; they exist so the response
    shape is identical to a live briefing and the frontend needs no second
    code path. `audio_available=False` is what tells it to skip the player.
    """
    from api.v1.schemas import BriefingResponse, TranscriptSegment

    settings = settings or get_settings()
    text = compose_briefing_text(db, org_id, scope=scope, max_items=max_items)

    segments: list[TranscriptSegment] = []
    cursor = 0
    for index, (body, insight) in enumerate(
        [(text.opening, None), *[(s, i) for s, i in zip(text.sentences, text.ranked, strict=True)]]
    ):
        end = cursor + estimated_duration_ms(body)
        segments.append(
            TranscriptSegment(
                segment_id=f"s{index}",
                start_ms=cursor,
                end_ms=end,
                text=body,
                insight_id=insight.id if insight is not None else None,
            )
        )
        cursor = end

    return BriefingResponse(
        briefing_id=ids.stable_uuid("briefing", "text-only", str(org_id), scope),
        # Points at the fallback audio route rather than being left blank, so
        # the URL becomes correct the moment `make record-fallback` runs
        # without this response shape changing. `audio_available` is the flag
        # to branch on, not the presence of a string.
        audio_url="/api/v1/voice/fallback/briefing.mp3",
        duration_ms=segments[-1].end_ms if segments else 0,
        transcript=segments,
        insight_ids=[insight.id for insight in text.ranked],
        generated_at=datetime.now(UTC),
        is_fallback=True,
        audio_available=False,
    )


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
    from api.v1.schemas import BriefingResponse, TranscriptSegment

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
