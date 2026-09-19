# Contract notes — backend to frontend, hour 3

The API contract is frozen as of Stage 1. Everything below is now safe to
generate types from and build against. If something here needs to change, it
comes through this file with a note, not silently.

## Get running in two minutes

```bash
make init                       # builds containers, installs deps
cd backend && python -m scripts.gen_fixtures
MOCK_MODE=1 make dev            # API on :8000, no database needed
```

- OpenAPI schema: `http://localhost:8000/openapi.json`
- Interactive docs: `http://localhost:8000/docs`
- Generate your types from the schema, not from these notes.

In `MOCK_MODE=1` every endpoint returns a realistic fixture after an 80–400 ms
delay. **You do not need Postgres, model weights, or either upstream API key.**

## Auth works for real in mock mode

This is the one thing worth reading twice. Mock login returns a **genuinely
signed JWT**, not a canned string:

```
POST /api/v1/auth/login   { "email": "ops@meridian.example", "password": "anything" }
```

The token it returns is validated by the real dependency on every subsequent
request. So:

- Your `Authorization: Bearer` header plumbing is exercised for real.
- `GET /api/v1/auth/me` returns real `permissions`, and unauthenticated
  requests get a real 401. Mock mode is **not** an auth bypass.
- The refresh cookie is HttpOnly + SameSite=Strict + pathed to
  `/api/v1/auth`. You cannot read it and should not try. Call
  `POST /api/v1/auth/refresh` and let the browser carry it.

Gate UI affordances on the `permissions` strings from `/auth/me`, never on
`role`. Roles may change shape; the permission strings are the stable contract.

## Deltas from 01-BACKEND-BRIEF.md

Four changes. All additive except the last, which resolves a genuine
contradiction between the two briefs.

1. **`InsightOut.degraded: boolean`** — new field. True when the cascade gated
   an insight for escalation but the Nemotron upstream hard-failed, so the
   classical decision stands. Render a "reviewed classically" marker rather
   than letting it look like a normal result.

2. **`BriefingResponse.is_fallback: boolean`** — new field. See point 4.

3. **`POST /api/v1/evals/fragility/run`** — new endpoint, owner-only,
   backend-only. Fuzzing 61 insights × 5 perturbations is 305 full pipeline
   passes and cannot live inside a 30-second ingest. `make fuzz` calls it once
   before the demo. **You never call this.** `GET /evals/fragility` is
   unchanged.

4. **`GET /api/v1/voice/fallback/briefing` returns JSON, not audio.** Backend
   brief §11 said "returns a pre-recorded briefing"; frontend brief §12.3
   fetches and plays it. Returning raw audio would have killed the
   transcript-sync highlight exactly when it matters most — on bad conference
   wifi. It now returns the **same shape as a live briefing**, transcript
   segments and `insight_id` mappings included, with `is_fallback: true` so you
   can show your honest "playing recorded briefing" note. Degraded mode looks
   identical to live mode.

## Things that will bite you if you assume otherwise

**`raw_text` is byte-exact and load-bearing.** Every `char_start`/`char_end` in
the system indexes into the exact string `GET /documents/{id}` returns. Do not
trim it, normalize whitespace, or pass it through a markdown renderer. Slice it
and render into `textContent`, never `innerHTML`. `documents.detail.json` is a
real document from the real corpus, so if your highlights line up against the
fixture they will line up in production.

**Cycles are computed server-side.** `GraphResponse.cycles` comes from Tarjan
SCC over the ownership subgraph. Do not attempt client-side cycle detection —
render what is in the array. Also honour `truncated`: a missing node in a fraud
ring is a worse failure than a "showing 300 of 412" label.

**`attention` and `tokens` are index-parallel.** An edge's `src_idx`/`dst_idx`
point into `tokens`. Render the ablation panel by index.

**`trust.fragility` is nullable, and null is not zero.** Null means the fuzzer
has not run against that insight yet. Zero would claim it was tested and found
robust.

**Pagination is keyset, not offset.** Pass `pagination.next_cursor` back as
`cursor`. New insights arrive during the demo, so an offset-paginated second
page would skip or duplicate rows on stage.

**`/insights` sort values** are `created_at`, `confidence`, `vacuity`,
`fragility`, optionally `-` prefixed. Default `-created_at`.

## Job progress

Open `ws://localhost:8000/api/v1/ws/jobs/{job_id}?token=<access_jwt>`. It
pushes a `JobOut` on every state transition and closes 1000 when done. In mock
mode it streams `queued → tagging → relating → done` with realistic delays, so
build your progress bar against that.

Fall back to polling `GET /ingest/jobs/{job_id}` every 2 s if the socket has
not opened within 3 s. That endpoint is a single indexed row read and is safe
to poll.

The token is a query parameter because browsers cannot set headers on a
WebSocket handshake. Bad token closes with 1008.

## Errors

Every failure is the same envelope:

```json
{ "error": { "code": "...", "message": "...", "status": 401,
             "request_id": "01JBQ...", "details": {} } }
```

Branch on `code`, never on `message`. The five you must handle specifically are
in `backend/data/fixtures/errors/`:

| code | status | what to do |
|---|---|---|
| `TOKEN_EXPIRED` | 401 | call `/auth/refresh`, retry once |
| `REFRESH_REUSED` | 401 | **hard logout.** The token family is revoked; do not retry |
| `FORBIDDEN` | 403 | hide the affordance; `details.missing` lists the permission |
| `RATE_LIMITED` | 429 | back off by `details.retry_after_seconds` |
| `NEMOTRON_UNAVAILABLE` | 503 | the classical result still stands — show `degraded` |

Set `MOCK_ERROR_RATE=0.05` to have mock mode inject these at random. They are
raised through the real handlers, so an injected error is byte-identical to a
real one. **Please run with this on for a while** — it is the cheapest way to
find the error states we would otherwise discover on stage.

Always surface `request_id` somewhere copyable. It is how we correlate your bug
report to a server log line.

## Fixture shapes worth knowing

- `insights.list.json` — 25 of 61 insights, `has_more: true`
- `graph.full.json` — **9 party nodes, 49 edges, 1 three-hop ownership cycle**.
  The brief's illustrative "42 nodes" is not what you get: a readable graph
  with one unmistakable loop sells better on stage than a hairball. For canvas
  performance testing, hit the live endpoint with the `invoice_flood` scenario
  (120 docs, 302 insights) at Stage 4.
- `evals.fragility.json` — 61 scatter points, Spearman 0.76, 4-row quartile
  table. Build the trend line off `scatter`; the headline number is
  `correlation.spearman`.
- `documents.detail.json` — document `INV-4471 Meridian Supply`, which contains
  both the demo sentence and the planted failure case. Good citation test case.

## Seeded ids are stable

Every id in the seeded scenario is a deterministic UUID5. `make nuke && make
seed` reproduces the same ids on any machine. You can safely hardcode one in a
dev route while building. The demo insight is the one whose citation reads
*"Payment of $48,200 was routed through Advent Holdings on behalf of Meridian
Supply LLC."*

## When real endpoints land

Stage ownership, so you know when mock mode stops being necessary per page:

| endpoints | stage | rough hour |
|---|---|---|
| auth | 1 | done |
| documents, ingest, ws | 2 | 6 |
| insights, graph | 4 | 12 |
| evals/fragility | 5 | 15 |
| routing, evals/routing | 6 | 18 |
| ablation | 7 | 20 |
| voice | 8 | 22 |
| calibration | 9 | 23 |

Until a stage lands, its endpoints return **501 with
`details.stage`** outside mock mode. That is deliberate — a pending endpoint
serving plausible-looking fixture data in live mode is how a demo ends up
showing numbers nobody computed. Stay in `MOCK_MODE=1` until the stage you need
is announced.
