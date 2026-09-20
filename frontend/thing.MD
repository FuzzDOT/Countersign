I'll start by reading through all the planning documents and then the codebase.Now let me extract the codebase and read the frontend brief in parallel.No torch/FastAPI/Postgres here, so verification will be static (AST + spec diff), same constraint the earlier stages worked under. Let me start with a repo-wide integrity pass.Route table is complete. Now the response contract — this is where frontend compatibility lives.Schemas look faithful. Now the frontend brief — I need its expectations to check compatibility.Now the detailed verification. Starting with config/env parity and the error envelope.Immediate finding before I continue — let me verify its scope in git.`scoped_query()` is named differently here. Let me audit whether org-scoping is actually enforced everywhere.Now the schema — brief DDL plus the five plan §2 deltas.Let me check for drift between the ORM models and the Alembic migration.Schema/migration are in sync. Now the insights feed — the brief specifies 11 filter params plus sort and cursor.## Stage 10 — Backend verification, interim report

I read all four markdown documents, extracted the codebase, and completed a static verification pass (no torch/FastAPI/Postgres/network in this container, so nothing was executed — same constraint every prior stage worked under; `make test-be` remains the real check). I hit the tool limit partway through, so this covers what's confirmed, and I've flagged what's still unverified at the end.

---

## Finding 1 — CRITICAL, act on this first: live API keys are in the shipped zip

`Countersign.zip` contains `.env` with **real, working credentials**:

| Variable | State in zip |
|---|---|
| `NEMOTRON_API_KEY` | live, 70 chars, `nvapi-…` |
| `ELEVENLABS_API_KEY` | live, 51 chars, `sk_ee7…` |
| `ELEVENLABS_VOICE_ID` | live, 20 chars |

`docs/STATE.md` records the correct intent verbatim — *"kept out of anything shared for security — correct call, don't put them in a zip or a chat"* — and that is exactly what happened anyway. Git itself is clean: `.gitignore:2` has `.env`, `git check-ignore` confirms it's ignored, `git ls-files` confirms it was never tracked, and `git log --all -- .env` is empty. The leak is purely the distribution channel.

**Rotate both keys.** The NVIDIA key is metered spend and the ElevenLabs key is character credits — the two things §7 of the plan lists as blocking Stages 6 and 8. Then add a zip-time guard (e.g. `zip -x '.env'` in whatever Makefile target builds the handoff, or `git archive HEAD` instead of zipping the working tree, which would have excluded it automatically).

`.env.example` is correctly placeholder-only (both keys empty, `JWT_SECRET=change-me-…`), and `config.py` refuses to boot in production on that placeholder (`check_production_safety` → `SystemExit(78)`). So the committed surface is right; only the zip is wrong.

## Finding 2 — HIGH: the committed gate threshold is the pre-retrain value, and it breaks the cascade claim on a clean machine

| Source | `VACUITY_GATE_THRESHOLD` |
|---|---|
| `core/config.py:180` (code default) | **0.225** |
| `.env.example:102` (what `make init` copies) | **0.225** |
| `.env` (your local machine, in the zip) | **0.625** |

`STATE.md` records that after the Stage 7 residual-connection retrain, `make tune-gate` measured **0.625**, and that at 0.225 the measured escalation rate was **82.76%** (`48/58`) — versus **10.34%** at 0.625. It also records that this exact staleness masqueraded as 28 test failures across three rounds.

Brief §16 requires escalation between 8% and 20%, and §16's last line requires `docker compose up` to work *on a machine that is not ours*. A judge cloning the repo gets 0.225 → ~83% escalation → the Nemotron track's entire thesis ("we only spend an LLM where our classical model admits ignorance") inverts on stage, and `GET /routing/summary` reports it honestly, in public.

The `.env` that makes it work is the one file that cannot ship. Fix both committed values, not just your local one.

Related, same file: the comment above the field still reads *"The brief's 0.45 escalated nothing on this corpus; 0.20 puts the cascade's LLM call rate at ~10%"* — three numbers (0.45, 0.20, 0.225) none of which is the tuned 0.625. Worth correcting so the next reader doesn't re-derive the wrong value from the prose.

## Finding 3 — LOW: `scoped_query()` doesn't exist under that name

Brief §13 and plan §4 Stage 0 both name `scoped_query()` in `api/deps.py` as the multi-tenancy defense. `grep -rn "scoped_query"` returns **nothing**.

The capability is there and is better than what was specified — a `Scope` class (`api/deps.py:185`) bundling `.query(model)` (pre-filtered on `org_id`), `.child_query(model, parent, cond)` for tables without their own `org_id`, `.get_or_404()` with 404-not-403 semantics, plus `.can()`/`.require()`. This is a naming deviation, not a gap, but the contract document names the symbol, so either rename or note the deviation in the writeup.

**I verified the underlying guarantee holds.** I wrote an AST/brace-matching audit over every `select(...)` chain in `api/v1/*.py` and `workers/pipeline.py`, resolving each against the 7 org-scoped models (`User`, `Document`, `Entity`, `Insight`, `CalibrationSnapshot`, `RoutingEvalCase`, `IngestJob`) and the 5 child models (`Mention`, `AblationRun`, `FragilityTrial`, `RefreshToken`, `NemotronRun`). **Every single query touching tenant data carries an `org_id` predicate.** Zero unscoped reads. The one bug class the brief calls "actually embarrassing" is closed.

## Confirmed conformant

**Route table** — all 28 contract endpoints exist with real decorators, `response_model`, and permission dependencies. Brief §1's router list is complete; plan additions are present and correctly scoped (`POST /evals/fragility/run` owner-only per §1.8; `GET /routing/summary` served as an alias of `GET /routing` per the Stage 6 note; `GET /voice/fallback/briefing.mp3` for the fixture's long-standing `audio_url`). `/insights/stats` is declared before `/{insight_id}` so the literal path wins the match.

**Zero stubs.** No `pending=True`, no `NotImplementedYet`, no `NotImplementedError` anywhere in `api/`, `ml/`, or `workers/`. `BUILD_STAGE = 9`. 148 files compile clean.

**Error contract** — all 16 brief §3 codes present with correct statuses (incl. `ACCOUNT_LOCKED`→423, `PAYLOAD_TOO_LARGE`→413, both 503s), plus `INTERNAL_ERROR`. `ACCOUNT_LOCKED` carries `details.locked_until` and `RATE_LIMITED` carries both `details.retry_after_seconds` and a `Retry-After` header — exactly what frontend §4.3 switches on. Five handlers registered including a bare-`Exception` catch-all. `X-Request-ID` echoed on every response, with inbound ids regex-gated (`^[A-Za-z0-9._\-]{1,64}$`) against log injection — a nice touch not in either brief.

**Schema** — 13 tables in both `db/models.py` and the Alembic migration, **zero column drift** between them (I diffed both sides programmatically; the only deltas were ORM `relationship()` attributes, as expected). All five plan §2 deltas landed: `calibration_snapshots.idempotency_key` with a partial unique index, `insights.degraded`, `ingest_jobs.stage_progress` JSONB, `routing_eval_cases.split` (with a `CHECK` constraint on the three legal values) + `gate_bypassed`. Brief §2's index set is verbatim, plus the pg_trgm GIN on `insights.sentence_text` and the pgvector HNSW on `entities.embedding`, with the extensions created both in the migration and in `docker/postgres/init/01-extensions.sql`.

**Auth/security** — argon2id at the brief's exact parameters (`time_cost=3, memory_cost=65536, parallelism=4`); JWT with `aud`+`iss` verified and an explicit `require` list covering `sub/org/exp/iat/jti/iss/aud`; HS-only algorithm validator blocking the asymmetric-key footgun; SHA-256 refresh hashing with `hmac.compare_digest`; absolute-deadline timing padding (`pad_to_target`) rather than a fixed sleep. The RBAC table in `api/deps.py:63` matches brief §4's matrix row for row, and the 8 permission strings match frontend §4.4 exactly. Security headers cover every item in §13, with the CSP correctly conditional on `serve_static or is_production` per plan Stage 10, and HSTS deliberately withheld over plain HTTP so a demo doesn't pin a judge's localhost to HTTPS.

**Response models** — `api/v1/schemas.py` tracks the brief's JSON examples field-for-field, including the subtleties: `fragility: Unit | None` (null ≠ zero), `Paginated[T]` wrapping collections while single resources stay unwrapped, `extra="forbid"` on requests, no `dict[str, Any]` on a public input, and the documented `degraded` addition at the sibling level frontend §4.3 needs for its "reviewed classically" marker.

**Feed contract** — all 11 brief §6 filter params implemented and index-aligned, keyset (not OFFSET) pagination so live-arriving insights can't duplicate a page mid-demo, `q` escaped against literal `%`/`_`, and `fragility` coalesced for stable ordering with untested insights sorting to the bottom.

---

## Not yet verified — what I'd continue with

I got through roughly the first half of the sweep. Still open: the ablation engine's `load_bearing`/template path and the brief §16 `|Δconf| > 0.10` criterion (which `STATE.md` already documents as failing at n=1 and needing a demo-insight decision from you); the fuzzer's five families and the `test_fuzzer_writes_no_documents` invariant; the Nemotron client's timeout/retry/injection-hardening detail; the voice range-request responder and signed-URL path validation; `GET /evals/*` aggregation math; recalibration idempotency; the 24 fixtures vs. live models; `docker-compose.prod.yml` and the `make prod` single-origin build; and the remaining brief §16 line items including the `torch-geometric` removal that plan §1.2 assigned to Stage 10 cleanup.

Two decisions from `STATE.md` are still awaiting you and neither is mine to make: **which insight the demo script points at** (the pinned Meridian one isn't load-bearing at any of its 24 edges, confirmed twice; `7469f821-…` clears the bar at n=4), and **how to report `nemotron_on_everything_accuracy: 0.1`** (4/40 real decisions — a genuine measurement, well below classical's 42%, and worth reading the actual rationale triples before writing it up either way).


Continuing from the ablation engine.Now the Stage 10 cleanup items specifically.## Stage 10 — Backend verification, part 2 (continued sweep)

Picking up from the ablation engine. Four new findings, three of them blockers for the demo-day requirements.

---

## Finding 4 — HIGH: `SERVE_STATIC` is read but never acted on, so `make prod` serves no app

`docker-compose.prod.yml` does everything right on its side: builds the frontend to a volume, mounts it read-only at `/app/static` in the backend container, sets `SERVE_STATIC=true` and `STATIC_DIR=/app/static`, publishes only the backend on `:8000`, and gives the frontend service `ports: []`. The header comment states the intent plainly — *"served by the backend container, so the whole thing is one origin on port 8000. This is what we hand a judge."*

But the backend never mounts it. `grep -rn "StaticFiles|serve_static|static_dir|mount"` across `api/` and `core/` returns exactly one functional use: `api/middleware.py:101`, where `serve_static` is used only to pick the strict CSP. `api/main.py`'s `create_app()` includes `api_router` and registers `/health`; there is no `app.mount(...)`, no `StaticFiles`, and no SPA catch-all route. `settings.static_dir` is never read by anything.

Net effect: `make prod` brings up Postgres and a working API, and `http://localhost:8000/` returns a 404 envelope. This fails three explicit requirements:

- Brief §16: *"`docker compose up` on a clean machine brings up API + Postgres, seeds a scenario, and **serves the frontend build**"*
- Plan §4 Stage 10: *"`make prod` single-origin build serving the frontend bundle"*
- Frontend §19: *"Production build served from the backend container works end to end"*

The dev path is unaffected — `docker-compose.yml` runs Vite on `:5173` separately. This is purely the single-origin handoff build, and the fix is in the backend app factory (a `StaticFiles` mount plus an SPA fallback that doesn't shadow `/api` or `/health`), so it needs no frontend changes.

## Finding 5 — HIGH: `make prod` also can't boot, for two independent config reasons

Stacked on top of Finding 4, and worth separating because fixing the mount alone won't make the drill pass.

`docker-compose.prod.yml` sets `ENVIRONMENT: production` in the service `environment:` block. Process env beats the `.env` file — the operational gotcha `STATE.md` already records twice. That switches on `check_production_safety()`, which on a clean `make init` (which copies `.env.example` verbatim) finds:

| Check | `.env.example` value | Result |
|---|---|---|
| `JWT_SECRET` is not the placeholder | `change-me-openssl-rand-hex-32` | fatal |
| `JWT_SECRET` ≥ 32 chars | 29 chars | fatal |
| `REFRESH_COOKIE_SECURE` is true | `false` | fatal |

So `make prod` exits 78 with `FATAL: refusing to start with an unsafe configuration`. The guardrail is doing its job correctly — the problem is that the documented Stage 10 drill runs straight into it.

There's a second-order trap behind it: setting `REFRESH_COOKIE_SECURE=true` to satisfy the guard means the `cs_refresh` cookie won't be sent over `http://localhost:8000`, so silent refresh (frontend §19: *"leave the app idle past 15 minutes, come back, no re-login"*) breaks on the demo machine. Production-correct and demo-correct genuinely conflict here.

The cheapest resolution keeps both honest: drop `ENVIRONMENT: production` from the prod overlay and let `SERVE_STATIC=true` carry the strict CSP, which `middleware.py:101` already handles via `serve_static or is_production`. That gives you the hardened CSP and the single origin without claiming a production posture over plain HTTP. Whichever way you go, generate a real `JWT_SECRET` into `.env.example`'s instructions and note the cookie tradeoff in the writeup rather than discovering it at hour 23.

## Finding 6 — HIGH: the prerecorded voice fallback does not exist

`backend/data/voice/` **does not exist**. No `fallback_briefing.mp3`, no `fallback_briefing.json`. `data/fixtures/audio/` holds only `.gitkeep` and a README.

Both fallback routes handle the absence correctly — `fallback_briefing_audio` checks `path.exists()` and raises `VoiceUnavailable`, and `_load_fallback_payload` does the same — so the code is right and the artifact is simply missing. `scripts/record_fallback.py` and `make record-fallback` exist but have never been run, because it takes a real ElevenLabs call.

The consequence is that the degraded voice path is currently a dead end: `POST /voice/briefing` fails on bad wifi → frontend automatically fetches `GET /voice/fallback/briefing` per §12.3 → that also returns 503. The one mitigation for the most likely on-stage failure is absent. Requirements this misses:

- Mission §6 Fallback plan: *"pre-record the briefing audio and the interrogation exchange as local files. Room wifi will be bad."*
- Mission §8 Protect at all costs, item 4: the voice briefing loop
- Brief §11: *"Conference wifi will fail. The demo will not."*
- Brief §16 Voice: *"`VOICE_UNAVAILABLE` path returns the prerecorded fallback and the frontend plays it"*
- Plan §4 Stage 8: *"**Fallback recorded and committed before the happy path is polished**"*

Note the ordering constraint this creates with Finding 1: rotate the ElevenLabs key first, then run `make record-fallback`, then commit the mp3 and the hand-checked transcript JSON. The plan's §1.10 decision (fallback returns full JSON with real `start_ms`/`end_ms`/`insight_id`, not raw audio) is correctly implemented in the route, and the deterministic UUID5 insight ids mean the committed transcript will survive a DB reset — so this is one command and a commit, not a design problem.

## Finding 7 — MEDIUM: `torch-geometric` is still a dependency, and Stage 10 owns removing it

`requirements.txt:27` pins `torch-geometric==2.6.1`. Nothing imports it — the only match repo-wide is a docstring in `ml/relations/gat.py` explaining why the GAT was hand-rolled instead.

Plan §1.2 assigned this to you explicitly: *"`torch-geometric` stays in `requirements.txt` for now (harmless, already pinned) but nothing imports it. I'll drop it in the Stage 10 cleanup."* This is that cleanup, and it hasn't happened.

It isn't cosmetic. The same section's stated benefit was *"one fewer dependency that has to install correctly inside a slim Docker image"* — and Finding 4/5 mean you're about to be rebuilding the prod image under time pressure. Dropping it shortens that build and removes an install-failure mode from the clean-machine requirement. One line.

## Finding 8 — LOW: recalibrate is rate-limited per-user, brief says per-org

Brief §13's table specifies `/calibration/recalibrate` at **5/hour/org**. `core/ratelimit.py:40`'s `rate_limit_key` returns `user:<uuid>` for any authenticated request, so the bucket is per-user. With multiple owners in one org the effective limit is 5/hour × owners.

Low impact (one owner on the demo tenant), and the correct fix is small — key that one route on the JWT's `org` claim rather than `sub`. Worth noting because it's the one line in the §13 limits table that doesn't match, and a Compound-track judge reading the table against the code would find it.

Same function carries a related, already-documented caveat: behind the prod nginx the peer address is the proxy, so every unauthenticated caller shares one `ip:` bucket. The code says so in a comment and deliberately doesn't trust `X-Forwarded-For` (right call — trusting it would let a caller mint a fresh bucket per request). Means `/auth/login` at 10/min/IP is shared across everyone behind the proxy during a demo. Fine at demo scale, worth knowing if judges are clicking around.

---

## Confirmed conformant (part 2)

**Ablation engine** — `load_bearing = abs(delta) > settings.ablation_load_bearing_delta or routing_before != routing_after`, exactly brief §8's rule with the threshold in config rather than inline. Both `zero` and `uniform` modes reach the model through plan §1.3's `RelationModel` protocol, so the endpoint genuinely doesn't know which extractor is behind it. A named edge not present in the sentence graph raises `VALIDATION_FAILED` rather than silently ablating nothing and reporting "not load-bearing" — which the comment correctly calls the most misleading possible answer. Everything is rebuilt from `documents.raw_text` through the same parse and tagger rather than cached, so the thing being ablated is provably the thing that produced the citation. `AblationRun` rows persist all ten brief §2 columns plus mode/relation/interpretation/latency; `db.flush()` in the engine, `scope.db.commit()` in the handler at `ablation.py:87`.

**Interpretation is template-only** — `ml/ablation/templates.py` selects on exactly three facts (delta sign, magnitude bucket, routing flip), and the negative result has its own pre-written wording. `tests/test_no_generation.py` enforces this at AST level, not by grep: it blocks generative SDK imports on the explanation paths, blocks raw HTTP (`httpx`/`requests`/`aiohttp`/`urllib.request`/`http.client`) with a file-level carve-out for exactly `ml/voice/tts.py` and `ml/voice/stt.py`, and a further test asserts that carve-out never grows to a third file. Stronger than brief §16's "grep returns nothing".

**Rate-limit regression closed** — all eight `@limiter.limit` routes now declare `response: Response`. The omission that bit three separate times (ablation, both voice routes, calibration) is gone across the whole API.

**Nemotron (§14)** — 6s timeout, 2 retries, full-jitter backoff, non-retryable on 4xx; malformed JSON is an upstream failure identical to a timeout; `asyncio.Semaphore(6)` per plan §1.9; `force_fail` checked above the cache lookup (the round-6 fix, so the Stage 10 drill can't pass on a warm cache); every call logged with prompt SHA, decision, rationale, latency, and both token counts, failures included. Injection hardening is all four layers: one citation sentence only, explicit data-not-instruction delimiters, control chars stripped and capped at 400 chars, output a three-value `Literal`. `rationale` is bounded at 600 chars and never parsed as a control signal.

**Fuzzer (§15)** — exactly five families in `FAMILIES`, synonym at `SUBSTITUTION_RATE = 0.15`, punctuation at `NOISE_RATE = 0.10`, both matching the brief's percentages. `fragility` is a weighted mean with flip weighted highest, then relation loss, then |Δconf|. Plan §3's isolation invariant is tested twice — `test_the_fuzzer_writes_no_documents` and `test_stored_documents_are_byte_identical_after_a_run`.

**Offsets** — `tests/test_offsets.py` parametrizes across all seed scenarios for gold mentions, gold citations, every token, sentence coverage, and predicted mentions, plus `test_no_offset_critical_module_searches_text_for_a_span` (enforcing plan §3's "never reconstructed by searching for a substring") and a self-test proving the source check would catch a violation.

**Voice transport** — a real hand-written 206 responder with `Accept-Ranges`, `Content-Range`, and 416 on an unsatisfiable range, chunked at 1 MiB rather than `FileResponse`'s read-everything. `file_id` typed as `uuid.UUID` at the signature, so traversal is rejected by parsing.

**Calibration** — `Idempotency-Key` replay returns the stored snapshot without refitting, backed by the partial unique index; baseline measured at the org's live temperature, not a hardcoded 1.0; the `fitted == current` no-op path reuses the before-series instead of reporting ~4e-9 of float noise as a calibration result; the tail-bias caveat ships inside the response's own `interpretation` rather than depending on recall at hour 23. `scope.db.commit()` present (the round-2 bug stays fixed).

**Evals** — per-class precision/recall/F1/support and macro-F1 computed from the confusion matrix with correct zero-denominator guards; three cascade arms read from `routing_eval_cases.split` (`eval` / `classical_only` / `nemotron_all`), which is what plan §2's delta 5 was added for; `documented_failures` sorts hand-written notes ahead of generated ones and is documented as never shipping empty.

**Fixtures** — all 19 brief §12 fixtures plus all 5 error fixtures, plus 7 extras (24 required, 31 present, matching `STATE.md`). `make fixtures-check` fails CI on staleness.

**Secrets in the repo** — a prefix grep for `nvapi-`, `sk_`, `sk-`, `AKIA`, `ghp_` across every **git-tracked** file returns nothing. Brief §16's "no secret in any committed file" passes. The exposure in Finding 1 is the zip only.

**Checkpoints** — committed per plan §1.7 with the deliberate `!backend/ml/checkpoints/*.pt` un-ignore, all three tracked in git, total ~4.2 MB against a 30 MB budget. The intent SVM has no checkpoint by design (fits in milliseconds from 120 strings) — a sensible departure from §1.7's budget line. `scripts/bootstrap.py`, §1.7's belt-and-braces retrain-on-missing-checkpoints fallback, does **not** exist — acceptable since the primary path (committed weights) works, but the plan promised both.

**Docker** — CPU-only torch wheels, `en_core_web_sm` and WordNet+omw baked into **both** dev and prod stages with `NLTK_DATA` set (plan Stage 5's "avoidable disaster" averted), non-root `app` user with `--chown` in a single layer, healthcheck present.

**Scenarios and uploads** — all four scenarios at the specified doc counts (34 / 28 / 120 / 400), `train_corpus` absent from the `Scenario` literal so it can never be served through the API. All four media types from §5 allowed, with the allowlist duplicated in `extract.py` behind a sync comment.

---

## What's left unverified

Static analysis can't reach: actual ECE reduction and the <4s target, ablation p95 <300ms, the 5-minute fuzz budget, the `<30s` ingest, tagger F1, WS behaviour under a dropped socket, or `docker compose down -v && make init && make seed` timing. Those are all `make test-be` plus the drill list in plan §4 Stage 10.

## Recommended order

1. **Rotate both API keys** (Finding 1) — everything else can wait behind this.
2. **`VACUITY_GATE_THRESHOLD` → 0.625** in `core/config.py` *and* `.env.example` (Finding 2). Single highest-value change: it's the difference between an 83% and a 10% escalation rate on a judge's clone.
3. **`make record-fallback`** with the new key, commit the mp3 + transcript (Finding 6).
4. **Mount the static bundle** and resolve the prod-env conflict (Findings 4, 5) — then actually run `make prod` once.
5. Drop `torch-geometric` (7), re-key the recalibrate limit on `org` (8).
6. Then the two decisions still sitting with you from `STATE.md`: which insight the demo script clicks, and how to report `nemotron_on_everything_accuracy: 0.1`.

Findings 2, 4, 5, and 6 all land on the same line of brief §16 — *"`docker compose up` works on a machine that is not ours"* — which is the one definition-of-done item that a judge tests by accident rather than on purpose.