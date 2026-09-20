"""Is the degraded voice path actually armed? Brief §16, plan §4 Stage 10.

    make verify-fallback

Brief §16's voice checklist has a line that was, for most of this project's
life, unverifiable by anything but reading the code:

    [ ] `VOICE_UNAVAILABLE` path returns the prerecorded fallback and the
        frontend plays it

`make record-fallback` writes two independent files and neither existed for a
long time, so the fallback route 503'd and nothing said so until someone
pulled the plug on stage. Plan §4 Stage 10's whole premise is that "the
failures that kill demos are the ones nobody rehearsed" — so this is the
rehearsal, as a command with an exit code.

Exit codes are the contract:

    0   audio + transcript both present. The full beat works offline.
    1   transcript present, audio missing. The degraded path returns a real
        briefing with `audio_available: false` — honest, usable, not the full
        beat. Record the audio.
    2   neither present. The route still degrades to a live text-only
        briefing rather than erroring, but nothing is committed, so this
        depends on the database being seeded at demo time.

Deliberately does not touch the network or the database: it validates what is
on disk against the production response model, which is the part that can be
wrong silently.
"""

from __future__ import annotations

import json

from api.v1.schemas import BriefingResponse
from core.config import get_settings


def main() -> int:
    settings = get_settings()
    audio = settings.path(settings.voice_fallback_audio)
    transcript = settings.path(settings.voice_fallback_transcript)

    has_audio = audio.is_file()
    has_transcript = transcript.is_file()

    print(f"audio      {'OK ' if has_audio else 'MISSING'}  {audio}")
    if has_audio:
        print(f"           {audio.stat().st_size} bytes")
    print(f"transcript {'OK ' if has_transcript else 'MISSING'}  {transcript}")

    if not has_transcript:
        print()
        print("Nothing recorded. GET /voice/fallback/briefing will build a text-only")
        print("briefing live from the database instead of erroring, so the demo does")
        print("not break — but it needs a seeded org, and there is no audio.")
        print()
        print("  make record-fallback              # audio + transcript (needs a key)")
        print("  make record-fallback-transcript   # transcript only (needs a seeded db)")
        return 2

    # The committed transcript is served straight back to the frontend, so a
    # shape error here is a 500 on the one path that exists to not fail.
    # Validating it through the same model the live route returns is the only
    # check that catches a hand-edited file.
    payload = json.loads(transcript.read_text(encoding="utf-8"))
    payload["audio_available"] = has_audio
    briefing = BriefingResponse.model_validate(payload)

    segments = len(briefing.transcript)
    linked = sum(1 for s in briefing.transcript if s.insight_id is not None)
    print()
    print(f"validates against BriefingResponse: {segments} segments, {linked} insight-linked")
    print(f"is_fallback={briefing.is_fallback}  audio_available={briefing.audio_available}")

    if not briefing.is_fallback:
        print()
        print("WARNING: is_fallback is false in the recorded transcript. The frontend")
        print("keys its 'playing recorded briefing' note on that flag, so it would")
        print("present a recording as live synthesis.")

    if linked == 0:
        print()
        print("WARNING: no segment carries an insight_id, so the transcript-to-feed")
        print("highlight sync (frontend brief §12.1) has nothing to drive. Re-record")
        print("against a seeded meridian_shell_ring org.")

    if not has_audio:
        print()
        print("Transcript only. The degraded path serves it with audio_available=false,")
        print("which the frontend should render as a transcript with no player.")
        print("Run `make record-fallback` once ELEVENLABS_API_KEY works to add audio.")
        return 1

    print()
    print("Degraded voice path is fully armed: audio and transcript both committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
