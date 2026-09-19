# COUNTERSIGN — Backend Implementation Plan

**Owner: Faaz · Derived from `00-MISSION.md` + `01-BACKEND-BRIEF.md` · Cross-checked against `02-FRONTEND-BRIEF.md` §4, §11, §12**

`01-BACKEND-BRIEF.md` is the contract. This document is the build order, the decisions the brief left open, and the places where I'm deliberately deviating — with reasons. Where this file and the brief disagree on *what* an endpoint returns, the brief wins. Where they disagree on *how* it's built, this file wins.

Repo state at hour 0: complete scaffold, zero implementation. Config, Docker, Alembic, Makefile, CI, lint/type/test setup all exist and are good. Every Python module under `backend/` is either `__init__.py` or `.gitkeep`. So this is a from-scratch build on solid rails.

---

## 0. The one insight that reorders the whole plan

**The fragility correlation is not a Stage 5 problem. It is a Stage 1 problem.**

Claim 2 — "our vacuity score predicts real adversarial fragility" — is the differentiator, and it is the only claim in the project whose outcome I cannot control by writing better code at hour 13. It is determined by whether the synthetic corpus contains genuine epistemic variance.

If the generator emits 34 documents from 12 clean templates, the tagger and relation model will see every construction in training, vacuity will be flat near zero with a variance of ~0.001, and the Spearman correlation against fragility will come out as noise — 0.08, p = 0.4. At hour 13 there is no fix for that. Rebuilding the corpus at hour 13 invalidates the trained tagger, the relation model, the gate threshold, and the routing eval set. That is a project-ending cascade.

So the generator is built in Stage 1 with a **deliberate difficulty spectrum and a held-out construction tail**:

| Band | Share | Construction | Expected vacuity |
|---|---|---|---|
| A — canonical | 40% | Active voice, adjacent entities, template verbs seen in training (`X wired $N to Y`) | 0.02–0.15 |
| B — varied | 30% | Passive voice, intervening clauses, aliased entity surface forms | 0.15–0.40 |
| C — indirect | 20% | Nominalized relation (`payment routing through Z on behalf of X`), 2-hop implication, appositive-embedded subjects | 0.40–0.70 |
| D — out-of-distribution | 10% | Constructions **and** entity name pools held out of the tagger/relation training split entirely | 0.60–0.95 |

Band D is the load-bearing part. Without names and syntax the model has genuinely never seen, "epistemic uncertainty" is a word we'd be using about a model that has no epistemic uncertainty.

Corollary requirements, all in Stage 1:
- The generator emits a **train/demo split manifest**. `meridian_shell_ring` (the demo scenario) contains band-D documents; the tagger and GAT are trained only on the `train_corpus` scenario. No demo document is ever in a training set. If that leaks, every number we report is contaminated and a sharp judge will ask exactly that question.
- The generator emits **ground-truth routing labels** per insight (`auto_file` / `flag_for_review` / `escalate_now`), because we author the fraud and therefore know the answer. This is where the 100 routing eval cases come from. It is also a real limitation to state out loud in the writeup: our routing ground truth is generator-authored, not human-adjudicated on real documents.
- The generator **plants the documented failure case by construction**: one document where the flagged sentence looks like an early-payment anomaly and the *immediately following* sentence contains the benign contractual explanation. Our cascade passes only the single citation sentence, so it cannot see the exculpation and will over-escalate. That is an honest, mechanistically explicable failure of our own architecture, discovered by design rather than by luck, and §10's `documented_failures` requires at least one.

I'd rather spend three hours on the corpus and two on the fuzzer than the reverse.

---

## 1. Decisions the brief left open

These are made. I'll relitigate any of them if you say so, but they're locked otherwise, because relitigating architecture at hour 14 is how hackathons die.

### 1.1 Sync SQLAlchemy, sync route handlers

Every route handler is `def`, not `async def`, except the websocket. FastAPI runs sync handlers in a threadpool, which means a 400 ms torch forward pass cannot stall the event loop — and with async handlers it would, because torch and spaCy are blocking C extensions with no async story.

Async SQLAlchemy buys nothing here and costs a whole bug class (greenlet context errors, session-per-task discipline) at exactly the hours when I can least afford to debug plumbing. `psycopg` sync driver, `Session`, `scoped_query`. The websocket handler is `async def` and reaches the DB via `asyncio.to_thread`.

### 1.2 Hand-rolled GAT, not `torch_geometric.nn.GATConv`

This is the highest-leverage technical decision in the plan and it's driven directly by claim 4.

Ablation requires masking **specific named edges at inference time** and reading per-edge attention weights back out per layer. PyG's `GATConv` will return attention with `return_attention_weights=True`, but masking an individual edge means either mutating `edge_index` (which changes the graph, not the attention — a different counterfactual than the one we're claiming) or forking its forward pass.

A 3-layer, 4-head attention over a ~40-node sentence graph is about 70 lines of pure torch: linear projection, per-edge score, segment-softmax by destination index via `index_add`, weighted aggregate. Writing it myself gives me:

- `forward(graph, edge_mask: torch.Tensor | None)` as a first-class signature — masking is a feature of the model, not a hack around it
- exact per-edge, per-layer attention tensors, which is what `insights.attention` JSONB and the ablation panel need
- one fewer dependency that has to install correctly inside a slim Docker image at hour 6
- a better answer when a judge asks how the ablation works: we wrote the attention layer so we could mask a single edge and re-infer

`torch-geometric` stays in `requirements.txt` for now (harmless, already pinned) but nothing imports it. I'll drop it in the Stage 10 cleanup.

### 1.3 The ablation interface is defined before either relation model exists

The brief's cut list (§8) says: if the GAT is shaky at hour 9, fall back to rule-based relation extraction over dependency paths. Taken literally that kills claim 4, because rule-based extraction has no attention to ablate — and claim 4 is one of the two claims nobody else in the room can make.

Fix: ablation targets an interface, not the GAT.

```python
class RelationModel(Protocol):
    def infer(
        self,
        graph: SentenceGraph,
        edge_mask: EdgeMask | None = None,
    ) -> RelationOutput: ...
```

- `GATRelationModel` implements `edge_mask` by zeroing attention logits on masked edges and renormalizing the segment-softmax (`mode="zero"`) or by replacing surviving weights with uniform (`mode="uniform"`).
- `RuleRelationModel` implements `edge_mask` by deleting those dependency arcs from the path-feature extractor before computing features, then running the same evidential head over the resulting feature vector.

Both produce a real counterfactual: remove this syntactic link, re-infer, observe the confidence and routing delta. `POST /ablation/insights/{id}` never learns which model is behind it. Claim 4 survives the hour-9 cut. This costs maybe 40 extra minutes in Stage 3 and it insures the demo's strongest moment.

### 1.4 No GloVe

The brief specifies "character-CNN + 100d word embeddings (GloVe init, fine-tuned)". I'm dropping the GloVe init and training embeddings from scratch.

GloVe 100d is a ~350 MB download that has to land inside the Docker build or a runtime cache volume, and our corpus is synthetic with an effectively closed vocabulary of a few thousand types. Pretrained vectors help with generalization to unseen real-world words; we have no unseen real-world words, we have a held-out synthetic pool (band D), and character-CNN features already cover morphology for the OOV names that matter. The brief's own target — token F1 ≥ 0.90 on synthetic dev — is comfortably reachable without it.

This is a deviation from the contract document. Flagging it explicitly rather than quietly doing it. If you want GloVe in, say so in the next message and I'll bake a trimmed 50 MB vocab slice into the image instead of the full file.

### 1.5 Entity embeddings are classical, not learned

`entities.embedding VECTOR(256)` needs a definition and the brief doesn't give one. Since its stated job is coref/dedupe by string similarity: **hashed character 3-gram TF-IDF, 256 dims, L2-normalized**, cosine via pgvector HNSW. Deterministic, no training step, no model to load, and it's honest about what it does — it merges surface forms, it does not understand companies.

Combined with a normalization heuristic (case-fold, strip legal suffixes `LLC|Inc|Ltd|Corp|LP`, collapse punctuation) and a cosine threshold of 0.86. This will occasionally merge two genuinely distinct companies with similar names, which the brief already lists as known debt (§13) — good, because now the debt note describes something real rather than something hypothetical.

### 1.6 No live network ingestion, ever

The mission diagram shows `RSS / GDELT` as inputs. Fetching user-influenced URLs server-side is textbook SSRF, and there is no version of that which is safe to build in a hackathon and demo on conference wifi.

Decision: RSS and GDELT documents are **pre-fetched synthetic fixtures** with `source: 'rss'` / `'gdelt'`, generated by `data/synth/`. The API has zero endpoints that fetch a URL. This removes an entire vulnerability class, removes a wifi dependency from the demo path, and satisfies the brief's own data policy (§13: synthetic and public sources only). Say it to the Compound judges unprompted — "we don't fetch anything, so there's no SSRF surface" is a better answer than a mitigation.

### 1.7 Model checkpoints ship in the repo

`.gitignore` currently excludes `*.pt` and `backend/ml/checkpoints/`. But the definition of done says `docker compose up` works on a machine that is not ours, and a fresh clone with no weights cannot serve an insight.

Decision: commit the checkpoints, with an explicit un-ignore:

```gitignore
# ml artifacts — checkpoints are committed deliberately (see docs/03-BACKEND-PLAN.md §1.7)
*.pt
!backend/ml/checkpoints/*.pt
```

Budget: tagger ≤ 25 MB (vocab-trimmed, fp32), relation model ≤ 3 MB, intent SVM ≤ 1 MB. Under 30 MB total, which is fine for git. Belt and braces: `scripts/bootstrap.py` detects missing checkpoints and retrains from the deterministic `train_corpus` seed in ~90 s, so a clean machine works either way.

**This needs your yes.** It's the one decision here that changes a committed file in a way that's annoying to reverse.

### 1.8 Fuzzing is a job, not part of ingest

Definition of done: a judge uploads a document set and sees insights in **under 30 seconds**. Fragility eval: 214 insights × 5 perturbations = 1,070 full pipeline passes. Those two cannot be the same code path.

Decision: ingest does tag → parse → relate → score → route and stops. Fragility runs as a separate owner-only job, `POST /api/v1/evals/fragility/run` (**endpoint addition, not in the brief §10**), returning `202` with a job id and writing `fragility_trials` rows plus `insights.fragility` in the background. We run it once before the demo; the eval page reads finished rows.

Frontend impact: none. `GET /evals/fragility` is unchanged. Frontend dev 2 never calls the runner; it goes in the Makefile as `make fuzz`.

### 1.9 Nemotron calls are parallel and the "on everything" baseline is capped

28 escalations × ~800 ms sequential = 22 seconds, inside a 30-second ingest budget that also has to do all the ML. Not survivable.

- Escalation calls run through `asyncio.gather` with a semaphore of 6. Brings 28 calls to ~4 s wall clock.
- `cascade_baseline.nemotron_on_everything_accuracy` requires running Nemotron on every insight. I'm capping that to **the 100 labeled routing cases, not all 214** — accuracy on the labeled set is the only number that's meaningful anyway, since the other 114 have no ground truth to be accurate against. 100 calls, run once by `scripts/run_baseline.py` at hour ~16, cached to `data/generated/nemotron_baseline.json`. The `interpretation` string gets computed from real counts.
- The response field keeps the brief's name and shape. I'll state the n in the writeup.

### 1.10 The voice fallback returns JSON, not raw audio

Contract gap between the two briefs. Backend brief §11 says `GET /voice/fallback/briefing` "returns a pre-recorded briefing". Frontend brief §12.3 says fetch it and play it. If it returns raw `audio/mpeg`, the transcript-sync beat — which §12.1 correctly identifies as the thing that sells the voice track — dies exactly when we need it most, on bad wifi.

Decision: `GET /voice/fallback/briefing` returns the **same JSON shape as `POST /voice/briefing`**, with `audio_url` pointing at the static fallback file and a complete hand-written `transcript[]` with real `start_ms`/`end_ms`/`insight_id` values matching the seeded `meridian_shell_ring` insight UUIDs (deterministic UUIDs via seeded UUID5 — see §1.11). Degraded mode looks identical to live mode.

Telling frontend dev 2 this at hour 3, not hour 20.

### 1.11 Everything is deterministic and seeded

Judges re-run things. The number on the slide must be the number on stage.

`PIPELINE_SEED` (new env var, default `20260919`) seeds `random`, `numpy`, `torch`, and the generator. Entity and insight UUIDs in seeded scenarios are **UUID5 over a namespace + stable key**, not `gen_random_uuid()`, so a reseed produces the same ids — which is what makes the hand-written fallback transcript's `insight_id` values valid across resets, and makes the demo script's "click this insight" reliable.

---

## 2. Schema deltas

The brief's DDL (§2) is sound. Five changes:

1. **`insights` ↔ `nemotron_runs` ordering.** The brief's SQL references `nemotron_runs(id)` from `insights` before that table is declared. Not a real cycle — `nemotron_runs.insight_id` is deliberately FK-less — so SQLAlchemy's dependency sort handles it automatically once both are in the same `MetaData`. No `use_alter` needed. Noting it so nobody "fixes" it later.
2. **`calibration_snapshots.idempotency_key TEXT`**, nullable, unique partial index where not null. §10 requires `Idempotency-Key` on `POST /calibration/recalibrate` and there's nowhere to put it. A judge will double-click that button.
3. **`insights.degraded BOOLEAN NOT NULL DEFAULT false`.** §14 requires a `degraded: true` marker on insights when Nemotron hard-fails, and §6's response shape has no field for it. Adding it to the DB and to the `trust` sibling level of the insight response. **Contract addition — frontend dev 2 needs to know**, since §4.3 of the frontend brief requires degraded insights to render a "reviewed classically" marker.
4. **`ingest_jobs.stage_progress JSONB NOT NULL DEFAULT '{}'`.** §5's job response returns `stage_progress` with five keys; there's no column for it. Computing it on read from `docs_done`/`docs_total` loses per-stage granularity.
5. **`routing_eval_cases.split TEXT NOT NULL DEFAULT 'eval'`** and `gate_bypassed BOOLEAN` — needed to compute `cascade_baseline.classical_only_accuracy` (gate off, classical decision only) over the same cases.

Indexes: taking the brief's set verbatim. They're matched to the actual queries — the feed's `(org_id, routing, created_at DESC)`, the gate's `(org_id, vacuity DESC)`, the reader's `(document_id, char_start)`. One addition: a `pg_trgm` GIN index on `insights.sentence_text` because the `q` substring filter in §6 is otherwise a sequential scan, and `pg_trgm` is already installed by `docker/postgres/init/01-extensions.sql` for exactly this. Cheap, and the filter is on the feed's hot path.

---

## 3. Offset integrity — the invariant everything else rests on

Brief §15: "if this breaks, nothing else in the project matters." So it gets one module and one hard rule.

**One tokenizer.** `ml/text/tokenize.py` is the only place text is split. It returns:

```python
@dataclass(frozen=True)
class TokenizedDoc:
    text: str                              # identical to documents.raw_text, byte for byte
    tokens: tuple[Token, ...]              # (surface, char_start, char_end, pos, dep, head_idx)
    sentences: tuple[Span, ...]            # (char_start, char_end)
```

Rules, enforced by test:
- Nothing calls `.split()`, `.strip()`, `.lower()`, or any normalizer on document text. Ever. Offsets index into the stored `raw_text` and nothing else.
- Spans come from spaCy `doc.char_span` / `token.idx`, never reconstructed by searching for a substring.
- PDF and `.eml` extraction produce `raw_text` **once**, at upload. That string is stored and is the sole coordinate system. No re-extraction anywhere downstream.
- `tests/test_offsets.py` asserts `raw_text[t.char_start:t.char_end] == t.surface` for every token, every mention, and every insight `sentence_text`, across all three seed scenarios. Loud failure, no tolerance, no `.strip()` in the assertion.

**The fuzzer trap.** Perturbation changes text, which changes every offset. Perturbed variants are in-memory only, never written to `documents`, and `fragility_trials` stores no offsets — only `label_flipped`, `conf_delta`, `relation_lost`. If a perturbed variant ever reaches the `documents` table, every citation in the demo silently points at the wrong sentence. Asserted in test: `test_fuzzer_writes_no_documents`.

**Parse cache.** `sha256(sentence_text)` → serialized parse, in `data/cache/`, LRU in-process on top. The brief is right that this is the difference between a 3-minute and a 40-minute fuzzer run, and it has to exist in Stage 1 because Stage 5 has no time to retrofit it.

---

## 4. Stage plan

Hour 0 is now. Windows are mapped onto the brief's §8 timeline so the two documents stay in step. Every stage ends with a command I run, not a feeling I have.

### Stage 0 — Foundation (hour 0–1)

The spine that every later stage bolts onto. Nothing here is interesting and all of it is load-bearing.

**Files**

```
core/config.py          pydantic-settings, every value from env, zero literals
core/security.py        argon2id hash/verify, JWT mint/verify (aud+iss+jti checked)
core/ratelimit.py       slowapi limiter, per-user keyfunc w/ IP fallback
core/logging.py         structlog, request_id contextvar, secret-redaction processor
api/errors.py           AppError base, code registry, 6 exception handlers
api/deps.py             get_db, current_user, require_perm, scoped_query
api/main.py             app factory, middleware order, CORS, security headers, /health
db/models.py            all 13 tables, SQLAlchemy 2.0 declarative, typed
db/session.py           engine, sessionmaker
db/migrations/versions/*_initial_schema.py
```

**Decisions baked in here**

- Middleware order, outermost first: request-id → structlog binding → security headers → CORS → rate limit → GZip. Request-id must be outermost so an error inside any other layer still carries one.
- `scoped_query(model)` returns a `Select` already filtered on `org_id` from the JWT. Every read goes through it. The brief is right that cross-org leakage is the one bug class that would actually be embarrassing, and the defense is a helper you cannot forget to call, not 40 remembered `.where()` clauses.
- Error codes live in one `StrEnum` with `(status, default_message)`. `X-Request-ID` echoed on every response including errors.
- `filterwarnings = ["error::DeprecationWarning"]` in `pyproject.toml` will explode the moment a test imports spaCy or torch. Adding targeted `ignore::DeprecationWarning:spacy.*` / `:torch.*` entries here, not at hour 20 when a test run is blocking a merge.

**Exit:** `make init` brings up db + backend, `alembic upgrade head` applies cleanly, `GET /health` returns 200, `pytest tests/test_errors.py` hits every one of the 16 error codes in §3 and asserts envelope shape.

---

### Stage 1 — Corpus, contract, mock mode, auth (hour 1–3)

The stage that unblocks two other people. It is the highest-priority stage in the build and it is not the most interesting one, which is exactly why it goes second.

**1a. Synthetic corpus** (`data/synth/`)

```
names.py          org/person/address/account pools, TRAIN and HELDOUT disjoint
templates.py      band A/B/C/D sentence constructions, per relation class
scenarios.py      meridian_shell_ring (34), clean_baseline (28), invoice_flood (120), train_corpus (400)
generate.py       emits documents + ground-truth manifest
labels.py          ground-truth relations, routing buckets, planted failure case
```

`meridian_shell_ring`: 34 docs, a 3-hop `OWNED_BY` cycle (Meridian Supply → Advent Holdings → Kestrel Registry → Meridian), two timing anomalies, one deliberate red herring, one planted exculpatory-adjacent-sentence failure case, band distribution per §0. `clean_baseline`: 28 docs, no fraud, proves we don't cry wolf — and it's the scenario that catches a gate threshold tuned too aggressively. `invoice_flood`: 120 docs for latency. `train_corpus`: 400 docs, bands A–C only, **disjoint name pool**, never served by the API.

The manifest is a JSON sidecar: every ground-truth mention with offsets, every ground-truth relation, every ground-truth routing label, and the train/demo split assignment. This file is what makes the tagger F1, the relation eval, and the routing confusion matrix computable at all.

**1b. Response models + full route table + MOCK_MODE** — the hour-3 deliverable

All ~28 Pydantic response models written first, from the brief's JSON examples verbatim, field for field. Then the complete route table with real decorators, real `response_model`, real auth dependencies, real rate limits — and handler bodies that raise `NotImplementedError` in live mode.

I know how you feel about stubs. Here's the deal I'm making with myself, and the enforcement:

- A stub exists only so that `/api/v1/openapi.json` is complete and correct at hour 3, which is what lets frontend dev 2 run `openapi-typescript` and build against real types instead of guesses.
- `ml/registry.py` holds `STAGE_OWNER: dict[route_name, stage_number]`. `tests/test_no_stubs.py` iterates every route, and fails if a route owned by a stage ≤ the current `BUILD_STAGE` still raises `NotImplementedError`. `BUILD_STAGE` bumps in the Makefile as each stage closes. A forgotten stub fails CI; it cannot survive to the demo.
- Every stub is retired inside its own stage. None of them ship.

`MOCK_MODE` implementation: `api/mock.py` with a `@mockable("insights.list.json")` decorator. When `MOCK_MODE=1` it returns the fixture with a uniform 80–400 ms delay and, at `MOCK_ERROR_RATE`, a random error from `fixtures/errors/`. When `0` it calls through. `scripts/gen_fixtures.py` **constructs every fixture by instantiating the production Pydantic model and dumping it**, so a fixture cannot drift from the response model — if they disagree, the generator won't run. That's the brief's §12 guarantee made structural instead of aspirational.

All 19 fixtures from §12 plus the 5 error fixtures, populated with realistic spread (insights across all three buckets, vacuity 0.02–0.94, a 214-point scatter with a visible but imperfect correlation, a 3-node cycle in the graph fixture, 10 calibration bins per snapshot).

**1c. Auth** — unblocks frontend dev 1's hour 3–6 block

Full §4: register with 12-char minimum against a bundled 10k common-password list, argon2id at the brief's parameters, login with constant-padded ~250 ms response (absolute deadline, computed from a start timestamp — not a fixed sleep bolted on after, which leaks timing through the variable part), 5-in-15-min lockout, rotating refresh tokens with family reuse detection, HttpOnly/Secure/SameSite=Strict `cs_refresh` cookie scoped to `/api/v1/auth`, `GET /me` with the exact 8 permission strings from frontend brief §4.4.

RBAC:

| Permission | viewer | analyst | owner |
|---|---|---|---|
| `insights:read`, `graph:read`, `voice:use` | ✅ | ✅ | ✅ |
| `documents:upload` | | ✅ | ✅ |
| `ablation:run` | | ✅ | ✅ |
| `evals:read` | | ✅ | ✅ |
| `calibration:run` | | | ✅ |
| `users:manage` | | | ✅ |

**Exit:** `MOCK_MODE=1 uvicorn api.main:app --port 8000` serves all 19 fixtures with latency and injected errors. `openapi-typescript` against the live schema produces a clean `schema.d.ts`. Full auth round-trip test: register → login → refresh → reuse-detection → lockout → cross-org 404. Message to the frontend channel with the four contract deltas (§2.3 `degraded`, §1.10 fallback JSON, §1.8 no runner endpoint on their side, seeded UUIDs).

---

### Stage 2 — Entity tagger + ingest pipeline (hour 3–6)

**Files:** `ml/text/tokenize.py`, `ml/text/parse.py` (spaCy wrapper + SHA cache), `ml/tagger/{model,weak_supervision,train,infer}.py`, `ml/entities/{embed,coref}.py`, `workers/pipeline.py`, `scripts/train_tagger.py`, `scripts/seed.py`, real `api/v1/{documents,ingest,ws}.py`.

BiLSTM-CRF per brief §15: char-CNN + 100d learned embeddings, 2×256 BiLSTM, dropout 0.5, CRF decode over BIO. Weak supervision from the generator's own pools — regex for `MONEY`/`DATE`/`ACCOUNT_REF`, gazetteer for `ORG`/`PERSON` from the train name pool — over `train_corpus`, giving ~2,400 auto-labeled sentences (more than the brief's 400 because generation is free and more data makes the F1 claim less fragile). Hand-correct ~150 into a dev set.

Document extraction: `.txt` direct, `.csv` row-joined with preserved offsets, `.pdf` via pypdf with a 30-page cap, 10 s timeout, and a 400k-char ceiling (a 2 MB PDF can extract to far more text than a 2 MB text file — that asymmetry is a real DoS vector and the brief's per-file byte cap doesn't catch it), `.eml` via `email.parser` with headers preserved in `raw_text` so offsets cover them.

Job state machine over §2's `job_state` enum with `stage_progress` updated per stage per document. WS pushes on every transition and every 5 docs, then closes `1000`.

**Exit:** `pytest tests/test_offsets.py` green across all scenarios. Tagger dev F1 ≥ 0.90 written to `evals/tagger_report.json`. `make seed s=meridian_shell_ring` ingests 34 docs end to end, mentions persisted with verified offsets, WS streams progress.

---

### Stage 3 — Relations, with the fallback pre-wired (hour 6–9)

**Files:** `ml/relations/{graph_builder,gat,rules,head,train,infer}.py`, `ml/relations/interface.py` (the `RelationModel` Protocol from §1.3).

Sentence graph: nodes = tokens, features = `[tagger hidden ‖ entity-type one-hot(7) ‖ POS one-hot(17) ‖ relative position(2)]`. Edges = dependency arcs, bidirectional with a direction flag. Hand-rolled 3-layer / 4-head GAT, hidden 128, ELU, dropout 0.3, per-edge per-layer attention retained and layer-averaged for the UI. Readout: concat candidate entity node embeddings + graph mean-pool → 6-way classifier over the brief's relation set plus `NO_RELATION`.

`RuleRelationModel` gets written in the same stage, not held in reserve — verb-lemma + preposition patterns over dependency paths, feeding hand-crafted features into the same evidential head. It's maybe 90 minutes and it means the hour-9 decision gate is a config flag (`RELATION_MODEL=gat|rules`) rather than a rewrite.

**Hour-9 gate, decided on numbers and not relitigated:** if GAT relation F1 on the demo manifest is below 0.70, flip to `rules`, keep the evidential head and the ablation interface, move on. Write the decision in the commit message.

**Exit:** ≥1 relation on ≥80% of `meridian_shell_ring` docs. Every insight row has non-null `char_start`, `char_end`, `sentence_text`, all round-trip verified. `attention` JSONB populated with `edge_id`/`src_token`/`dst_token`/`weight`/`src_idx`/`dst_idx` per §6.

---

### Stage 4 — Evidential head, gate, insights + graph endpoints (hour 9–12)

**Files:** `ml/evidential/{head,uncertainty,temperature}.py`, `ml/cascade/gate.py`, `scripts/tune_gate.py`, real `api/v1/{insights,graph}.py`.

Dirichlet head per §15: `e_k = softplus(logit_k)`, `α_k = e_k + 1`, `S = Σα`, `confidence = max(α)/S`, `vacuity = K/S`, dissonance by the standard Dirichlet conflict measure. Loss = expected CE under the Dirichlet + KL-to-uniform on wrong classes, annealed over 10 epochs.

**Gate threshold is tuned, not asserted.** The brief's 0.45 is a guess and definition-of-done demands an escalation rate in 8–20%. `scripts/tune_gate.py` sweeps the threshold over the demo scenario and picks the value landing escalation at ~13%, then verifies on `clean_baseline` that it doesn't cry wolf. Threshold stays in config so hour 16 can retune without a code change; `routing.summary.gate.policy` reports `vacuity_gate_v2` with the actual number.

Graph endpoint: Tarjan SCC over the `OWNED_BY` subgraph server-side for `cycles` (frontend brief §9 explicitly forbids client-side cycle detection). Node `risk` = normalized weighted composite of degree centrality, cycle participation, and mean incident-edge routing severity, weights in config and documented in the response so the number isn't magic.

**Exit:** confidence/vacuity/dissonance populated and in `[0,1]` for every insight. `GET /insights` honors all 11 filter params plus cursor pagination. `GET /graph` returns the 3-node cycle. Vacuity distribution has real variance across bands — and if it doesn't, **stop and fix it here**, because Stage 5 has nothing to correlate against a constant.

---

### Stage 5 — Fuzzer + fragility (hour 12–15) · PROTECTED BLOCK

Phone down. This is the differentiator and it gets its whole block.

**Files:** `ml/fuzzer/{synonym,rename,boilerplate,reorder,punctuation,runner,fragility}.py`, `api/v1/evals.py` (fragility portion), `scripts/run_fuzzer.py`.

Five families per §15, applied independently so per-family effects separate: WordNet synonym substitution on 15% of non-entity content words; entity rename from the held-out pool (this is the one that tests structure vs. memorization, and it's the one I expect to hurt most); boilerplate legal/footer injection around the target sentence; sentence reordering with the target intact; punctuation and whitespace noise at 10% of positions.

**WordNet needs baking into the image.** `nltk.download('wordnet')` is a runtime network call, and the fuzzer dying at hour 12 inside a container with no internet is an avoidable disaster. Adding `RUN python -m nltk.downloader -d /usr/share/nltk_data wordnet omw-1.4` to both Dockerfile stages in Stage 0, plus a curated 200-word finance lexicon as a hard fallback if the corpus is missing.

`fragility` = weighted mean of normalized instability across trials (label flip weighted highest, then relation loss, then absolute confidence delta). Then Spearman `vacuity` vs `fragility`, Pearson alongside, p-value, the 214-point scatter, per-perturbation breakdown, and the quartile table.

**Honesty rule, decided now while it's cheap to commit to.** Whatever the Spearman comes out as, that's what `/evals/fragility` returns. If it's 0.62, we report 0.62 and the `interpretation` string explains the tail. If it's 0.31, we report 0.31 and say the signal is weaker than we hoped and here's our read on why. There is no branch in this code that tunes a number to look better. A middling number with a diagnosis is the research-credible outcome; a suspicious 0.97 gets picked apart in the Q&A by whoever in that room has fit a calibration curve before.

**Exit:** full `meridian_shell_ring` fuzz run under 5 minutes. `GET /evals/fragility` returns a Spearman with `p < 0.05` and a populated quartile table. Top vacuity quartile flip rate visibly exceeds bottom quartile — if not, that's a miscalibrated head and a bug to fix, not a result to ship.

---

### Stage 6 — Nemotron cascade + routing eval (hour 15–18)

**Files:** `ml/cascade/{nemotron,prompt,schema,cascade}.py`, `api/v1/routing.py`, `api/v1/evals.py` (routing portion), `scripts/build_routing_eval.py`, `scripts/run_baseline.py`.

Client per §14: 6 s timeout, 2 retries with jittered backoff via tenacity, then hard-fail to the classical decision with `resolved_by: "classical"` and `degraded: true`. The pipeline never blocks on an upstream outage and never 500s because of one.

Structured output: prompt demands a single JSON object, response is fence-stripped defensively and validated against a Pydantic model with `decision` as a 3-value enum. Parse failure counts as an upstream failure and falls back. Every call logged to `nemotron_runs` with prompt SHA, decision, rationale, latency, token counts — this table is what the Beyond the Chatbot judges will actually want to open.

**Prompt injection hardening**, since document text flows into the prompt and this is worth raising with judges before they raise it:
- The document's contribution is exactly one citation sentence, nothing more.
- It's wrapped in explicit delimiters with an instruction that the delimited region is data, not instruction.
- Control characters stripped, length capped at 400 chars.
- Output is constrained to a 3-value enum, so a *successful* injection can do nothing except pick a wrong bucket — it cannot exfiltrate, cannot change the citation, cannot reach the DB.
- The rationale field is stored and displayed but never parsed as a control signal.

That last point is the real defense and it's architectural: the LLM's blast radius is one enum value on one insight.

Routing eval from the generator's ground-truth labels: 100 cases, 3×3 confusion matrix, per-class precision/recall/F1/support, macro-F1, accuracy. `cascade_baseline` computed three ways over the same 100 cases — classical-only (gate bypassed), cascade, Nemotron-on-everything (§1.9). `documented_failures` populated from the planted case with a hand-written mechanism note; the brief is right that this field is worth more than every clean metric above it, and it does not ship empty.

**Exit:** escalation rate in 8–20%. `GET /evals/routing` returns a full matrix over ≥30 (target 100) cases with ≥1 documented failure. `NEMOTRON_FORCE_FAIL=1` → pipeline completes, insights marked `degraded`, zero 500s, frontend degradation path verified.

---

### Stage 7 — Ablation engine (hour 18–20)

**Files:** `ml/ablation/{engine,templates}.py`, `api/v1/ablation.py`.

Re-infer under `edge_mask` through the §1.3 interface, in both `zero` and `uniform` modes, persist an `ablation_runs` row, compute deltas, set `load_bearing = |Δconf| > 0.10 OR routing_changed`.

`interpretation` is **template-filled, never generated** — templates in `ml/ablation/templates.py`, selected by delta sign, magnitude bucket, and whether routing flipped. The voice layer reads this field verbatim, so it's part of the no-hallucination guarantee. `tests/test_no_generation.py` greps the ablation and voice paths for any upstream LLM call and fails if it finds one. That test is how the claim stays true at hour 23 when someone is tired.

Latency: re-running a full forward pass per call is the brief's acknowledged debt. At a 30/min rate limit on a ~40-node graph it's fine — target p95 < 300 ms, measured not assumed.

**Exit:** ablating the top-attention edge on the demo insight yields `|Δconf| > 0.10`. At least one demo insight where ablation flips the routing bucket — if none does naturally, I pick the insight for the demo script based on measured deltas rather than tuning the model to produce one. p95 < 300 ms.

---

### Stage 8 — Voice (hour 20–22)

**Files:** `ml/voice/{briefing_templates,intent,stt,tts,answer}.py`, `api/v1/voice.py`, `scripts/record_fallback.py`.

Briefing assembled from templates over structured fields, ranked by routing severity, with real segment timings. ElevenLabs TTS server-side only. Segment `start_ms`/`end_ms` come from the API's character-level timing data where available, else from a measured words-per-second estimate against actual audio duration — approximate is fine, the sync just has to look right.

Intent classifier: TF-IDF + linear SVM over ~120 hand-written utterances across the 7 intents from §11. No LLM. `unknown` returns a template asking for a rephrase and never a guess.

`explain_flag` answers assemble from: cited source sentence + the insight's most recent ablation result (running one on the top-attention edge if none exists, ~120 ms). That's what populates `ablation_run_id` in the response and it's why the voice answer can make a causal claim instead of a vibe.

Audio serving: `data/audio/{uuid}.mp3`, `file_id` validated as a UUID and never concatenated into a path unchecked (trivial traversal otherwise). `Accept-Ranges: bytes` needs a real partial-content responder — `FileResponse` doesn't do 206 — so that's a small hand-written range handler, maybe 30 minutes, not free.

Signed URLs: HMAC-SHA256 over `file_id|exp` with `JWT_SECRET`, 10-minute expiry, `?exp=&sig=`; bearer auth also accepted per §11.

**Fallback recorded and committed before the happy path is polished**, per frontend brief §12.3. `scripts/record_fallback.py` generates the `meridian_shell_ring` briefing once and writes both the mp3 and the hand-checked transcript JSON with deterministic insight UUIDs (§1.11).

**Exit:** briefing returns playable mp3 with segment→insight mapping. 5 rehearsed phrasings of "why is Meridian flagged" all resolve to the right insight. `VOICE_UNAVAILABLE` returns the fallback in full JSON shape. `test_no_generation.py` green.

---

### Stage 9 — Live recalibration (hour 22–23)

**Files:** `ml/evidential/calibration.py`, `api/v1/calibration.py`.

ECE (10 equal-width bins), MCE, Brier. Hard negatives logged throughout the event: every case where Nemotron's high-confidence routing disagrees with the classical model's high-confidence prediction. Temperature scaling = 1-parameter L-BFGS over the logged logits, seconds not minutes. Snapshots persisted with full bins so the reliability diagram can redraw both series.

Idempotency via `calibration_snapshots.idempotency_key` (§2.2): same key returns the same snapshot, no second optimization.

**The honest caveat, rehearsed:** temperature scaling fit on hard negatives only biases toward the tail. It's defensible for the demo claim and it is not a production calibration strategy. A sharp judge will ask. The answer is ready: "It's fit on the disagreement set, which is where miscalibration lives — for a production system you'd fit on a held-out sample of the full distribution, and our ECE improvement would be smaller and more honest." Better to say that first than to be caught.

**Exit:** baseline ECE recorded. `POST /calibration/recalibrate` reduces ECE, completes < 4 s, idempotent under a repeated key. Bins sum to total case count.

---

### Stage 10 — Hardening, drills, rehearsal (hour 23–24)

Full definition-of-done sweep from brief §16, every line executed. Then the drills, because the failures that kill demos are the ones nobody rehearsed:

- `NEMOTRON_FORCE_FAIL=1` → full demo path, verify degradation
- ElevenLabs key removed → verify fallback plays
- DB restarted mid-ingest → verify job marks `failed` with a stage, no 500
- Two-org cross-read test, explicit and asserted
- Every rate limit hit, verify 429 + `retry_after_seconds`
- `git secrets`-style grep for key prefixes across all committed files
- `docker compose down -v && make init && make seed` from clean, timed
- `make prod` single-origin build serving the frontend bundle

CSP note: strict CSP with no `unsafe-inline` breaks Vite's dev server. So the strict policy is conditional on `SERVE_STATIC=true` / production, and dev gets a relaxed script policy. Worth knowing before it looks like a security miss in the writeup — it's a documented dev/prod split, not an oversight.

---

## 5. Security posture (proactive, per §13 and beyond it)

Handled by design rather than by remembering:

| Class | Defense | Where |
|---|---|---|
| Cross-tenant read | `scoped_query()` on every read; two-org test | Stage 0, asserted Stage 10 |
| SQL injection | SQLAlchemy binding only; zero f-string SQL including eval aggregations, which is where the temptation actually lives | all stages, ruff `S` rules on |
| Prompt injection | one-sentence contribution, delimited, control-chars stripped, output enum-constrained | Stage 6 |
| SSRF | no endpoint fetches a URL, ever (§1.6) | by construction |
| PDF DoS | 30-page cap, 10 s timeout, 400k extracted-char ceiling | Stage 2 |
| Multipart bomb | per-file 2 MB **and** aggregate request-size middleware | Stage 2 |
| Path traversal | `file_id` UUID-validated, never path-concatenated | Stage 8 |
| User enumeration | absolute-deadline response padding on login | Stage 1 |
| Credential stuffing | 10/min/IP, 5-in-15 lockout, argon2id | Stage 1 |
| Token theft/replay | rotating refresh + family reuse detection, `jti` recorded, 15-min access | Stage 1 |
| Secret leakage | env only, structlog redaction processor, no `VITE_*` secret, grep in Stage 10 | Stage 0 |

Two I'm accepting and writing down rather than fixing:

- **WS auth via `?token=<access_jwt>`** (brief §5). A JWT in a query string can land in logs. But frontend dev 2 codes against this at hour 3 and the token lives 15 minutes on a localhost demo. Changing the contract to a 60-second WS ticket costs coordination I'd rather spend elsewhere. Mitigation: `--no-access-log` in prod (already in the Dockerfile). Debt ledger, stated in the writeup.
- **slowapi in-memory storage** resets on reload. Correct at demo volume, would need Redis in production.

The repo's `.env` is in the zip. It's gitignored and contains only a dev placeholder JWT secret with both upstream API keys blank, so nothing leaked — but worth confirming it never gets force-added, since the `.env.*` pattern with `!.env.example` is easy to trip over.

---

## 6. Shortcut ledger

Taking the brief's §13 ledger as-is and adding what this plan introduces.

**Fine for 24 hours, and I'll say so plainly if asked:**
- In-process `BackgroundTasks` instead of Celery/Redis. At demo volume this is correct, not lazy.
- Models loaded into the FastAPI process at startup rather than a separate inference service.
- No email verification on register. Single Postgres, no replica.
- Routing ground truth is generator-authored. Stated in the writeup; unavoidable in 24 hours without human annotators.
- `nemotron_on_everything` baseline capped at the 100 labeled cases (§1.9) — the other 114 have no ground truth to be accurate against.

**Real debt, flagged in the writeup rather than hidden:**
- Ablation re-runs a full forward pass instead of caching the graph encoding. Fine at 30/min, would not survive load.
- Entity coref is TF-IDF cosine + string heuristics. It *will* merge two distinct companies with similar names. Known, stated, and a good answer when asked what breaks first.
- Temperature scaling fit on hard negatives biases toward the tail (Stage 9 caveat).
- Refresh-token family revocation is correct but there's no admin UI to view active sessions.
- Rate-limit state is in-process.
- Tagger F1 ≥ 0.90 is on data we generated. Easier than it sounds, and the writeup says so rather than implying real-world performance.

---

## 7. What I need from you

**Blocking, needed before hour 15:**

1. **`NEMOTRON_API_KEY`** — both blank in `.env`. Stage 6 is the strongest track submission and it cannot be built, let alone evaluated, without a working key. I also need to know the call budget: the routing eval plus the baseline run is ~130 calls, plus ~30 per full demo rehearsal.
2. **`ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`** and whether the account has character credits. Stage 8 is a whole track. If credits are thin, the fallback recording gets made first and we ration live synthesis to the rehearsals and the demo itself.

**Confirmations — I've decided these and will proceed unless you say otherwise:**

3. **Commit model checkpoints** (§1.7), un-ignoring `backend/ml/checkpoints/*.pt`. ~30 MB. Needed for `docker compose up` on a judge's machine.
4. **Drop GloVe** (§1.4) — the one place I'm departing from the brief's ML spec.
5. **Hand-rolled GAT instead of PyG** (§1.2) — better ablation story, one less install risk.
6. **The four contract deltas** going to the frontend channel at hour 3: `insights.degraded`, fallback-briefing returns JSON not audio, `POST /evals/fragility/run` exists but is ours not theirs, seeded deterministic UUIDs.

**How you want the code delivered.** Two options and I'd pick the first: stage-by-stage, each stage as a reviewable drop with its exit tests run and the output pasted, so you can catch a wrong call at hour 4 instead of hour 16 — or one large drop of Stages 0–2 together, which is faster to hand off but slower to correct.

Say go and I start on Stage 0.
