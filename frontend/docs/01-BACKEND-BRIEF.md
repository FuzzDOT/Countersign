# COUNTERSIGN — Backend Brief

**Owner: Faaz · Stack: Python 3.11 / FastAPI / PostgreSQL 16 + pgvector / PyTorch / Docker**
**This document is the contract. If the frontend and backend disagree, this file wins.**

---

## 1. Service topology

Single FastAPI monolith with internal module boundaries, not microservices. At 24 hours, service boundaries cost more than they buy. Each pipeline stage gets its own router so any stage can be demoed independently if another breaks.

```
countersign/
├── api/
│   ├── v1/
│   │   ├── auth.py            # register, login, refresh, logout, me
│   │   ├── documents.py       # upload, list, fetch raw text
│   │   ├── ingest.py          # job submission, job status
│   │   ├── insights.py        # feed, detail, citation
│   │   ├── graph.py           # nodes, edges, neighborhood
│   │   ├── ablation.py        # counterfactual edge zeroing
│   │   ├── routing.py         # cascade decisions, Nemotron audit log
│   │   ├── evals.py           # fragility, routing, calibration metrics
│   │   ├── calibration.py     # live recalibration trigger
│   │   ├── voice.py           # briefing synth, spoken Q&A
│   │   └── ws.py              # job progress websocket
│   ├── deps.py                # auth deps, RBAC guards, db session
│   └── errors.py              # exception handlers, error envelope
├── core/
│   ├── config.py              # pydantic-settings, env only, no literals
│   ├── security.py            # argon2 hashing, JWT mint/verify
│   └── ratelimit.py           # slowapi limiter config
├── ml/
│   ├── tagger/                # BiLSTM-CRF entity tagger
│   ├── parser/                # dependency parse wrapper (spaCy)
│   ├── relations/             # GAT relation extractor
│   ├── evidential/            # Dirichlet head, vacuity, temp scaling
│   ├── fuzzer/                # adversarial perturbation suite
│   ├── cascade/              # uncertainty gate + Nemotron client
│   └── ablation/              # attention edge masking + re-inference
├── data/
│   ├── synth/                 # synthetic document generators
│   └── fixtures/              # mock API payloads for frontend
├── db/
│   ├── models.py              # SQLAlchemy 2.0 declarative
│   ├── session.py
│   └── migrations/            # alembic
├── workers/
│   └── pipeline.py            # background ingest orchestration
├── tests/
├── docker-compose.yml
└── Dockerfile
```

---

## 2. Data model

```sql
-- ─── identity ─────────────────────────────────────────────────────────────
CREATE TABLE organizations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE user_role AS ENUM ('owner', 'analyst', 'viewer');

CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  email           CITEXT NOT NULL UNIQUE,
  password_hash   TEXT NOT NULL,              -- argon2id
  role            user_role NOT NULL DEFAULT 'viewer',
  is_active       BOOLEAN NOT NULL DEFAULT true,
  failed_logins   SMALLINT NOT NULL DEFAULT 0,
  locked_until    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE refresh_tokens (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  TEXT NOT NULL,                  -- sha256 of opaque token
  family_id   UUID NOT NULL,                  -- rotation family for reuse detection
  expires_at  TIMESTAMPTZ NOT NULL,
  revoked_at  TIMESTAMPTZ,
  user_agent  TEXT,
  ip          INET
);
CREATE INDEX ON refresh_tokens (user_id, revoked_at);
CREATE UNIQUE INDEX ON refresh_tokens (token_hash);

-- ─── documents ────────────────────────────────────────────────────────────
CREATE TYPE doc_source AS ENUM ('invoice','email','press_release','rss','gdelt','note','transaction_log');

CREATE TABLE documents (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  source        doc_source NOT NULL,
  title         TEXT NOT NULL,
  raw_text      TEXT NOT NULL,                -- canonical; all offsets index into THIS
  content_sha   CHAR(64) NOT NULL,            -- dedupe
  received_at   TIMESTAMPTZ NOT NULL,
  ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  meta          JSONB NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX ON documents (org_id, content_sha);
CREATE INDEX ON documents (org_id, received_at DESC);

-- ─── extraction ───────────────────────────────────────────────────────────
CREATE TABLE entities (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  canonical     TEXT NOT NULL,                -- resolved surface form
  entity_type   TEXT NOT NULL,                -- ORG | PERSON | MONEY | DATE | ACCOUNT_REF | TRANSACTION_TYPE
  embedding     VECTOR(256),                  -- for coref / dedupe similarity
  first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  mention_count INT NOT NULL DEFAULT 0
);
CREATE INDEX ON entities (org_id, entity_type);
CREATE INDEX ON entities USING hnsw (embedding vector_cosine_ops);

CREATE TABLE mentions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  surface       TEXT NOT NULL,
  char_start    INT NOT NULL,                 -- offset into documents.raw_text
  char_end      INT NOT NULL,
  tagger_conf   REAL NOT NULL
);
CREATE INDEX ON mentions (document_id, char_start);

CREATE TYPE routing_bucket AS ENUM ('auto_file','flag_for_review','escalate_now');
CREATE TYPE resolver AS ENUM ('classical','nemotron');

CREATE TABLE insights (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  subject_id      UUID NOT NULL REFERENCES entities(id),
  object_id       UUID NOT NULL REFERENCES entities(id),
  relation        TEXT NOT NULL,              -- WIRED_FUNDS_TO | OWNED_BY | INVOICED | SHARES_ADDRESS_WITH | SIGNATORY_OF
  -- citation (byte-exact, non-negotiable)
  char_start      INT NOT NULL,
  char_end        INT NOT NULL,
  sentence_text   TEXT NOT NULL,              -- denormalized for fast feed render
  -- trust layer
  confidence      REAL NOT NULL,              -- Dirichlet mean of argmax class, [0,1]
  vacuity         REAL NOT NULL,              -- epistemic uncertainty, [0,1]
  dissonance      REAL NOT NULL,              -- aleatoric conflict, [0,1]
  fragility       REAL,                       -- measured adversarial instability, [0,1], null until fuzzed
  -- cascade
  routing         routing_bucket NOT NULL,
  resolved_by     resolver NOT NULL,
  nemotron_run_id UUID REFERENCES nemotron_runs(id),
  attention       JSONB NOT NULL DEFAULT '[]',-- [{edge_id, src_token, dst_token, weight}]
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON insights (org_id, routing, created_at DESC);
CREATE INDEX ON insights (org_id, vacuity DESC);
CREATE INDEX ON insights (document_id);
CREATE INDEX ON insights (subject_id);
CREATE INDEX ON insights (object_id);

-- ─── cascade audit ────────────────────────────────────────────────────────
CREATE TABLE nemotron_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  insight_id      UUID,
  prompt_sha      CHAR(64) NOT NULL,
  decision        routing_bucket NOT NULL,
  rationale       TEXT NOT NULL,
  latency_ms      INT NOT NULL,
  input_tokens    INT,
  output_tokens   INT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── ablation ─────────────────────────────────────────────────────────────
CREATE TABLE ablation_runs (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  insight_id          UUID NOT NULL REFERENCES insights(id) ON DELETE CASCADE,
  masked_edges        JSONB NOT NULL,
  confidence_before   REAL NOT NULL,
  confidence_after    REAL NOT NULL,
  vacuity_before      REAL NOT NULL,
  vacuity_after       REAL NOT NULL,
  routing_before      routing_bucket NOT NULL,
  routing_after       routing_bucket NOT NULL,
  load_bearing        BOOLEAN NOT NULL,       -- |delta_conf| > tau OR routing changed
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── evals ────────────────────────────────────────────────────────────────
CREATE TABLE fragility_trials (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  insight_id      UUID NOT NULL REFERENCES insights(id) ON DELETE CASCADE,
  perturbation    TEXT NOT NULL,              -- synonym | rename | boilerplate | reorder | punctuation
  label_flipped   BOOLEAN NOT NULL,
  conf_delta      REAL NOT NULL,
  relation_lost   BOOLEAN NOT NULL
);

CREATE TABLE calibration_snapshots (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  label         TEXT NOT NULL,                -- 'baseline' | 'post_recalibration'
  temperature   REAL NOT NULL,
  ece           REAL NOT NULL,
  mce           REAL NOT NULL,
  brier         REAL NOT NULL,
  bins          JSONB NOT NULL,               -- [{bin_lo,bin_hi,avg_conf,accuracy,count}]
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE routing_eval_cases (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  insight_id      UUID REFERENCES insights(id) ON DELETE SET NULL,
  ground_truth    routing_bucket NOT NULL,
  predicted       routing_bucket NOT NULL,
  resolved_by     resolver NOT NULL,
  is_failure      BOOLEAN NOT NULL,
  failure_note    TEXT
);

-- ─── ingest jobs ──────────────────────────────────────────────────────────
CREATE TYPE job_state AS ENUM ('queued','tagging','parsing','relating','scoring','routing','done','failed');

CREATE TABLE ingest_jobs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  state           job_state NOT NULL DEFAULT 'queued',
  doc_ids         UUID[] NOT NULL,
  docs_total      INT NOT NULL,
  docs_done       INT NOT NULL DEFAULT 0,
  insights_found  INT NOT NULL DEFAULT 0,
  error           TEXT,
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ
);
```

**Index rationale (matched to actual access patterns, not theoretical scale):**
- `insights (org_id, routing, created_at DESC)` — the feed's default query is "show me this org's flagged items newest first." This is the hot path.
- `insights (org_id, vacuity DESC)` — the cascade gate scans the high-vacuity tail. Descending order matters.
- `mentions (document_id, char_start)` — the citation reader renders highlight spans in document order.
- HNSW on `entities.embedding` — entity dedupe/coref does k-NN lookup per new mention. At our volume (thousands of entities) this is borderline unnecessary, but it's one line and it makes coref not O(n).
- No index on `nemotron_runs` beyond the PK — it's append-only audit, read once per eval page load.

---

## 3. API conventions

**Base path:** `/api/v1`
**Content type:** `application/json` except document upload (`multipart/form-data`) and audio (`audio/mpeg`).
**Auth:** `Authorization: Bearer <access_jwt>` on everything except `/auth/register`, `/auth/login`, `/auth/refresh`, `/health`.

### Success envelope

Collections are paginated and always wrapped:
```json
{
  "data": [ ... ],
  "pagination": { "cursor": "eyJpZCI6...", "next_cursor": "eyJpZCI6...", "has_more": true, "limit": 25 }
}
```
Single resources return the object at top level, unwrapped.

### Error envelope — identical shape on every failure, no exceptions

```json
{
  "error": {
    "code": "INSIGHT_NOT_FOUND",
    "message": "No insight with that id in this organization.",
    "status": 404,
    "request_id": "01JBQ7X3K9M2N4P6R8T0V2W4Y6",
    "details": { "insight_id": "..." }
  }
}
```

**Canonical error codes** (frontend should switch on `code`, never on `message`):

| Code | Status | When |
|------|--------|------|
| `VALIDATION_FAILED` | 422 | Pydantic rejection; `details.fields` maps field → message |
| `UNAUTHENTICATED` | 401 | Missing/expired/invalid access token |
| `TOKEN_EXPIRED` | 401 | Specifically expired — frontend should attempt refresh |
| `REFRESH_REUSED` | 401 | Token family compromised, all sessions revoked, force re-login |
| `FORBIDDEN` | 403 | Authenticated but RBAC denied |
| `ACCOUNT_LOCKED` | 423 | Too many failed logins; `details.locked_until` |
| `NOT_FOUND` | 404 | Generic resource miss |
| `INSIGHT_NOT_FOUND` | 404 | |
| `DOCUMENT_NOT_FOUND` | 404 | |
| `CONFLICT` | 409 | Duplicate email, duplicate document sha |
| `RATE_LIMITED` | 429 | `details.retry_after_seconds` present |
| `PAYLOAD_TOO_LARGE` | 413 | Doc > 2 MB or batch > 50 docs |
| `UNSUPPORTED_MEDIA` | 415 | Non-text/PDF upload |
| `PIPELINE_FAILED` | 500 | Ingest worker crashed; `details.stage` |
| `NEMOTRON_UNAVAILABLE` | 503 | Upstream LLM down — **frontend must degrade, not break** |
| `VOICE_UNAVAILABLE` | 503 | ElevenLabs down — frontend falls back to text + prerecorded |

`X-Request-ID` is echoed on every response including errors. Frontend should surface it in error toasts for demo debugging.

---

## 4. Auth endpoints

### `POST /api/v1/auth/register`
Request:
```json
{ "email": "ops@meridian.example", "password": "...", "org_name": "Meridian Ops" }
```
Response `201`:
```json
{
  "user": { "id": "uuid", "email": "ops@meridian.example", "role": "owner", "org_id": "uuid" },
  "access_token": "jwt", "token_type": "bearer", "expires_in": 900
}
```
Refresh token is set as an **HttpOnly, Secure, SameSite=Strict cookie** named `cs_refresh`, path `/api/v1/auth`. It is never in the JSON body — the frontend never touches it.

Password policy enforced server-side: ≥12 chars, not in a bundled 10k-common-password list. Hashed with **argon2id** (`time_cost=3, memory_cost=65536, parallelism=4`).

### `POST /api/v1/auth/login`
Same response shape. On failure: increments `failed_logins`; at 5 within 15 min, sets `locked_until = now() + 15 min` and returns `ACCOUNT_LOCKED`. Response timing is constant-padded to ~250ms regardless of whether the email exists (no user enumeration).

### `POST /api/v1/auth/refresh`
No body. Reads `cs_refresh` cookie. **Rotating refresh tokens with reuse detection:** every refresh issues a new token in the same `family_id` and revokes the old. If a revoked token is presented, the entire family is revoked and `REFRESH_REUSED` is returned — frontend must hard-logout and route to login.

### `POST /api/v1/auth/logout`
Revokes the current family, clears the cookie. `204`.

### `GET /api/v1/auth/me`
```json
{ "id": "uuid", "email": "...", "role": "analyst", "org_id": "uuid", "org_name": "Meridian Ops",
  "permissions": ["insights:read","graph:read","ablation:run","voice:use"] }
```
Frontend should gate UI affordances on `permissions`, not on `role` string comparisons — roles may change, the permission list is the contract.

### JWT claims
```json
{ "sub":"user_uuid", "org":"org_uuid", "role":"analyst", "perms":["..."],
  "iat":1758..., "exp":1758..., "jti":"uuid", "iss":"countersign", "aud":"countersign-web" }
```
HS256 with a 32-byte secret from env. 15-minute access lifetime, 7-day refresh.

### RBAC matrix

| Capability | viewer | analyst | owner |
|-----------|--------|---------|-------|
| read insights / graph / citations | ✅ | ✅ | ✅ |
| upload documents, run ingest | ❌ | ✅ | ✅ |
| run ablation | ❌ | ✅ | ✅ |
| use voice briefing | ✅ | ✅ | ✅ |
| trigger live recalibration | ❌ | ❌ | ✅ |
| view eval dashboards | ❌ | ✅ | ✅ |
| manage users | ❌ | ❌ | ✅ |

---

## 5. Documents & ingest

### `POST /api/v1/documents` — `multipart/form-data`
Fields: `files[]` (≤50, ≤2 MB each, `text/plain`, `text/csv`, `application/pdf`, `message/rfc822`), `source` (enum), optional `received_at`.

Response `202`:
```json
{
  "job_id": "uuid",
  "documents": [ { "id":"uuid", "title":"INV-4471 Meridian Supply.pdf", "source":"invoice", "chars": 3812 } ],
  "duplicates_skipped": 1
}
```

### `POST /api/v1/documents/seed`
Loads a named synthetic scenario. **This is the demo button.** Owner/analyst only.
```json
{ "scenario": "meridian_shell_ring" }
```
Available scenarios: `meridian_shell_ring` (the demo path — 34 docs, a 3-hop ownership loop, two timing anomalies, one deliberate red herring), `clean_baseline` (28 docs, no fraud — proves we don't cry wolf), `invoice_flood` (120 docs, volume/latency stress).

### `GET /api/v1/ingest/jobs/{job_id}`
```json
{
  "id":"uuid", "state":"relating", "docs_total":34, "docs_done":19, "insights_found":61,
  "stage_progress": { "tagging":1.0, "parsing":1.0, "relating":0.56, "scoring":0.0, "routing":0.0 },
  "started_at":"2026-09-19T21:04:11Z", "finished_at":null, "error":null
}
```

### `WS /api/v1/ws/jobs/{job_id}?token=<access_jwt>`
Server pushes the same object on every state transition and every 5 documents. Terminal frame has `state: "done"` or `"failed"`, then the server closes with code `1000`.

Frontend: use the websocket for the live progress bar, but **poll the REST endpoint every 2s as a fallback** if the socket fails to open within 3s. Conference wifi will break websockets.

### `GET /api/v1/documents/{id}`
```json
{
  "id":"uuid", "title":"INV-4471 Meridian Supply", "source":"invoice",
  "received_at":"2026-09-14T08:31:00Z",
  "raw_text":"Invoice INV-4471\nMeridian Supply LLC\n...",
  "spans": [
    { "insight_id":"uuid", "char_start":412, "char_end":501, "relation":"WIRED_FUNDS_TO",
      "confidence":0.81, "routing":"escalate_now" }
  ],
  "mentions": [
    { "entity_id":"uuid", "surface":"Meridian Supply LLC", "entity_type":"ORG",
      "char_start":18, "char_end":37, "tagger_conf":0.97 }
  ]
}
```
**`raw_text` is the single source of truth for all offsets.** Frontend must render highlights by slicing this exact string — do not normalize whitespace, do not trim, do not pass it through a markdown renderer. Offsets are Python string indices over the UTF-8-decoded text; JS string indices match for the BMP characters our synthetic data uses.

---

## 6. Insights

### `GET /api/v1/insights`
Query params: `routing` (repeatable), `resolved_by`, `min_confidence`, `max_confidence`, `min_vacuity`, `relation` (repeatable), `entity_id`, `document_id`, `q` (substring over `sentence_text`), `sort` (`created_at|confidence|vacuity|fragility`, prefix `-` for desc, default `-created_at`), `cursor`, `limit` (default 25, max 100).

```json
{
  "data": [
    {
      "id": "0193f2a1-...",
      "relation": "WIRED_FUNDS_TO",
      "subject": { "id":"uuid", "canonical":"Meridian Supply LLC", "entity_type":"ORG" },
      "object":  { "id":"uuid", "canonical":"Advent Holdings",     "entity_type":"ORG" },
      "citation": {
        "document_id":"uuid", "document_title":"INV-4471 Meridian Supply",
        "char_start":412, "char_end":501,
        "sentence_text":"Payment of $48,200 was routed through Advent Holdings on behalf of Meridian Supply LLC."
      },
      "trust": { "confidence":0.81, "vacuity":0.62, "dissonance":0.11, "fragility":0.58 },
      "routing": "escalate_now",
      "resolved_by": "nemotron",
      "nemotron": {
        "run_id":"uuid",
        "rationale":"Third-party payment routing combined with a shared registered address across two counterparties is consistent with layering. Escalating.",
        "latency_ms": 840
      },
      "attention_available": true,
      "created_at":"2026-09-19T21:06:02Z"
    }
  ],
  "pagination": { "next_cursor":"eyJ...","has_more":true,"limit":25 }
}
```

### `GET /api/v1/insights/{id}`
Everything above plus:
```json
{
  "attention": [
    { "edge_id":"e_0","src_token":"routed","dst_token":"Advent","weight":0.41,"src_idx":7,"dst_idx":9 },
    { "edge_id":"e_1","src_token":"behalf","dst_token":"Meridian","weight":0.33,"src_idx":12,"dst_idx":15 }
  ],
  "tokens": ["Payment","of","$48,200","was","routed","through","Advent","Holdings", "..."],
  "graph_neighborhood": { "node_ids": ["uuid","uuid","uuid"], "depth": 2 },
  "ablation_history": [ { "id":"uuid","masked_edges":["e_0"],"confidence_before":0.81,
                          "confidence_after":0.40,"load_bearing":true,
                          "created_at":"2026-09-19T21:41:00Z" } ],
  "fragility_trials": [ { "perturbation":"rename","label_flipped":false,"conf_delta":-0.07,"relation_lost":false } ]
}
```

### `GET /api/v1/insights/stats`
Feed header counters, single query, cheap:
```json
{
  "total": 214,
  "by_routing": { "auto_file":186, "flag_for_review":21, "escalate_now":7 },
  "by_resolver": { "classical":186, "nemotron":28 },
  "mean_confidence": 0.88,
  "high_vacuity_count": 28,
  "documents_ingested": 34
}
```

---

## 7. Graph

### `GET /api/v1/graph`
Params: `entity_type` (repeatable), `relation` (repeatable), `min_confidence`, `routing`, `root_entity_id`, `depth` (1–3, default 2 when root given), `limit_nodes` (default 300).

```json
{
  "nodes": [
    { "id":"uuid","canonical":"Meridian Supply LLC","entity_type":"ORG",
      "mention_count":14,"degree":6,"risk":0.74,
      "flags":["shared_address","ownership_cycle"] }
  ],
  "edges": [
    { "id":"uuid","source":"uuid","target":"uuid","relation":"WIRED_FUNDS_TO",
      "confidence":0.81,"vacuity":0.62,"routing":"escalate_now",
      "insight_ids":["uuid"],"weight":3 }
  ],
  "cycles": [ { "node_ids":["uuid","uuid","uuid"],"length":3,"relation":"OWNED_BY","risk":0.91 } ],
  "truncated": false
}
```

`risk` on a node is a normalized composite of degree centrality, cycle participation, and mean incident-edge routing severity. `cycles` is computed server-side via Tarjan SCC on the `OWNED_BY` subgraph — **the frontend must not try to detect cycles client-side.** It just renders what's in `cycles` with emphasis.

`weight` on an edge = number of distinct insights supporting that relation. Use it for stroke width.

### `GET /api/v1/graph/entities/{id}`
```json
{
  "entity": { "id":"uuid","canonical":"Meridian Supply LLC","entity_type":"ORG",
              "mention_count":14,"risk":0.74,"flags":["shared_address"],
              "first_seen":"2026-09-14T08:31:00Z" },
  "aliases": ["Meridian Supply","Meridian Supply, LLC"],
  "neighbors": [ { "entity":{...},"relation":"WIRED_FUNDS_TO","direction":"out",
                   "confidence":0.81,"insight_ids":["uuid"] } ],
  "documents": [ { "id":"uuid","title":"INV-4471 Meridian Supply","mention_count":3 } ],
  "insight_count": 9
}
```

---

## 8. Ablation — causal explainability

### `POST /api/v1/ablation/insights/{insight_id}`
Analyst+. Rate limited to 30/min/user (each call is a real forward pass).
```json
{ "masked_edges": ["e_0"], "mode": "zero" }
```
`mode`: `zero` (set attention weight to 0 and renormalize) or `uniform` (replace with uniform attention over remaining edges).

Response `200`:
```json
{
  "run_id":"uuid",
  "insight_id":"uuid",
  "masked_edges":["e_0"],
  "before": { "confidence":0.81,"vacuity":0.62,"routing":"escalate_now",
              "relation":"WIRED_FUNDS_TO" },
  "after":  { "confidence":0.40,"vacuity":0.88,"routing":"flag_for_review",
              "relation":"WIRED_FUNDS_TO" },
  "delta":  { "confidence":-0.41,"vacuity":0.26,"routing_changed":true },
  "load_bearing": true,
  "interpretation": "The dependency link between 'routed' and 'Advent' accounts for 41 points of confidence. Removing it downgrades the routing decision, so this edge is causally responsible for the flag.",
  "latency_ms": 118
}
```
`interpretation` is a **template-filled string, not generated text.** Templates live in `ml/ablation/templates.py`. This matters: it's part of the no-hallucination guarantee, and the voice layer reads this field verbatim.

`load_bearing` is true iff `|delta.confidence| > 0.10` OR `routing_changed`.

---

## 9. Routing / cascade

### `GET /api/v1/routing/summary`
```json
{
  "gate": { "vacuity_threshold":0.45, "policy":"vacuity_gate_v2" },
  "volume": { "total_insights":214,"handled_classically":186,"escalated_to_nemotron":28,
              "escalation_rate":0.131,"llm_calls_avoided":186 },
  "latency": { "classical_p50_ms":34,"classical_p95_ms":71,
               "nemotron_p50_ms":812,"nemotron_p95_ms":1640 },
  "agreement": { "nemotron_upheld_classical":19,"nemotron_overrode_classical":9,
                 "override_rate":0.321 },
  "distribution": { "auto_file":186,"flag_for_review":21,"escalate_now":7 }
}
```

### `GET /api/v1/routing/runs`
Paginated Nemotron audit log — every call, its input digest, decision, rationale, latency, token counts. This is the "show your work" table on the eval page.

---

## 10. Evals — the differentiator endpoints

### `GET /api/v1/evals/fragility`
```json
{
  "n_insights": 214,
  "n_trials": 1070,
  "perturbations": ["synonym","rename","boilerplate","reorder","punctuation"],
  "correlation": { "spearman":0.67,"pearson":0.61,"p_value":0.0000031 },
  "scatter": [ { "insight_id":"uuid","vacuity":0.62,"fragility":0.58,
                 "routing":"escalate_now" } ],
  "by_perturbation": [
    { "perturbation":"rename","flip_rate":0.11,"mean_abs_conf_delta":0.14,
      "relation_loss_rate":0.06 },
    { "perturbation":"boilerplate","flip_rate":0.03,"mean_abs_conf_delta":0.05,
      "relation_loss_rate":0.01 }
  ],
  "quartile_table": [
    { "vacuity_quartile":1,"vacuity_range":[0.00,0.18],"mean_fragility":0.09,"flip_rate":0.02 },
    { "vacuity_quartile":4,"vacuity_range":[0.51,0.94],"mean_fragility":0.54,"flip_rate":0.29 }
  ],
  "interpretation": "Insights in the top vacuity quartile flip labels under perturbation 14.5x more often than the bottom quartile. The uncertainty signal is predictive of real fragility, not decorative."
}
```
**This is the single most important response object in the project.** The scatter plot and the quartile table are what a technical judge will look at.

### `GET /api/v1/evals/routing`
```json
{
  "n_cases": 100,
  "confusion_matrix": {
    "labels": ["auto_file","flag_for_review","escalate_now"],
    "matrix": [[61,3,0],[4,19,2],[0,2,9]]
  },
  "per_class": [
    { "bucket":"auto_file","precision":0.938,"recall":0.953,"f1":0.945,"support":64 },
    { "bucket":"flag_for_review","precision":0.792,"recall":0.760,"f1":0.776,"support":25 },
    { "bucket":"escalate_now","precision":0.818,"recall":0.818,"f1":0.818,"support":11 }
  ],
  "macro_f1": 0.846,
  "accuracy": 0.89,
  "cascade_baseline": {
    "classical_only_accuracy": 0.74,
    "nemotron_on_everything_accuracy": 0.91,
    "cascade_accuracy": 0.89,
    "cascade_llm_calls": 28,
    "nemotron_on_everything_llm_calls": 214,
    "interpretation": "The cascade recovers 94% of the accuracy gain of running Nemotron on everything, using 13% of the LLM calls."
  },
  "documented_failures": [
    {
      "case_id":"uuid","insight_id":"uuid",
      "ground_truth":"flag_for_review","predicted":"escalate_now",
      "sentence_text":"Invoice INV-4471 was submitted 14 days ahead of the contracted schedule.",
      "note":"Nemotron over-escalates on timing anomalies when the counterparty has any prior flag, even when the timing has a benign contractual explanation present in an adjacent sentence. The cascade passes only the single citation sentence plus graph context, not neighboring sentences — so the exculpatory context is invisible to it. Fix would be a two-sentence citation window, which we'd do with more time."
    }
  ]
}
```
The `documented_failures[].note` field is written by hand and is worth more to the Nemotron judges than every clean metric above it. **Do not ship this endpoint with an empty failures array.**

### `GET /api/v1/evals/calibration`
```json
{
  "snapshots": [
    { "id":"uuid","label":"baseline","temperature":1.0,"ece":0.094,"mce":0.187,"brier":0.121,
      "bins":[ { "bin_lo":0.0,"bin_hi":0.1,"avg_conf":0.06,"accuracy":0.02,"count":11 } ],
      "created_at":"2026-09-19T20:10:00Z" },
    { "id":"uuid","label":"post_recalibration","temperature":1.37,"ece":0.041,"mce":0.092,
      "brier":0.098,"bins":[ ... ],"created_at":"2026-09-20T09:14:00Z" }
  ],
  "current_snapshot_id":"uuid",
  "hard_negatives_logged": 43
}
```
10 equal-width bins. `bins` drives the reliability diagram: plot `avg_conf` on x, `accuracy` on y, with the y=x diagonal as the perfect-calibration reference and bin `count` as bar opacity or point size.

### `POST /api/v1/calibration/recalibrate`
**Owner only. This is the finale button.** Rate limited to 5/hour.
```json
{ "method": "temperature_scaling" }
```
Response `200` (target latency under 4s — it's a 1-parameter optimization over a few hundred logged cases):
```json
{
  "before": { "temperature":1.0,"ece":0.094,"mce":0.187,"brier":0.121,"bins":[...] },
  "after":  { "temperature":1.37,"ece":0.041,"mce":0.092,"brier":0.098,"bins":[...] },
  "improvement": { "ece_absolute":-0.053,"ece_relative":-0.564 },
  "n_hard_negatives": 43,
  "elapsed_ms": 2180,
  "snapshot_id": "uuid"
}
```
Idempotency: accepts an `Idempotency-Key` header. If the judge double-clicks, the same snapshot comes back instead of a second optimization run.

---

## 11. Voice

### `POST /api/v1/voice/briefing`
```json
{ "scope": "flagged", "max_items": 3, "voice_id": "default" }
```
`scope`: `flagged` (routing ≥ flag_for_review), `escalated` (escalate_now only), `all_new`.

Response `200`:
```json
{
  "briefing_id":"uuid",
  "audio_url":"/api/v1/voice/audio/uuid.mp3",
  "duration_ms": 41200,
  "transcript": [
    { "segment_id":"s0","start_ms":0,"end_ms":3100,
      "text":"Three things need your attention.","insight_id":null },
    { "segment_id":"s1","start_ms":3100,"end_ms":14800,
      "text":"First: Meridian Supply LLC routed a payment of forty-eight thousand two hundred dollars through Advent Holdings. Confidence eighty-one percent. Escalated.",
      "insight_id":"uuid" }
  ],
  "insight_ids":["uuid","uuid","uuid"],
  "generated_at":"2026-09-20T09:02:00Z"
}
```
**Transcript segments carry `insight_id`.** The frontend must use this to highlight the corresponding feed row as the audio plays — that synchronization is the moment that sells the voice track.

Briefing text is assembled from templates over structured insight fields. **No generative model writes this text.** Templates in `ml/voice/briefing_templates.py`.

### `POST /api/v1/voice/ask` — `multipart/form-data`
Fields: `audio` (webm/opus or wav, ≤30s), optional `context_insight_id`.

Response `200`:
```json
{
  "question_id":"uuid",
  "heard":"Why is Meridian flagged?",
  "stt_confidence":0.94,
  "intent":"explain_flag",
  "resolved_insight_id":"uuid",
  "answer_text":"Meridian Supply LLC is flagged because a payment was routed through Advent Holdings on invoice INV-4471. The dependency link between 'routed' and 'Advent' accounts for 41 points of confidence — removing it downgrades the flag, so that connection is what's driving this.",
  "citation": { "document_id":"uuid","document_title":"INV-4471 Meridian Supply",
                "char_start":412,"char_end":501,
                "sentence_text":"Payment of $48,200 was routed through Advent Holdings on behalf of Meridian Supply LLC." },
  "ablation_run_id":"uuid",
  "audio_url":"/api/v1/voice/audio/uuid.mp3",
  "duration_ms": 16400
}
```
Intent classifier (classical — TF-IDF + linear SVM over a small labeled intent set, no LLM) supports: `explain_flag`, `show_source`, `list_flagged`, `entity_summary`, `confidence_query`, `dismiss`, `unknown`. On `unknown`, `answer_text` is a template asking for rephrasing — never a guess.

### `GET /api/v1/voice/audio/{file_id}.mp3`
Returns `audio/mpeg` with `Accept-Ranges: bytes` so the `<audio>` element can seek. Signed short-lived URL; also accepts bearer auth.

### `GET /api/v1/voice/fallback/briefing`
Returns a pre-recorded briefing matching the `meridian_shell_ring` scenario. **Frontend must wire this as the automatic fallback when `/voice/briefing` returns `VOICE_UNAVAILABLE` or times out after 8s.** Conference wifi will fail. The demo will not.

---

## 12. Mock fixtures — unblocking the frontend by hour 3

Shipped at `data/fixtures/` and served by a flag-flipped dev server:

```
fixtures/
├── auth.me.json
├── documents.seed.json
├── ingest.job.queued.json          # plus .tagging / .relating / .done
├── documents.detail.json           # full raw_text + spans, the citation reader's test case
├── insights.list.json              # 25 items, all routing buckets, varied confidence/vacuity
├── insights.detail.json            # with attention array + tokens
├── insights.stats.json
├── graph.full.json                 # 42 nodes, 67 edges, 1 three-node OWNED_BY cycle
├── graph.entity.json
├── ablation.run.json               # load_bearing: true, -0.41 confidence delta
├── routing.summary.json
├── routing.runs.json
├── evals.fragility.json            # 214-point scatter, real-looking spread
├── evals.routing.json              # confusion matrix + one documented failure
├── evals.calibration.json          # baseline + post snapshots, 10 bins each
├── calibration.recalibrate.json
├── voice.briefing.json             # with segment→insight_id mapping
├── voice.ask.json
└── errors/
    ├── 401.token_expired.json
    ├── 403.forbidden.json
    ├── 422.validation.json
    ├── 429.rate_limited.json
    └── 503.nemotron_unavailable.json
```

**Run it:** `MOCK_MODE=1 uvicorn api.main:app --reload --port 8000`. Every endpoint returns its fixture with a randomized 80–400ms delay so the frontend builds real loading states instead of assuming instant responses. `MOCK_ERROR_RATE=0.1` injects random failures from `errors/` so error paths get exercised.

The fixtures are generated from the same Pydantic response models as production, so **if a fixture and the real response disagree, that's a bug in the backend, not the frontend.** Report it and it gets fixed.

---

## 13. Security requirements

Not optional, and several of these are things a Compound-track judge may specifically probe.

**Input handling**
- Every request body is a Pydantic model with explicit types, bounds, and enums. No `dict[str, Any]` on any public endpoint.
- All SQL through SQLAlchemy parameter binding. Zero f-string SQL anywhere — including in the eval aggregation queries, which is where the temptation lives.
- Uploaded filenames sanitized and never used as filesystem paths; stored content is keyed by UUID.
- `raw_text` is returned as-is but the frontend renders it into `textContent`, never `innerHTML`. Documented in the frontend brief as a hard rule.
- PDF parsing runs with a hard timeout and page cap (30 pages) — malformed PDFs are a DoS vector.

**Auth**
- argon2id hashing, per-user salt, no pepper-in-code.
- Rotating refresh tokens with family-based reuse detection (§4).
- Constant-time login response padding to prevent user enumeration.
- Account lockout after 5 failures in 15 min.
- JWTs carry `aud` and `iss` and both are verified. `jti` recorded so a token can be individually revoked.

**Rate limits** (slowapi, per-user where authenticated, per-IP otherwise)
| Endpoint | Limit | Why |
|---|---|---|
| `/auth/login` | 10/min/IP | credential stuffing |
| `/auth/register` | 5/hour/IP | spam orgs |
| `/documents` upload | 20/min/user | pipeline is CPU-bound |
| `/ablation/*` | 30/min/user | each call is a forward pass |
| `/voice/*` | 20/min/user | metered upstream cost |
| `/calibration/recalibrate` | 5/hour/org | expensive + state-mutating |
| everything else | 300/min/user | |

**Secrets** — all from env via pydantic-settings. `ELEVENLABS_API_KEY`, `NEMOTRON_API_KEY`, `JWT_SECRET`, `DATABASE_URL`. `.env` in `.gitignore`, `.env.example` committed with placeholder values. **No API key ever reaches the browser** — all upstream calls are server-side. If the frontend ever needs to call ElevenLabs directly, the answer is no; it calls our endpoint and we proxy.

**Transport / headers** — HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a CSP with no `unsafe-inline` for scripts. CORS allowlist is explicit origins only, `allow_credentials=True`, never `*` (which is incompatible with credentialed requests anyway).

**Multi-tenancy** — every query filters on `org_id` from the JWT, enforced by a `scoped_query()` helper in `api/deps.py` rather than by remembering to add a `.where()` in 40 places. A cross-org read is the one bug class that would actually be embarrassing.

**Data policy** — synthetic and public sources only, enforced at the seed layer and stated in the UI. No real account numbers, credentials, or financial records anywhere in the repo or the demo. Both Compound and Xtract have explicit rules about this.

### Shortcut ledger — what's fine vs. what's real debt

**Fine for a 24h demo, say so openly if asked:**
- In-process background tasks instead of Celery/Redis. At demo volume, `BackgroundTasks` is correct, not lazy.
- Models loaded into the FastAPI process at startup rather than a separate inference service.
- No email verification on register.
- Single Postgres instance, no read replica, no connection pooler beyond SQLAlchemy's.
- Fixture-backed eval numbers refreshed by a script rather than a scheduled job.

**Real tech debt — flag it in the writeup rather than pretending:**
- Refresh-token family revocation is correct but there's no admin UI to see active sessions.
- Entity coreference is embedding-similarity + string heuristics; it will merge two genuinely distinct companies with similar names. Known limitation, worth stating in the failure section.
- The ablation engine re-runs a full forward pass per call instead of caching the graph encoding. Fine at 30/min, would not survive real load.
- Temperature scaling is fit on hard negatives only, which biases toward the tail. Defensible for the demo claim, not a production calibration strategy — and a sharp judge may ask. Have the answer ready.

---

## 14. Nemotron integration specifics

Server-side only, via the NVIDIA API. Client in `ml/cascade/nemotron.py`.

- **Timeout** 6s, **2 retries** with jittered backoff, then hard-fail to the classical decision with `resolved_by: "classical"` and a `degraded: true` marker on the insight. The pipeline must never block on an upstream outage.
- **Structured output enforced.** Prompt requires a single JSON object, no prose, no markdown fences. Response is parsed, fence-stripped defensively, and validated against a Pydantic model. Parse failure counts as an upstream failure and falls back.
- **Input is structured, not raw document text:** relation triple, confidence, vacuity, dissonance, the citation sentence, and a serialized 2-hop graph neighborhood. This keeps the prompt small and makes the failure mode in §10 (`documented_failures`) honest and explicable.
- **Every call is logged** to `nemotron_runs` with a SHA of the prompt, the decision, the rationale, latency, and token counts. This table is what the Beyond the Chatbot judges are actually going to want to look at.
- **Prompt injection is a real concern here** — document text flows into the prompt. Mitigations: the document contribution is limited to one citation sentence, it's wrapped in explicit delimiters with an instruction that the delimited region is data not instruction, and the output is constrained to a 3-value enum so a successful injection can't do anything except pick a wrong bucket. Worth mentioning to judges unprompted; it shows the security thinking the Compound track asks for.

---

## 15. ML component specs

### BiLSTM-CRF entity tagger
- Input: character-CNN + 100d word embeddings (GloVe init, fine-tuned).
- 2-layer BiLSTM, hidden 256/direction, dropout 0.5, CRF decode over BIO tags.
- Tags: `ORG`, `PERSON`, `MONEY`, `DATE`, `ACCOUNT_REF`, `TRANSACTION_TYPE`.
- Training: weak supervision (regex for money/date/account patterns, gazetteer for orgs from the synthetic generator's own name pool) → ~400 auto-labeled sentences, then hand-correct ~150 for a dev set.
- Target: token-level F1 ≥ 0.90 on synthetic dev. This is easier than it sounds because we generate the data; be honest about that in the writeup rather than implying real-world performance.
- **Offset preservation is the critical requirement.** Tokenization must retain `(char_start, char_end)` per token. Assert round-trip equality (`raw_text[start:end] == surface`) in a test and let it fail loudly.

### Dependency parse
spaCy `en_core_web_sm` parse only, no NER (we have our own tagger). Cache parses keyed by sentence SHA — the fuzzer will re-parse thousands of near-identical sentences and this is the difference between the fragility eval taking 3 minutes and 40.

### GAT relation extractor
- Per-sentence graph: nodes = tokens, features = [tagger embedding ‖ entity-type one-hot ‖ POS one-hot ‖ positional]. Edges = dependency arcs, bidirectional with direction flag.
- 3 GAT layers, 4 heads, hidden 128, ELU, dropout 0.3. Attention weights retained per edge per layer (layer-averaged for the UI).
- Read out: concat the two candidate entity node embeddings + graph mean pool → relation classifier.
- Classes: `WIRED_FUNDS_TO`, `OWNED_BY`, `INVOICED`, `SHARES_ADDRESS_WITH`, `SIGNATORY_OF`, `NO_RELATION`.
- **Fallback if this is shaky at hour 9:** rule-based relation extraction over dependency paths (verb-lemma + preposition patterns), keeping the evidential head bolted on top of hand-crafted features. We lose the attention-ablation story's elegance but keep every other claim. Decide by hour 9 and don't relitigate.

### Evidential Dirichlet head
- Replaces softmax: outputs evidence `e_k = softplus(logit_k)`, `α_k = e_k + 1`, `S = Σα_k`.
- `confidence = max(α_k)/S`, `vacuity = K/S`, `dissonance` = standard Dirichlet conflict measure.
- Loss: expected cross-entropy under the Dirichlet + KL regularizer toward uniform on wrong classes, annealed over the first 10 epochs.
- Temperature scaling applied post-hoc to the evidence logits for the recalibration endpoint.

### Adversarial fuzzer
Five perturbation families, each applied independently so per-family effects are separable:
1. **synonym** — WordNet substitution on non-entity content words, 15% of eligible tokens
2. **rename** — swap entity surface forms for unseen names from a held-out pool (tests memorization vs. structure)
3. **boilerplate** — inject legal/footer text before and after the target sentence
4. **reorder** — shuffle sentence order within the document, keeping the target sentence intact
5. **punctuation** — comma/period/whitespace noise, 10% of positions

Per insight: 5 perturbations × 1 variant = 5 trials minimum (scale to 3 variants each if time allows). Record label flip, confidence delta, relation loss. `fragility = weighted mean of normalized instability across trials`.

Then correlate `vacuity` against `fragility` (Spearman, since the relationship needn't be linear) and build the quartile table. **This is the number the whole project is built to be able to report.**

---

## 16. Definition of done — backend

Verification checklist. Every line is a thing to actually run, not a thing to believe.

**Extraction**
- [ ] `pytest tests/test_offsets.py` passes — `raw_text[s:e] == surface` for every mention across all seed scenarios
- [ ] Tagger dev F1 ≥ 0.90, number recorded in `evals/tagger_report.json`
- [ ] Relation extractor produces ≥ 1 relation on ≥ 80% of documents in `meridian_shell_ring`
- [ ] Every insight in the DB has non-null `char_start`, `char_end`, `sentence_text`

**Trust layer**
- [ ] Confidence, vacuity, dissonance all populated and in `[0,1]` for every insight
- [ ] Fuzzer runs the full `meridian_shell_ring` set in under 5 minutes
- [ ] `GET /evals/fragility` returns a Spearman correlation with `p < 0.05` and a populated quartile table
- [ ] Top vacuity quartile has visibly higher flip rate than bottom quartile — if it doesn't, the head is miscalibrated and that's a bug to fix, not a result to report

**Cascade**
- [ ] Escalation rate between 8% and 20% on the demo scenario (if it's 60%, the gate threshold is wrong)
- [ ] `GET /evals/routing` returns a full confusion matrix over ≥ 30 labeled cases
- [ ] `documented_failures` has ≥ 1 entry with a hand-written mechanism explanation
- [ ] Nemotron outage simulated (`NEMOTRON_FORCE_FAIL=1`): pipeline completes, insights marked `degraded`, no 500s

**Ablation**
- [ ] Ablating the top-attention edge on the demo insight produces `|Δconfidence| > 0.10`
- [ ] At least one demo insight where ablation flips the routing bucket
- [ ] Ablation p95 latency < 300ms

**Calibration**
- [ ] Baseline ECE recorded before recalibration
- [ ] `POST /calibration/recalibrate` reduces ECE, completes in < 4s, is idempotent under a repeated `Idempotency-Key`
- [ ] Reliability diagram bins sum to the total case count

**Voice**
- [ ] Briefing returns playable mp3 with segment→insight_id mapping
- [ ] Spoken question → correct insight resolution on 5 rehearsed phrasings of "why is Meridian flagged"
- [ ] `VOICE_UNAVAILABLE` path returns the prerecorded fallback and the frontend plays it
- [ ] All briefing/answer text traced to a template file — grep for any generative call in the voice path returns nothing

**API / security**
- [ ] Every endpoint returns the standard error envelope on failure (test hits each error code)
- [ ] Cross-org read attempt returns 404, not another org's data (explicit test, two seeded orgs)
- [ ] Rate limits return 429 with `retry_after_seconds`
- [ ] No secret in any committed file (`git secrets --scan` or manual grep for key prefixes)
- [ ] `docker compose up` on a clean machine brings up API + Postgres, seeds a scenario, and serves the frontend build

**Frontend contract**
- [ ] Every fixture in `data/fixtures/` validates against the live production response model
- [ ] `MOCK_MODE=1` server starts and serves all fixtures with simulated latency
- [ ] OpenAPI schema at `/api/v1/openapi.json` is current (`fastapi` auto-generates; verify it isn't lying about optional fields)
