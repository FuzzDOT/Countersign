# COUNTERSIGN — Mission Brief

**SteelHacks XIII · Sept 19–20, 2026 · Pittsburgh, PA**
**Team size: 3 · Duration: 24h · Tracks targeted: 5**

---

## 1. The one-sentence version

COUNTERSIGN is a financial risk analyst for businesses too small to hire one. It reads every invoice, vendor email, and news signal you get, extracts what matters using classical neural extraction with **zero generative text**, proves its own confidence scores are real by adversarially attacking itself, escalates only its genuinely-uncertain cases to NVIDIA Nemotron, causally verifies its own explanations by ablating them, and briefs you out loud instead of making you check a dashboard.

## 2. Why this wins instead of placing

Read the judging criteria across all six SteelHacks tracks and one word appears in four of them: **trust**. Xtract wants source traceability. Compound wants you to know what could go wrong. Beyond the Chatbot wants *evidence that it works — an eval, comparison, benchmark, or even a failure you found*. Seed Round wants a reason to believe.

Every other team will handle trust with a number. They will put a confidence score next to a prediction and move on. That number will be an unvalidated softmax output, and if a judge asks "how do you know that 0.87 means anything," the team will have nothing.

We win by making uncertainty **load-bearing instead of decorative**. Five falsifiable claims, each with a number attached:

| # | Claim | How we prove it live |
|---|-------|----------------------|
| 1 | Every insight traces to an exact source span | Click any claim → source document scrolls to and highlights the originating sentence, byte-offset exact |
| 2 | Our confidence score predicts real model fragility | We adversarially perturbed our own pipeline and measured the correlation between our uncertainty score and actual prediction instability |
| 3 | We only spend an LLM where our classical model admits ignorance | Routing cascade metrics: % of insights handled classically vs. escalated, with per-bucket precision/recall |
| 4 | Our explanations are causal, not cosmetic | Live attention-edge ablation: zero an edge, watch the confidence and routing decision actually change |
| 5 | The system's calibration measurably improves during the demo | ECE before/after a live temperature-scaling pass, computed on stage in seconds |

No hackathon project in that room will be equipped to make claim 2 or claim 4. Those are the two that turn "excellent project" into "these people do research."

## 3. The architecture, conceptually

```
 DOCUMENTS                CLASSICAL EXTRACTION            TRUST LAYER
 ─────────                ────────────────────            ───────────
 invoices      ┐          BiLSTM-CRF entity tagger        Dirichlet evidential head
 vendor email  ├────────▶  → dependency parse        ────▶ → calibrated confidence
 press release │           → GAT relation extractor        → epistemic uncertainty (vacuity)
 RSS / GDELT   ┘           → byte-offset citation           → adversarial fragility score
                                                                      │
                                    ┌─────────────────────────────────┤
                                    │                                 │
                            LOW UNCERTAINTY                   HIGH UNCERTAINTY
                                    │                                 │
                                    ▼                                 ▼
                            auto-file, classically          NEMOTRON TRIAGE CASCADE
                            resolved, no LLM call           → auto-file / flag / escalate
                                    │                                 │
                                    └────────────┬────────────────────┘
                                                 ▼
                                    ┌────────────────────────┐
                                    │   RISK GRAPH + FEED    │
                                    └────────────────────────┘
                                          │            │
                          CAUSAL ABLATION │            │ VOICE BRIEFING
                          zero an edge,   │            │ ElevenLabs TTS out
                          re-infer, show  │            │ ElevenLabs STT in
                          the delta       │            │ cited answers read aloud
```

## 4. What each component is actually for

### 4.1 Classical extraction — the "no generative text" spine

A **BiLSTM-CRF sequence tagger** for entities (`ORG`, `PERSON`, `MONEY`, `DATE`, `TRANSACTION_TYPE`, `ACCOUNT_REF`). Bootstrapped with regex/gazetteer weak supervision on synthetic data, refined on a small hand-labeled set. It is a classifier over tokens — it cannot emit a token it hasn't been trained to tag, which is the architectural guarantee behind our no-hallucination claim.

A **Graph Attention Network** over dependency parses for relation extraction. Each sentence becomes a local graph: tagged entities as nodes, syntactic dependency paths as edges. The GAT scores which entity pairs carry a real relation (`WIRED_FUNDS_TO`, `OWNED_BY`, `INVOICED`, `SHARES_ADDRESS_WITH`, `SIGNATORY_OF`). Attention weights are retained — they become the ablation targets in §4.4.

**Citation grounding** is not a feature, it is the foundation. Byte offsets are tracked through tokenization so every extracted triple carries `(doc_id, char_start, char_end)`. If this breaks, nothing else in the project matters.

### 4.2 The trust layer — where we stop being a normal hackathon project

A **Dirichlet evidential head** on the GAT relation embeddings outputs a full Dirichlet distribution over relation classes rather than a point softmax. This gives us two separable quantities: *aleatoric* uncertainty (the data is genuinely ambiguous) and *epistemic* uncertainty / **vacuity** (the model has never seen anything like this). Vacuity is the signal that drives the entire cascade.

Then the part nobody else does. A **classical adversarial perturbation fuzzer** — synonym substitution from a WordNet-style lexicon, entity renaming, boilerplate injection, sentence reordering, whitespace and punctuation noise. No LLM anywhere in the fuzzer. We run the full pipeline on original and perturbed variants of each document and measure prediction instability (label flips, confidence deltas, relation dropouts).

Then we correlate. **Does our vacuity score spike on exactly the cases that turn out to be adversarially fragile?** That is a real, falsifiable, measurable claim about whether our uncertainty means anything. Report Spearman correlation and a scatter plot. If it comes out at 0.62, we report 0.62 and explain the tail — an honest middling number with a diagnosis beats a suspicious 0.97 every single time, and judges who know the field will recognize the difference instantly.

### 4.3 Nemotron as a triage cascade — the Beyond the Chatbot answer

**Nemotron is never called on every insight, and never talks to the user.** It sits between extraction and action, invoked only on the high-vacuity tail.

Input: a structured insight (subject, relation, object), its confidence and vacuity scores, its citation span, and its local graph neighborhood. Output: one routing decision from `auto_file`, `flag_for_review`, `escalate_now`, plus a short structured rationale.

This is a legitimate production pattern — cheap model handles the confident majority, expensive model handles the hard tail — and it is the strongest available answer to the track's own question, *why did you need Nemotron?* The answer: **because our classical system knows what it doesn't know, and the only insights worth an LLM call are the ones it flags.** That is materially better than "Nemotron classifies everything," which is what the other submissions to that track will say.

**Eval requirements (the track demands evidence, so this is non-optional):**
- Labeled synthetic routing set, target 100 cases, floor 30 if time-crunched
- Per-bucket precision / recall / F1, plus a 3×3 confusion matrix
- Cascade efficiency: % handled classically, % escalated, LLM calls saved
- **At least one documented failure**, with an explanation of the mechanism. Over-escalation of a benign timing anomaly, or under-escalation of a subtle multi-hop shell pattern, are both realistic and both interesting. Finding your own failure is explicitly listed in the track criteria — it is worth more than a clean sweep.

### 4.4 Causal explainability — beating the attention-heatmap default

Attention visualizations are the generic move and attention is known to be an unreliable explanation on its own. We go one step further.

For any flagged insight, **zero out specific GAT attention edges and re-run inference.** If the routing decision and confidence actually change, the edge was load-bearing and the explanation is causal. If they don't, the pretty heatmap was lying and we say so.

This gets wired into the voice interrogation loop. The user asks "why is this flagged?" and hears a tested causal claim: *"Removing the link between Meridian Supply and Advent Holdings drops confidence by 41 points, so that connection is what's driving this flag."* That sentence is defensible under judge questioning because we ran the counterfactual.

### 4.5 Voice — the Out Loud answer, justified not bolted on

**Briefing mode (TTS):** ElevenLabs delivers a spoken summary of what needs attention, ranked by routing severity, with confidence read aloud. *"Three things need attention. First: an invoice from Meridian Supply matches a payment pattern seen on two other flagged accounts, confidence eighty-one percent."*

**Interrogation mode (STT → structured lookup → TTS):** the user asks a follow-up in natural speech. Crucially, the answer is **not generated** — the intent is classified, the relevant insight is looked up, and the response is assembled from the cited source sentence plus the ablation result. Voice is the delivery channel for retrieved facts, not a text generator.

**The one sentence the judges asked for:** a business owner doing inventory, driving between sites, or standing in a warehouse cannot read a dashboard, but they will listen to a 90-second briefing and ask one follow-up. A text box version of this product is a worse product — which is exactly the bar that track sets.

### 4.6 Live recalibration — the demo beat that wins the room

Throughout the event, every case where Nemotron's high-confidence routing disagrees with the classical model's own high-confidence prediction is logged as a hard negative.

On stage, we run a lightweight **temperature-scaling pass** over the Dirichlet head against that logged set. It takes seconds. We display **expected calibration error before and after**, live, with the reliability diagram redrawing in front of the judges.

"Watch our model get measurably better calibrated in the next ten seconds" is a real number changing in real time. It is the single highest-leverage moment available to us and it should be the last thing in the demo.

## 5. Track strategy

| Track | Our claim | Strength |
|-------|-----------|----------|
| **Beyond the Chatbot** (Nemotron) | Uncertainty-gated triage cascade with full eval + documented failure | **Strongest.** Directly answers "why did you need it" |
| **Xtract** (signal-to-insight) | Byte-exact citation on every claim, multi-source ingestion, clean inspection UI | **Very strong.** Traceability is our foundation, not an add-on |
| **Out Loud** (ElevenLabs) | Voice-first briefing + spoken interrogation with cited, non-generated answers | **Strong**, provided we nail the "why voice beats a screen" sentence |
| **Compound** (financial) | SMB fraud/risk copilot, synthetic data, quantified false-positive story | **Strong.** The eval gives us real edge-case answers |
| **Seed Round** (fundability) | Compliance copilot for businesses under the analyst-hiring threshold | **Solid.** Working product, not a deck |

### Explicitly NOT submitting

**No Wrapper** — the rules state *no language models in the finished project*. Nemotron is a language model. Submitting to both is not a long shot, it is disqualifying and it would look like we didn't read the rules. Skip it.

**Press Start** — a game does not belong in this system. Bolting one on reads as scope-chasing, and that track explicitly rewards one mechanic done well over breadth. Skip it.

Five honest tracks beats seven stretched ones. Judges talk to each other.

## 6. Demo script — 3 minutes, rehearsed twice minimum

1. **(0:00–0:20) The problem, concretely.** "A bookkeeper at a 12-person company gets 40 invoices and 200 emails a week. Nobody is checking any of it for fraud. Enterprise tools for this start at five figures a year."
2. **(0:20–0:50) Ingest live.** Drop a document set. Show the graph assembling. Show the insight feed populating with confidence scores.
3. **(0:50–1:20) Trace a claim.** Click a flagged insight. Source document opens, exact sentence highlights. "Every claim in this system does that. Nothing is generated."
4. **(1:20–1:50) The cascade.** Show the routing panel: 87% handled classically, 13% escalated to Nemotron, and here's the eval — precision, recall, and the one case we got wrong and why.
5. **(1:50–2:20) Ablation.** Pick the flagged insight. Zero the key edge. Confidence visibly drops. "Our explanation isn't a heatmap. We ran the counterfactual."
6. **(2:20–2:50) Voice.** Trigger the briefing. Let it speak. Ask it "why is Meridian flagged?" out loud. Let it answer with the citation and the ablation result.
7. **(2:50–3:00) Recalibrate live.** Hit the button. ECE drops on screen. Stop talking. Let the number land.

**Fallback plan:** pre-record the briefing audio and the interrogation exchange as local files. Room wifi will be bad. If the live ElevenLabs call fails, play the recording and say so honestly — judges forgive infrastructure, they don't forgive a frozen screen.

## 7. Team split

| Owner | Scope |
|-------|-------|
| **Faaz** | Entire backend: extraction, GAT, evidential head, fuzzer, Nemotron cascade, ablation engine, recalibration, voice pipeline, API, Docker |
| **Frontend dev 1** | Marketing surface + auth: landing page, hero animation, auth flow, app shell, settings, protected routing |
| **Frontend dev 2** | Application surface: graph canvas, insight feed, citation reader, ablation panel, eval dashboards, voice console |

Backend ships mock fixtures matching the real API contract by **hour 3** so frontend is never blocked. See `01-BACKEND-BRIEF.md` §11 and `02-FRONTEND-BRIEF.md` §4.

## 8. Build timeline

| Hours | Backend (Faaz) | Frontend (2 devs) |
|-------|----------------|-------------------|
| 0–3 | Synthetic data generator, entity tagger scaffold, **mock API fixtures published** | Design tokens, type system, app shell, routing skeleton |
| 3–6 | BiLSTM-CRF trained, citation offsets verified | Landing page hero + animation, auth screens |
| 6–9 | Dependency parse → GAT relation extraction | Graph canvas against mocks, insight feed |
| 9–12 | Evidential Dirichlet head, calibration sanity check | Citation reader, document viewer with span highlighting |
| 12–15 | **Adversarial fuzzer + fragility correlation (protected block)** | Ablation panel, eval dashboard shells |
| 15–18 | Nemotron cascade + routing eval set built in parallel | Eval dashboards wired to real endpoints |
| 18–20 | Ablation engine, causal deltas exposed via API | Voice console, audio playback, waveform |
| 20–22 | ElevenLabs TTS briefing + STT interrogation loop | Polish pass, reduced-motion, keyboard nav, mobile |
| 22–23 | Live recalibration endpoint, ECE before/after | Recalibration UI, reliability diagram |
| 23–24 | Buffer: fallback audio, kill flaky paths, rehearse | Buffer: rehearse, screenshot QA |

### Protect at all costs, in priority order
1. **Citation grounding.** Nothing else matters without it.
2. **Adversarial fragility eval.** This is the differentiator.
3. **Nemotron cascade + documented failure case.** Track requirement.
4. **Voice briefing loop.** Whole track depends on it.

### Cut in this order if behind
1. Custom HNSW index (use pgvector)
2. Nemotron eval set 100 → 30 cases
3. Ablation: general tool → single pre-selected edge, hardcoded for demo
4. GAT relation extractor → rule-based relations over dependency edges, keep the evidential head on top

## 9. What "done" looks like

- A judge can upload a document set and watch insights appear, with confidence scores, in under 30 seconds.
- Every insight in the feed clicks through to a highlighted source span.
- The eval page shows real numbers: fragility correlation, routing precision/recall, cascade efficiency, calibration curve.
- The ablation panel changes a confidence score in response to an edge being zeroed.
- The system speaks a briefing and answers a spoken follow-up with a cited fact.
- ECE drops when the recalibrate button is pressed.
- `docker compose up` works on a machine that is not ours.

## 10. The pitch, distilled

> Everyone else's confidence score is decoration. Ours is tested — we adversarially attacked our own pipeline and measured whether our uncertainty predicts real fragility. We only call an LLM on the cases our classical model admits it doesn't understand, and we can show you the precision, the recall, and the one case we got wrong. Our explanations aren't attention heatmaps — we ablate the edge and show the answer actually changes. And we can make this system measurably better calibrated in the next ten seconds, live, right now.

Five falsifiable claims. Most rooms will see zero.
