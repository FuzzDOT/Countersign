# Fixtures

These unblock the frontend before the pipeline exists. `MOCK_MODE=1` serves
them instead of touching the ML stack.

Generate them from the **same Pydantic response models** as production —
never hand-write them. If a fixture and a real response disagree, that's a
backend bug, not a frontend bug.

    python -m scripts.gen_fixtures

Expected files (see backend brief §12):

    auth.me.json
    documents.seed.json
    documents.detail.json          # full raw_text + spans — the reader's test case
    ingest.job.queued.json
    ingest.job.tagging.json
    ingest.job.relating.json
    ingest.job.done.json
    insights.list.json             # 25 items, all routing buckets
    insights.detail.json           # with attention[] + tokens[]
    insights.stats.json
    graph.full.json                # 42 nodes, 67 edges, one 3-node OWNED_BY cycle
    graph.entity.json
    ablation.run.json              # load_bearing: true, -0.41 confidence delta
    routing.summary.json
    routing.runs.json
    evals.fragility.json           # 214-point scatter
    evals.routing.json             # confusion matrix + one documented failure
    evals.calibration.json         # baseline + post snapshots, 10 bins each
    calibration.recalibrate.json
    voice.briefing.json            # with segment → insight_id mapping
    voice.ask.json
    errors/401.token_expired.json
    errors/403.forbidden.json
    errors/422.validation.json
    errors/429.rate_limited.json
    errors/503.nemotron_unavailable.json

`errors/` is served randomly when `MOCK_ERROR_RATE > 0`. Leave that at 0.1
while building so error paths get exercised instead of retrofitted.
