# Stage 10 fixes — overlay

Unzip over the repo root (the directory containing `backend/` and `Makefile`).
Every file here replaces one of the same path; `backend/scripts/verify_fallback.py`
is the only new file. Nothing under `frontend/` is touched.

    unzip -o countersign-stage10-fixes.zip -d /path/to/Countersign

19 files: 18 changed, 1 new.

---

## 1. HIGH — gate threshold was the pre-retrain value

`backend/core/config.py`, `.env.example`

`vacuity_gate_threshold` was `0.225` in both the code default and the file
`make init` copies. That value is correct for the head as it existed *before*
the Stage 7 residual-connection retrain. Against the checkpoints actually
committed in `backend/ml/checkpoints/` it escalates ~83% of insights (measured
48/58) instead of the 8–20% brief §16 requires — so a judge cloning the repo
and running `make init` would watch the cascade's whole thesis invert in
public, while `GET /routing/summary` reported it honestly.

Both now read `0.625`, the value `make tune-gate` measured after the retrain
and the one your local `.env` already had. The comment carries the full history
(0.45 → 0.225 → 0.625) and the reason each was right at the time, plus the
reminder that `make tune-gate` prints a recommendation and does not write
either file — which is how the two drifted apart in the first place.

## 2. LOW — `scoped_query()` did not exist under that name

`backend/api/deps.py`

Brief §13 and plan §4 Stage 0 both name `scoped_query()` in `api/deps.py` as
the multi-tenancy defence. The capability was there under a different shape
(`Scope.query()`), but the symbol the contract names did not resolve.

Added as a free function delegating to `Scope.query()` — one implementation
under two names, not two that can disagree. `ScopedModel` stays bound to
`OrgScoped`, so passing a model without an `org_id` is still a type error at
lint time. The docstring points callers at `Scope.child_query()` for tables
reachable only through an org-scoped parent, and notes there is deliberately
no unscoped equivalent.

No behavioural change. The underlying guarantee was already sound: every
`select()` in `api/v1/` and `workers/pipeline.py` that touches tenant data
carries an `org_id` predicate.

## 3. HIGH — `SERVE_STATIC` was read but never acted on

`backend/api/main.py`

`docker-compose.prod.yml` built the frontend to a volume, mounted it at
`/app/static`, set `SERVE_STATIC=true` and published only the backend — and
the backend served nothing at `/`. `settings.serve_static` was read in exactly
one place (the CSP in `api/middleware.py`) and `settings.static_dir` was never
read at all. `make prod` gave you a working API and a 404 homepage.

Added `_register_static()`:

- Mounts `/assets` via `StaticFiles` for Vite's content-hashed output.
- An SPA catch-all serving any real file it finds, else `index.html`, so
  frontend brief §3's deep links (`/app/feed/:insightId`,
  `/app/document/:docId?span=…`) survive a hard reload — which is what makes
  "jump straight to any screen if something breaks live" true.
- Registered **after** the API router and health routes, since Starlette
  resolves in registration order. Paths starting `api/` or `health` raise
  `NotFound` rather than returning 200 `text/html`, so a typo'd API call still
  gets the error envelope the frontend switches on.
- `index.html` is served `Cache-Control: no-cache` because it names hashed
  assets; caching it pins a browser to a stale bundle across a rebuild.
- Path traversal is rejected by `resolve()` + `is_relative_to(root)`.
- Non-fatal when the bundle is absent: a backend that refuses to boot because
  a frontend build is missing is useless for backend work.

**Paths resolve per request, not at startup.** The frontend container builds
for a minute or two then copies into the shared volume, and it cannot be a
compose `depends_on` of the backend (see fix 4). A startup-time existence
check therefore loses that race on a cold `make prod` and strands the bundle:
files present, routes never registered, `/` 404 until someone restarts the
API. Per-request resolution also means a rebuilt bundle is picked up with no
restart.

## 4. HIGH — `make prod` could not boot, for two independent reasons

`docker-compose.prod.yml`

The overlay set `ENVIRONMENT: production`. Process env beats the `.env` file,
so `check_production_safety()` ran against a clean `make init` — which copies
`.env.example`, shipping the placeholder `JWT_SECRET` (29 chars) and
`REFRESH_COOKIE_SECURE=false`. Three fatal findings, `SystemExit(78)`,
"FATAL: refusing to start" — on exactly the machine the overlay exists to
serve. And satisfying the guard by setting `REFRESH_COOKIE_SECURE=true` then
stops the `cs_refresh` cookie travelling over `http://localhost`, breaking
silent refresh (frontend §19) on the demo box.

Removed `ENVIRONMENT: production`. Nothing is lost: the strict CSP with no
`unsafe-inline` keys on `serve_static OR is_production`
(`api/middleware.py:101`), so `SERVE_STATIC=true` still turns it on. HSTS
stays off, which is correct over plain HTTP — sending it from a localhost demo
pins a judge's browser to https for a year. The header comment explains all of
this and states what to set for a genuine internet-facing deploy, so it reads
as a decision rather than an omission.

Also **removed a dependency cycle I nearly introduced.** My first pass added
`depends_on: frontend` to the backend to win the build race. The base
`docker-compose.yml` already has `frontend` depending on `backend`; compose
would have refused to start the stack. Ordering is handled in the application
instead (fix 3).

## 5. HIGH — the prerecorded voice fallback did not exist

`backend/api/v1/voice.py`, `backend/ml/voice/briefing.py`,
`backend/api/v1/schemas.py`, `backend/scripts/record_fallback.py`,
`backend/scripts/verify_fallback.py` (new), `Makefile`,
`backend/tests/test_voice_api.py`, `backend/data/synth/fixtures.py`,
`backend/data/fixtures/voice.{briefing,fallback}.json`

`backend/data/voice/` did not exist — no mp3, no transcript. Both fallback
routes correctly raised `VoiceUnavailable`, so the degraded path was a dead
end: live briefing fails on bad wifi → the frontend auto-fetches
`GET /voice/fallback/briefing` per §12.3 → that 503s too. Brief §11's line
about this path is "Conference wifi will fail. The demo will not."

**Two-tier degradation** (`_degraded_briefing`), used by both `POST /briefing`'s
except-branch and `GET /fallback/briefing`:

1. The prerecorded briefing, if `make record-fallback` has run.
2. Otherwise a text-only briefing built live from the org's insights —
   templates only, zero upstream calls, `audio_available: false`.

Tier 2 means no input to that endpoint produces a dead end. The
transcript-to-insight sync that frontend §12.1 calls the beat that sells the
track works without audio; the frontend skips the player and renders the
transcript.

Supporting changes:

- `compose_briefing_text()` / `build_text_only_briefing()` factored out of
  `build_briefing`, so live and degraded wording cannot drift. Segment timings
  come from `estimated_duration_ms` since there is no API alignment to use —
  approximate, and nothing plays against them.
- `BriefingResponse.audio_available: bool = True`. **Additive with a default**,
  deliberately: making `audio_url` nullable would break the generated
  TypeScript for every existing consumer. Clients ignoring the field behave
  exactly as before. Both committed fixtures and the fixture builder updated
  so `make fixtures-check` stays green.
- `_load_fallback_payload` now recomputes `audio_available` from whether the
  mp3 is actually on disk, rather than trusting a flag baked in at record
  time — the transcript and the audio are two files and can exist
  independently.
- `scripts/record_fallback.py --no-audio` writes only the transcript, with no
  ElevenLabs call, so a committable artifact can be produced from a machine
  with a seeded database but no key or no credits.
- `scripts/verify_fallback.py` + `make verify-fallback` makes brief §16's
  otherwise-unverifiable voice line a command with an exit code: `0` both
  files present, `1` transcript only, `2` nothing recorded. It validates the
  committed transcript through the production `BriefingResponse` model (a
  shape error there would be a 500 on the one path that exists not to fail)
  and warns if `is_fallback` is false or no segment carries an `insight_id`.

**One test changed, and it is the honest kind.**
`test_fallback_briefing_with_nothing_recorded_yet_is_voice_unavailable`
asserted a 503. That accurately described behaviour that was wrong, so it is
replaced by `test_fallback_briefing_with_nothing_recorded_degrades_to_text_only`
(200, `is_fallback: true`, `audio_available: false`, non-empty transcript,
every non-opening segment carrying an id from that org) plus
`test_a_recorded_fallback_wins_over_the_text_only_path`, which pins that tier 2
is a fallback and not a replacement for recording the real thing. The other
two tests in that file monkeypatch `_fallback_response` with
`lambda settings: obj`; that arity is unchanged and they still intercept.

**What this does not do: it does not manufacture audio.** There is no TTS in
the environment this was fixed in, and shipping a silent mp3 labelled as a
briefing would be worse than shipping none. You still want the real recording:

    # after rotating ELEVENLABS_API_KEY (see below)
    make seed s=meridian_shell_ring
    make record-fallback              # audio + transcript
    # or, with no key / thin credits:
    make record-fallback-transcript   # transcript only, commit it
    make verify-fallback

## 6. MEDIUM — `torch-geometric` was still a dependency

`backend/requirements.txt`

Pinned at `2.6.1` and imported by nothing (the only repo-wide match was a
docstring in `ml/relations/gat.py` explaining why the GAT is hand-rolled).
Plan §1.2 scheduled its removal for the Stage 10 cleanup.

Removed, with that section's reasoning left in place of the pin so nobody
re-adds it: ablation needs to mask named edges at inference time and read
per-edge, per-layer attention back out, which PyG's `GATConv` would mean
either mutating `edge_index` — a different counterfactual than the one claim 4
makes — or forking its forward pass. Also one less thing that has to install
correctly inside a slim image, which matters given fixes 3 and 4 mean
rebuilding that image.

## 7. LOW — recalibrate was rate-limited per user

`backend/core/security.py`, `backend/core/ratelimit.py`,
`backend/api/v1/calibration.py`

Brief §13 specifies `/calibration/recalibrate` at **5/hour/org**. The shared
`rate_limit_key` returns `user:<uuid>`, so an org with three owners got
fifteen refits an hour against a documented five — the one line in that table
that did not match the code.

Added `peek_token_org` (same discipline as `peek_token_subject`: signature
verified so a bucket cannot be forged, expiry not verified so an expired token
is still limited, never used for authorization) and `org_rate_limit_key`,
passed explicitly to that one decorator. Every other route keeps per-user
keying, which is what the brief specifies for them.

---

## Verification performed

No torch, sklearn, pydantic, httpx, Postgres or network in the environment
these fixes were made in, so nothing ran against FastAPI's router or a real
database. `make test-be` remains the real check. What was actually executed:

- **Repo-wide compile: 149 files, 0 failures.** All 18 edited files compile
  individually.
- **Undefined-name and unused-import audit clean** on every edited file (the
  AST check this project adopted after the `response: Response` incident).
- **SPA routing executed standalone against a real filesystem, 18/18 cases.**
  Deep links return the shell; real files are served; `api/*` and `health*`
  raise `NotFound` rather than returning HTML; four traversal attempts
  (`../secret.txt`, `../../etc/passwd`, `assets/../../secret.txt`,
  `sub/../../secret.txt`) resolve outside root and leak nothing; a missing
  bundle raises rather than 500s.
- **Text-only briefing segment math executed, 16/16 cases.** Segments
  contiguous with no gaps or overlaps, `start_ms >= 0` (the schema bound),
  every segment positive-duration, ids sequential from `s0`, opening carries
  no `insight_id`, the rest map to ranked insights in order, and the
  zero-insight `clean_baseline` case yields one valid segment with a positive
  duration rather than an empty transcript.
- **Two-tier fallback control flow executed, 5/5 cases**, including that the
  existing tests' `lambda settings: obj` monkeypatch still intercepts, and
  that a non-`VoiceUnavailable` error still propagates rather than being
  silently degraded.
- **`docker-compose.prod.yml` parsed as YAML** and asserted against:
  no `ENVIRONMENT`, `SERVE_STATIC=true`, `STATIC_DIR` set, the static volume
  mounted, no `depends_on` that would cycle with the base file's
  `frontend → backend`.
- **All 31 fixture JSON files still parse**, with the two briefing fixtures
  carrying `audio_available` in the position `model_dump` emits it.
- **`make -n verify-fallback record-fallback-transcript`** expands correctly
  (tabs verified).

Two things to watch on the first real run:

- Whether `@limiter.limit(..., key_func=org_rate_limit_key)` behaves as
  expected under slowapi 0.1.9. The signature supports it; I have not watched
  it.
- Whether `audio_available` appears in the regenerated OpenAPI schema so
  `make types` picks it up for the frontend.

## Still outstanding — not code, and not fixed here

**Rotate both API keys.** `Countersign.zip` shipped `.env` containing a live
`NEMOTRON_API_KEY` (`nvapi-…`) and a live `ELEVENLABS_API_KEY` (`sk_ee7…`)
plus the voice id. Git itself is clean — `.env` is gitignored, was never
tracked, and a secret-prefix grep across all tracked files returns nothing —
so this is purely the distribution channel. Rotate both, then consider
`git archive HEAD` instead of zipping the working tree, which would have
excluded it automatically.

**Two decisions still with you**, from `docs/STATE.md`: which insight the demo
script clicks (the pinned Meridian one is not load-bearing at any of its 24
edges, confirmed twice; `7469f821-3f83-5d46-9eed-41e1257f086d` clears the bar
at n=4), and how to report `nemotron_on_everything_accuracy: 0.1` (4/40 real
decisions — a genuine measurement, well below classical's 42%, worth reading
the actual `(ground_truth, predicted, rationale)` triples before writing it up
either way).
