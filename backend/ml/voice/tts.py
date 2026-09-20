"""ElevenLabs text-to-speech. Brief §11.

Raw `httpx`, not the `elevenlabs` SDK — same call this project made for
`ml/cascade/nemotron.py`, and for the same reason: a hosted-model SDK's
method names and return shapes drift under a project this size faster than
a pinned `requirements.txt` entry does, and this whole build has already
been bitten twice this session by trusting a model string and a default
parameter that had quietly moved. A raw request against a documented REST
endpoint is one fewer place for that to happen invisibly.

**The `/with-timestamps` endpoint, specifically, not plain `/text-to-speech`.**
Segment `start_ms`/`end_ms` in `BriefingResponse.transcript` have to line up
with the actual audio, and the character-level alignment this endpoint
returns is the only honest way to get that — everything else in this module
exists to turn that per-character data into per-segment timing.

Failure here is never fatal to the response: every caller in
`api/v1/voice.py` catches `VoiceUnavailable` and falls back to the
prerecorded briefing or a text-only answer. Losing the audio is a
degraded experience; it is not a 500.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

import httpx

from api.errors import VoiceUnavailable
from core.config import Settings, get_settings
from core.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.elevenlabs.io/v1"
_WITH_TIMESTAMPS = "/text-to-speech/{voice_id}/with-timestamps"


@dataclass(frozen=True, slots=True)
class Alignment:
    """Character-level timing for one synthesized string.

    Parallel arrays, index-aligned with `characters[i]` itself — exactly the
    shape ElevenLabs returns, kept as-is rather than restructured into a
    list of records, because the only thing this is ever used for is a
    binary search by character offset (`segment_timing_ms` below), and a
    list of records would just be three arrays re-zipped back together at
    the point of use.
    """

    characters: tuple[str, ...]
    start_seconds: tuple[float, ...]
    end_seconds: tuple[float, ...]

    def char_count(self) -> int:
        return len(self.characters)


@dataclass(frozen=True, slots=True)
class Synthesis:
    audio_bytes: bytes
    alignment: Alignment
    full_text: str


def synthesize(
    text: str,
    *,
    voice_id: str | None = None,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Synthesis:
    """One TTS call, with character-level timing.

    `text` is the *entire* narration for one briefing or one answer — the
    briefing's several segments are concatenated into one call rather than
    synthesized separately, because separate calls would need their own
    silence-gap stitching to sound natural, and ElevenLabs bills per
    character regardless of how many calls it takes.

    `transport` is injectable for the same reason `ml/cascade/nemotron.py`'s
    is: `httpx.MockTransport` is the only honest way to test an integration
    whose credential this repository does not have committed anywhere, and
    a bare `httpx.post()` call cannot accept one at all — every code path
    here, including the timeout and status-code handling, is real; only
    the server is faked.
    """
    settings = settings or get_settings()
    if not settings.elevenlabs_api_key:
        raise VoiceUnavailable("no ELEVENLABS_API_KEY configured")

    resolved_voice = voice_id or settings.elevenlabs_voice_id
    if not resolved_voice:
        raise VoiceUnavailable("no voice id configured or supplied")

    payload = {
        "text": text,
        "model_id": settings.elevenlabs_model,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    headers = {"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"}
    url = f"{BASE_URL}{_WITH_TIMESTAMPS.format(voice_id=resolved_voice)}"

    try:
        with httpx.Client(transport=transport, timeout=settings.elevenlabs_timeout_seconds) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise VoiceUnavailable(
            f"timeout after {settings.elevenlabs_timeout_seconds}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"transport error: {exc}") from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise VoiceUnavailable(f"upstream returned {response.status_code}")
    if response.status_code >= 400:
        raise VoiceUnavailable(
            f"upstream rejected the request with {response.status_code}"
        )

    try:
        body = response.json()
        audio_bytes = base64.b64decode(body["audio_base64"])
        # `normalized_alignment` covers the text ElevenLabs actually spoke
        # (expanded numbers, normalized punctuation); `alignment` covers the
        # text as submitted. Segment offsets are computed against the text
        # this module submitted, so `alignment` — not the normalized one —
        # is the one whose character indices line up with `full_text` below.
        raw = body["alignment"]
        alignment = Alignment(
            characters=tuple(raw["characters"]),
            start_seconds=tuple(raw["character_start_times_seconds"]),
            end_seconds=tuple(raw["character_end_times_seconds"]),
        )
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise VoiceUnavailable(f"unreadable response envelope: {exc}") from exc

    log.info(
        "tts_synthesized",
        chars=len(text),
        audio_bytes=len(audio_bytes),
        voice_id=resolved_voice,
    )
    return Synthesis(audio_bytes=audio_bytes, alignment=alignment, full_text=text)


def segment_timing_ms(alignment: Alignment, full_text: str, segment_text: str, start_from: int = 0) -> tuple[int, int]:
    """Where one segment's words fall in the full synthesized audio.

    `full_text` is the exact string that was submitted for synthesis, so a
    plain substring search against it is exact — no fuzzy matching needed,
    because this module controls both sides of the lookup. `start_from`
    lets a caller walking several segments in order avoid matching an
    earlier occurrence of the same words (two segments should never contain
    identical text in this codebase's templates, but the parameter costs
    nothing and removes the assumption).

    Falls back to an estimated duration (`estimated_duration_ms`) rather
    than raising if the segment text cannot be found — a briefing whose
    segments were built from the same call that produced `full_text` should
    never hit this path, but voice is exactly the kind of feature where a
    fallback for "should never happen" earns its keep on stage.
    """
    index = full_text.find(segment_text, start_from)
    if index == -1 or alignment.char_count() == 0:
        from ml.voice.briefing_templates import estimated_duration_ms

        duration = estimated_duration_ms(segment_text)
        return 0, duration

    end_index = min(index + len(segment_text), alignment.char_count()) - 1
    start_ms = int(alignment.start_seconds[index] * 1000)
    end_ms = int(alignment.end_seconds[max(end_index, index)] * 1000)
    return start_ms, end_ms
