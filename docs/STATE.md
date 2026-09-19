<!-- Updated by /next-stage. Keep it to a few lines: it loads every session. -->

## Where the build is

- **Stage 0–1 complete** — config, logging, security, errors, deps, middleware,
  13-table schema, full API contract, auth end to end, MOCK_MODE with 31
  fixtures, four synthetic scenarios.
- **Stage 2 complete** — parse cache, document extraction, BiLSTM-CRF tagger,
  entity embeddings + coreference, ingest job state machine, real
  `documents` / `ingest` / `ws` routes.
- **Stage 3 complete** — sentence graphs, hand-rolled GAT, rule fallback, the
  shared evidential head, classical routing, insight persistence.
- **Stage 4 complete** — the vacuity gate, threshold tuning against the
  definition of done, the graph assembler (simple cycles, node risk), and the
  real `insights` / `graph` endpoints.
- **Stage 5 complete** — the five-family adversarial fuzzer, the fragility
  score, and `GET /evals/fragility` + the owner-only runner.
- **Stage 6 complete** — the Nemotron client, the cascade, the audit log and
  the routing eval. **No live call has been made: `NEMOTRON_API_KEY` is
  unset**, so the shipped behaviour is the degraded path. `BUILD_STAGE = 6`.
- **Next: Stage 7** — the ablation engine.

**675 tests pass**, 1 skipped; ruff, ruff format and mypy clean.

## Stage 6: what runs, and what has not been run

Implemented and tested against `httpx.MockTransport` — real client, real
retry policy, real parsing, faked server:

- 6 s timeout, 2 retries, full-jitter backoff; 4xx not retried, 429/5xx are.
- Malformed JSON is an upstream failure, exactly like a timeout (brief §14).
- `asyncio.gather` with a semaphore of 6 (plan §1.9); one failure does not
  cancel the batch.
- Responses cached by prompt digest, so a rehearsal replays rather than
  re-spends — and replays **without a key**, which is the bad-wifi path.
- Every call logged to `nemotron_runs`, **including the failures**, with the
  prompt digest rather than the prompt.

**Not verified, and it cannot be from here:** that the hosted endpoint
accepts our request shape, and how often the model returns parseable JSON.
Both need a key. `make baseline` exits 2 with an explanation rather than
writing anything.

With no key the demo tenant runs the degraded path end to end: 11 of 60
insights gated, all falling back to classical, `degraded: true` on each,
zero 500s. That is the drill from plan §4 Stage 10, and it is what the
committed fixtures and tests reflect.

## Stage 6 numbers (classical only — no LLM in the loop)

| what | number |
| --- | --- |
| Escalation rate | **18.3%** (11/60), inside the 8–20% band |
| Routing eval cases | 54 (gold relations the pipeline extracted) |
| Accuracy / macro-F1 | **0.648** / 0.489 |
| Per class F1 | auto_file 0.78 · flag_for_review 0.12 · escalate_now 0.57 |
| Documented failures | 6, the planted one first with its mechanism note |

The accuracy is the classical router's, and it is not good. `flag_for_review`
precision is 0.08 — the rule over-flags. That number was **not** tuned away:
the thresholds are fitted to the escalation *rate* the definition of done
specifies, never to these labels, because fitting a decision threshold to the
set it is about to be scored against is marking your own homework. Improving
the rule itself is legitimate and is where the remaining headroom is.

## Claim 2, measured

`make fuzz` — 34 documents, 60 insights, 300 trials, **16 seconds**.

| | |
| --- | --- |
| **Spearman(vacuity, fragility)** | **0.59** (Pearson 0.44, p = 6.9e-07, n = 60) |
| Top vs bottom vacuity quartile flip rate | **11.0×** (0.147 vs 0.013) |
| Mean fragility by quartile | 0.007 → 0.027 → 0.040 → 0.136 |

Per family — flip rate / relation loss:

| family | flip | loss | reading |
| --- | --- | --- | --- |
| punctuation | 0.183 | 0.150 | the most damaging, and not for the reason expected |
| rename | 0.083 | 0.083 | structure over memorization, mostly holding |
| synonym | 0.017 | 0.000 | wording is not what the model keys on |
| boilerplate | 0.000 | 0.000 | position in the document does not matter |
| reorder | 0.000 | 0.000 | as predicted: the model reads one sentence |

**The surprise is worth more than the headline.** The plan expected `rename`
to hurt most. It is `punctuation`, and the mechanism is not the model: a
stray newline splits the sentence, the two parties land in different graphs,
and the claim disappears — 15% of those trials lose the relation outright.
That is a direct cost of the line-aware segmentation added in Stage 3, which
made citations tight and the pipeline more brittle to OCR noise. Our
*pipeline* is more fragile than our *model*, and the interpretation string
says so.

Pearson (0.44) sits well below Spearman (0.59): the relationship is monotonic
and not linear, which is exactly why Spearman is the headline.

## Numbers

| what | number |
| --- | --- |
| Tagger, held-out scenario | token F1 **0.9895**, span F1 0.9807 |
| Relations, held-out (gold mentions) | GAT F1 **0.896**, rules 0.786 |
| Relations, **end to end** | GAT P/R/F1 **0.860**, rules 0.765 |
| Document coverage | 34/34 documents yield ≥1 relation |
| `make seed s=meridian_shell_ring` | 34 docs → 60 insights in **2.0 s** |
| Vacuity by band (A/B/C/**D**) | 0.115 / 0.127 / 0.108 / **0.254** |
| Accuracy by band (A/B/C/**D**) | 0.98 / 0.98 / 1.00 / **0.06** |
| Spearman(band, vacuity) | **0.386** over 417 gold pairs |
| Routing, demo | auto 62% · flag 27% · **escalate 12%** |
| Routing, `clean_baseline` | auto 67% · flag 33% · **escalate 0%** |
| Gate LLM-call rate | **11.7%** (target band 8–20%) |

Reports: `ml/evals/{tagger,relations,vacuity}_report.json`,
`ml/evals/gate_tuning.json`. Regenerate with `make train`, `make tune-gate`,
`make vacuity`.

## The claim-2 precondition, measured

Plan §0 says a flat vacuity distribution is a **Stage 4 problem to stop and
fix**, because Stage 5 has nothing to correlate against a constant. It is not
flat, but the shape is worth knowing before Stage 5 interprets anything:

- Bands **A, B and C are indistinguishable** (vacuity ≈ 0.11, accuracy ≈ 0.98).
  They are all in-distribution and all easy, so that is the honest outcome
  rather than a failure.
- Band **D is cleanly separated**: 2.2× the vacuity and accuracy of 0.06. The
  model is uncertain precisely where it is wrong.
- So the fragility correlation will be **driven by the band-D tail**. Expect a
  moderate Spearman, not a suspicious one, and say so.

The KL weight was swept against this report rather than against the loss:
KL 1.0 → Spearman 0.345, F1 0.889; KL 3.0 → 0.386, F1 0.896; KL 8.0 →
Spearman −0.05 with vacuity 0.9 everywhere and accuracy 0.15, which is the
"I know nothing about everything" collapse the head's docstring warns about.
Settled on 3.0 for the GAT and 1.0 for the rule model, which has 84 features
and loses 0.21 F1 at 3.0.

## Stage 6 decisions

- **A degraded call still writes an audit row**, carrying the classical
  decision it fell back to, the reason and `degraded = true`. A
  `nemotron_runs` table containing only successes lies by omission.
- **Escalation counts gated insights, not successful calls.** Otherwise the
  volume figure would drop to zero whenever the upstream is down, which is
  exactly when the cascade is doing the most interesting thing.
- **`GET /routing/summary` is served as well as `GET /routing`.** The brief
  documents the first, the hour-3 route table published the second and the
  frontend generated TypeScript from it. One extra line beats a contract
  change at hour 16.
- **Classical latency is measured on demand**, not stored: insights are
  written in a batch and a batch gives a mean, so reporting one under a field
  called `p95` would be a small lie in a response whose purpose is to be
  checkable. Twelve re-inferences, cached a minute (`ml/cascade/latency.py`).
- **The planted failure's note now says what actually happened.** It
  describes the cascade over-escalating; with no key the classical model
  under-routes it instead. The note keeps its mechanism and gains an
  "Observed in this run" sentence, so it does not claim a failure we did not
  see.
- **Gold relations the extractor missed are not eval cases.** That is a
  recall failure the relation eval already reports, and counting it here
  would mix two mistakes into one confusion matrix.
- Operational gotcha, found the hard way: `.env` is read into the container
  environment at `docker compose up`, and the process env wins over the file.
  A threshold change needs `docker compose up -d backend`, not just an edit —
  the gate silently ran at the brief's 0.45 for an hour because of this.

## Stage 5 decisions

- **The whole document is perturbed, not just the sentence.** Re-inferring one
  sentence would be four times faster and would make `reorder` a definitional
  no-op; running the document puts coreference and segmentation in scope,
  which is where two of the five families do their damage.
- **Matching is by entity, not by string.** After a rename the parties have
  different names, so the runner resolves candidates through the same
  coreference layer the pipeline uses, inverting the rename map first.
  Matching on text would score every rename as a total loss.
- **`names.FUZZ_*` is a third pool**, disjoint from training *and* held-out.
  Renaming into the held-out pool would sometimes pick a company already in
  the scenario and score a coreference merge as a relation loss.
- **Vacuous trials are excluded, not scored as zero.** A family that could not
  perturb a document has not shown the insight is robust.
- Perturbed text never touches `documents`, and `fragility_trials` has no
  offset columns at all. Both are asserted, including a byte-identity check
  of every stored document after a run.

## Decisions and findings worth knowing

- **Thresholds are tuned, not guessed** (`make tune-gate`): flag 0.60,
  escalate 0.80, vacuity gate 0.225. Tuned against the escalation *rate* the
  definition of done specifies and verified on `clean_baseline` — never
  against the ground-truth routing labels, which are what Stage 6 scores.
- **Cycles cover OWNED_BY *and* WIRED_FUNDS_TO**, need 3 hops for funds and 2
  for ownership, and are filtered to minimal ones. Reciprocal payments between
  two trading partners are ordinary trade; counting them as layering put 42%
  of the no-fraud corpus in the review queue. Without minimality the demo
  graph returned eight overlapping loops where one is the ring.
- **`cycles` holds simple cycles, not SCCs.** The funds subgraph is one
  five-node component and rendering it as a blob hides the ring inside it.
- **Node risk weights are config** (0.25 degree / 0.40 cycle / 0.35 severity)
  and are written into the endpoint's OpenAPI description. The response shape
  is unchanged — no contract churn for a documentation problem.
- **The band-D ownership edge is still missed**, by design: ownership recovers
  a 2-hop chain and the funds graph closes the ring. Training on band D would
  fix the edge and destroy the claim the project is about.
- Line-aware sentence segmentation, word dropout, train-pool morphology
  coverage, the capitalized-span filter and one-claim-per-direction are all
  Stage 2–3 findings; see the stage commits for the measurements.
- `.claude/commands/next-stage.md` does not exist in this repo; the stage
  procedure is being followed from the run brief.
- `BUILD_STAGE` skipped 3: that stage owns no HTTP route, and
  `tests/test_no_stubs.py` now records that in `ROUTELESS_STAGES` rather than
  having the tripwire switched off.

## Open blockers

- `NEMOTRON_API_KEY` — needed by Stage 6, with the call budget.
- `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` — needed by Stage 8.
- Checkpoints **are** committed (plan §1.7): tagger 3.5 MB, GAT 0.7 MB, rules
  26 KB.
