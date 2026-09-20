"""ElevenLabs TTS and STT, against a mock transport. Brief §11.

Same honesty note as `tests/test_nemotron.py`: **no live call has ever been
made from this repository.** `ELEVENLABS_API_KEY` is unset here. Every path
below runs against `httpx.MockTransport` — the retry-free failure handling,
the header shape, the response parsing, are all real code running against a
real `httpx` client stack; only the server is faked. What is therefore not
proven here: that the live endpoint accepts this request shape, or that
`scribe_v2` and the `/with-timestamps` response fields are what this
account's key actually returns. `docs/STATE.md` records both as outstanding,
same as it already does for Nemotron.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from api.errors import VoiceUnavailable
from core.config import Settings, get_settings
from ml.voice import stt as stt_mod
from ml.voice import tts as tts_mod


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={"elevenlabs_api_key": "test-key", "elevenlabs_voice_id": "voice-1"}
    )


# ── TTS ──────────────────────────────────────────────────────────────────────


def _tts_ok(text: str = "hello there"):  # type: ignore[no-untyped-def]
    audio_b64 = base64.b64encode(b"\x00\x01fake-mp3-bytes").decode("ascii")
    characters = list(text)
    step = 0.08
    starts = [round(i * step, 3) for i in range(len(characters))]
    ends = [round((i + 1) * step, 3) for i in range(len(characters))]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "audio_base64": audio_b64,
                "alignment": {
                    "characters": characters,
                    "character_start_times_seconds": starts,
                    "character_end_times_seconds": ends,
                },
                "normalized_alignment": {
                    "characters": characters,
                    "character_start_times_seconds": starts,
                    "character_end_times_seconds": ends,
                },
            },
        )

    return httpx.MockTransport(handler)


def test_synthesize_decodes_audio_and_alignment(settings) -> None:  # type: ignore[no-untyped-def]
    result = tts_mod.synthesize("hello", settings=settings, transport=_tts_ok("hello"))
    assert result.audio_bytes == b"\x00\x01fake-mp3-bytes"
    assert result.alignment.char_count() == len("hello")
    assert result.full_text == "hello"


def test_synthesize_request_shape(settings) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key_header"] = request.headers.get("xi-api-key")
        seen["auth_header"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        audio_b64 = base64.b64encode(b"x").decode("ascii")
        return httpx.Response(
            200,
            json={
                "audio_base64": audio_b64,
                "alignment": {
                    "characters": ["h", "i"],
                    "character_start_times_seconds": [0.0, 0.1],
                    "character_end_times_seconds": [0.1, 0.2],
                },
            },
        )

    tts_mod.synthesize("hi", settings=settings, transport=httpx.MockTransport(handler))

    assert str(seen["url"]).endswith("/text-to-speech/voice-1/with-timestamps")
    # ElevenLabs uses its own header scheme, not `Authorization: Bearer` —
    # worth pinning explicitly since every other client in this codebase
    # (Nemotron) uses bearer auth and it would be an easy header to guess wrong.
    assert seen["key_header"] == "test-key"
    assert seen["auth_header"] is None
    body = seen["body"]
    assert body["text"] == "hi"  # type: ignore[index]
    assert body["model_id"] == settings.elevenlabs_model  # type: ignore[index]


def test_synthesize_without_a_key_fails_fast(settings) -> None:  # type: ignore[no-untyped-def]
    no_key = settings.model_copy(update={"elevenlabs_api_key": ""})
    with pytest.raises(VoiceUnavailable):
        tts_mod.synthesize("hello", settings=no_key)


def test_synthesize_without_a_voice_id_fails(settings) -> None:  # type: ignore[no-untyped-def]
    no_voice = settings.model_copy(update={"elevenlabs_voice_id": ""})
    with pytest.raises(VoiceUnavailable):
        tts_mod.synthesize("hello", settings=no_voice, transport=_tts_ok())


def test_synthesize_maps_429_and_5xx_to_voice_unavailable(settings) -> None:  # type: ignore[no-untyped-def]
    for status in (429, 500, 503):
        transport = httpx.MockTransport(lambda request, status=status: httpx.Response(status))
        with pytest.raises(VoiceUnavailable):
            tts_mod.synthesize("hello", settings=settings, transport=transport)


def test_synthesize_maps_4xx_to_voice_unavailable(settings) -> None:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(lambda request: httpx.Response(422))
    with pytest.raises(VoiceUnavailable):
        tts_mod.synthesize("hello", settings=settings, transport=transport)


def test_synthesize_unreadable_body_is_voice_unavailable(settings) -> None:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"nope": True}))
    with pytest.raises(VoiceUnavailable):
        tts_mod.synthesize("hello", settings=settings, transport=transport)


def test_synthesize_bad_base64_is_voice_unavailable(settings) -> None:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "audio_base64": "not valid base64!!!",
                "alignment": {
                    "characters": [],
                    "character_start_times_seconds": [],
                    "character_end_times_seconds": [],
                },
            },
        )
    )
    with pytest.raises(VoiceUnavailable):
        tts_mod.synthesize("hello", settings=settings, transport=transport)


def test_segment_timing_finds_the_right_slice() -> None:
    text = "First sentence. Second sentence."
    alignment = tts_mod.Alignment(
        characters=tuple(text),
        start_seconds=tuple(i * 0.05 for i in range(len(text))),
        end_seconds=tuple((i + 1) * 0.05 for i in range(len(text))),
    )
    start_ms, end_ms = tts_mod.segment_timing_ms(alignment, text, "Second sentence.")
    expected_index = text.index("Second sentence.")
    # Matches segment_timing_ms's own `int(seconds * 1000)` exactly, rather
    # than `round()` — the two can disagree by one on a value float
    # multiplication didn't land on an exact integer, and this should test
    # the real computation, not a rounding convention that happens to agree
    # with it for this particular index.
    assert start_ms == int(alignment.start_seconds[expected_index] * 1000)
    assert end_ms > start_ms


def test_segment_timing_falls_back_when_text_not_found() -> None:
    """Should never happen in practice — the segment text always comes from
    the same call that produced `full_text` — but a missing match falls back
    to an estimate instead of raising, which is the right failure mode for a
    "should never happen" path in a demo feature."""
    alignment = tts_mod.Alignment(characters=(), start_seconds=(), end_seconds=())
    start_ms, end_ms = tts_mod.segment_timing_ms(alignment, "abc", "not in there")
    assert start_ms == 0
    assert end_ms > 0


# ── STT ──────────────────────────────────────────────────────────────────────


def test_transcribe_happy_path(settings) -> None:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"text": "why is Meridian flagged"})
    )
    text, confidence = stt_mod.transcribe(b"fake-audio", settings=settings, transport=transport)
    assert text == "why is Meridian flagged"
    assert confidence == stt_mod.FALLBACK_CONFIDENCE_NONEMPTY


def test_transcribe_empty_result_gets_zero_confidence(settings) -> None:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"text": ""}))
    text, confidence = stt_mod.transcribe(b"fake-audio", settings=settings, transport=transport)
    assert text == ""
    assert confidence == stt_mod.FALLBACK_CONFIDENCE_EMPTY


def test_transcribe_uses_word_logprobs_when_present(settings) -> None:  # type: ignore[no-untyped-def]
    import math

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "text": "hello world",
                "words": [
                    {"word": "hello", "logprob": -0.1},
                    {"word": "world", "logprob": -0.3},
                ],
            },
        )
    )
    _, confidence = stt_mod.transcribe(b"fake-audio", settings=settings, transport=transport)
    assert confidence == pytest.approx(math.exp(-0.2), abs=1e-6)


def test_transcribe_request_shape(settings) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key_header"] = request.headers.get("xi-api-key")
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "ok"})

    stt_mod.transcribe(
        b"fake-audio",
        content_type="audio/webm",
        settings=settings,
        transport=httpx.MockTransport(handler),
    )
    assert str(seen["url"]).endswith("/speech-to-text")
    assert seen["key_header"] == "test-key"
    # Multipart, not a JSON body — confirmed by the content-type prefix
    # rather than parsing the multipart body directly.
    assert str(seen["content_type"]).startswith("multipart/form-data")


def test_transcribe_without_a_key_fails_fast(settings) -> None:  # type: ignore[no-untyped-def]
    no_key = settings.model_copy(update={"elevenlabs_api_key": ""})
    with pytest.raises(VoiceUnavailable):
        stt_mod.transcribe(b"fake-audio", settings=no_key)


def test_transcribe_maps_429_and_5xx_to_voice_unavailable(settings) -> None:  # type: ignore[no-untyped-def]
    for status in (429, 500, 503):
        transport = httpx.MockTransport(lambda request, status=status: httpx.Response(status))
        with pytest.raises(VoiceUnavailable):
            stt_mod.transcribe(b"fake-audio", settings=settings, transport=transport)


def test_transcribe_timeout_is_voice_unavailable(settings) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    with pytest.raises(VoiceUnavailable):
        stt_mod.transcribe(b"fake-audio", settings=settings, transport=httpx.MockTransport(handler))
