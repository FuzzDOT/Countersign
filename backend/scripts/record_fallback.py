"""Record the fallback briefing once. Brief §11, frontend brief §12.3.

    make record-fallback
    docker compose exec backend python -m scripts.record_fallback

"Fallback recorded and committed before the happy path is polished" — the
plan's own words for this stage. The reasoning: a live demo depending on
ElevenLabs actually answering, at that exact moment, in front of judges, is
a single point of failure this project has no business accepting when the
fix costs one script and one committed mp3.

This calls the *exact same* `ml/voice/briefing.build_briefing` the live
`POST /voice/briefing` endpoint calls — not a hand-rolled second version of
the same logic — so the fallback cannot quietly drift from what a working
live call would have produced. The only difference is what happens to the
result: instead of being handed back to one HTTP caller, it is frozen to
disk as `settings.voice_fallback_audio` and `settings.voice_fallback_transcript`.

Deterministic insight ids (plan §1.11, `core/ids.py`): this must run against
a `meridian_shell_ring`-seeded org, so the transcript's `insight_id` values
are the same UUID5s that exist in *any* fresh seed of that scenario — the
fallback stays correct after `make nuke && make seed`, without re-recording.
"""

from __future__ import annotations

import argparse
import json
import uuid

from core import ids
from core.config import get_settings
from core.logging import configure_logging, get_logger
from db.session import db_session
from ml.voice.briefing import build_briefing, build_text_only_briefing

log = get_logger(__name__)


def _write_transcript_only(org_id, args, settings) -> int:  # type: ignore[no-untyped-def]
    """The `--no-audio` path: transcript on disk, no upstream call.

    Uses `build_text_only_briefing`, the same function the live degraded path
    falls through to, so a committed transcript and an on-the-fly one cannot
    say different things about the same corpus. `audio_available` is not
    written here on purpose — `api/v1/voice._load_fallback_payload` recomputes
    it from whether the mp3 actually exists, so dropping a real recording in
    later flips it without rewriting this file.
    """
    with db_session() as db:
        response = build_text_only_briefing(
            db,
            org_id,
            scope=args.scope,
            max_items=args.max_items,
            settings=settings,
        )

    payload = response.model_dump(mode="json")
    payload["is_fallback"] = True
    payload["briefing_id"] = str(ids.stable_uuid("briefing", "fallback"))
    payload["audio_url"] = "/api/v1/voice/fallback/briefing.mp3"

    transcript_path = settings.path(settings.voice_fallback_transcript)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    log.info(
        "fallback_transcript_recorded",
        transcript_path=str(transcript_path),
        segments=len(payload["transcript"]),
        insight_ids=len(payload["insight_ids"]),
        audio="skipped (--no-audio)",
    )
    print(
        f"wrote {transcript_path} ({len(payload['transcript'])} segments, "
        f"{len(payload['insight_ids'])} insights) with NO audio. "
        "Re-run without --no-audio once ELEVENLABS_API_KEY works to add the mp3."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default=None, help="Defaults to the demo tenant.")
    parser.add_argument("--scope", default="flagged", choices=("flagged", "escalated", "all_new"))
    parser.add_argument("--max-items", type=int, default=3)
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help=(
            "Write only the transcript, with no ElevenLabs call. Produces a "
            "committable fallback artifact from a machine that has a seeded "
            "database but no API key or no character credits. The audio is "
            "still worth recording for real later — this makes the degraded "
            "path serve a correct transcript in the meantime rather than "
            "nothing."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)
    org_id = uuid.UUID(args.org) if args.org else ids.DEMO_ORG_ID

    if args.no_audio:
        return _write_transcript_only(org_id, args, settings)

    with db_session() as db:
        response, audio_bytes = build_briefing(
            db,
            org_id,
            scope=args.scope,
            max_items=args.max_items,
            # The fallback audio lives at a fixed path
            # (`settings.voice_fallback_audio`), not the per-briefing
            # `data/audio/{uuid}.mp3` the live route writes to — so the
            # live-path write is skipped here and this script does its own,
            # below.
            write_audio=False,
            settings=settings,
        )
        # `build_briefing` only reads (insights, entities); `db_session`'s
        # commit on exit has nothing to commit either way.

    audio_path = settings.path(settings.voice_fallback_audio)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(audio_bytes)

    # `is_fallback: true` and a deterministic `briefing_id` — the recorded
    # briefing is served forever after, so its identity should not change
    # every time this script re-runs, unlike the live route's random uuid4.
    payload = response.model_dump(mode="json")
    payload["is_fallback"] = True
    payload["briefing_id"] = str(ids.stable_uuid("briefing", "fallback"))
    payload["audio_url"] = "/api/v1/voice/fallback/briefing.mp3"

    transcript_path = settings.path(settings.voice_fallback_transcript)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    log.info(
        "fallback_recorded",
        audio_path=str(audio_path),
        audio_bytes=len(audio_bytes),
        transcript_path=str(transcript_path),
        segments=len(payload["transcript"]),
        insight_ids=len(payload["insight_ids"]),
    )
    print(
        f"wrote {audio_path} ({len(audio_bytes)} bytes) and {transcript_path} "
        f"({len(payload['transcript'])} segments, {len(payload['insight_ids'])} insights)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
