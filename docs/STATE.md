<!-- Updated by /next-stage. Keep it to a few lines: it loads every session. -->

## Where the build is

- **Stage 0 complete** — config, logging, security, errors, deps, middleware,
  13-table schema, initial migration.
- **Stage 1 complete** — full API contract (`api/v1/schemas.py`), all 11 routers
  with `@contract` stage markers, auth implemented end to end, MOCK_MODE with
  31 fixtures, four synthetic scenarios.
- **Stage 2 complete** — spaCy parse cache, document extraction
  (`.txt`/`.csv`/`.pdf`/`.eml`), BiLSTM-CRF tagger, char-3gram entity
  embeddings + coreference, the ingest job state machine, and real
  `documents` / `ingest` / `ws` routes. `BUILD_STAGE = 2`.
  **437 tests pass**, ruff and mypy clean.
- **Next: Stage 3** — relations (`RelationModel` protocol, hand-rolled GAT,
  rule fallback, evidential head wiring). Bump `BUILD_STAGE` to 3 when its
  routes land.

## Stage 2 numbers

- Tagger: dev token F1 **1.00** (in-distribution, same templates and name pool
  as training — read as a floor, not a result); **held-out token F1 0.9922 /
  span F1 0.9861** on `meridian_shell_ring`, whose names and 10% band-D syntax
  the model has never seen. That second number is the one to quote.
  `ml/evals/tagger_report.json`, checkpoint 3.5 MB, trains in ~60 s.
- `make seed s=meridian_shell_ring`: 34 documents, 361 mentions, 141 entities,
  **1.5 s**. The six-company cast resolves to exactly six ORG nodes.

## Decisions taken in Stage 2 (deviations worth knowing)

- **Word dropout (0.3) in tagger training.** Without it the model reads the
  word embedding and ignores the character CNN — perfect on dev, ORG span F1
  **0.00** on the demo scenario. `ml/tagger/train.py` documents the measurement.
- **The training name pool gained suffix-less orgs and short aliases.** Every
  original `TRAIN_ORG` ended in a legal suffix, so "two capitalized words, no
  suffix" only ever appeared as a person — and the tagger typed
  `Advent Holdings` as PERSON, splitting the demo's ownership cycle. Band D is
  meant to hold out *names*, not name *morphology*. Pools remain disjoint,
  asserted over the full alias closure.
- **Header dates and account refs are now gold mentions.** The generator
  labeled the ORG in an invoice header but not the DATE beside it, so the gold
  set disagreed with itself and capped achievable F1 at ~0.83.
- **ORG and PERSON share one entity-id namespace**; the stored type is a
  majority vote over the cluster's mentions. Keying on `(type, name)` split
  entities the tagger was unsure about.
- **Prefix containment added to coreference** alongside the 0.86 cosine:
  `Meridian` vs `meridian supply` scores ~0.7, below threshold, and cosine
  alone left the demo's central company as two nodes.
- Weak supervision is *exact* against gold on this closed-vocabulary corpus,
  so `dev` measures the model, not the labeling function. Said plainly in the
  report rather than left to look like a strong result.
- `.claude/commands/next-stage.md` does not exist in this repo; the stage
  procedure is being followed from the run brief instead.

## Open blockers

- `NEMOTRON_API_KEY` — needed by Stage 6. Also need the call budget.
- `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` — needed by Stage 8.
- Model checkpoints **are** committed (plan §1.7). `ml/checkpoints/tagger.pt`
  is 3.5 MB, well inside the 30 MB budget.
