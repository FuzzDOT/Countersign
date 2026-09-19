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
  real `insights` / `graph` endpoints. `BUILD_STAGE = 4`.
- **Next: Stage 5 (PROTECTED BLOCK)** — the fuzzer and the fragility
  correlation. This is the differentiator.

**567 tests pass**, 1 skipped; ruff, ruff format and mypy clean.

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
