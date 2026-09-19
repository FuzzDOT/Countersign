# Fallback audio

`fallback_briefing.mp3` — a pre-recorded briefing matching the
`meridian_shell_ring` scenario.

This exists because conference wifi is the single most likely thing to break
the demo. The frontend preloads it on mount and plays it automatically when
`/voice/briefing` returns `VOICE_UNAVAILABLE` or times out at 8s.

Record it once the real briefing works, then never touch it again.
Do not skip this. It is the cheapest insurance in the project.
