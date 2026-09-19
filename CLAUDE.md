# COUNTERSIGN — backend

Hackathon build (SteelHacks XIII). Classical extraction + evidential uncertainty
+ a Nemotron cascade, with byte-exact citations and zero generative text.

@docs/STATE.md

## Commands

Everything runs in the container. Never run `python` or `pytest` on the host —
it picks up Anaconda, whose package versions have nothing to do with
`backend/requirements.txt`.

```
make test-be          # pytest in the container (~320 tests, seconds)
make fixtures         # regenerate backend/data/fixtures from the corpus
make migrate          # alembic upgrade head
make corpus           # summary of all four synthetic scenarios
make dev              # API on :8000
docker compose exec backend python -c "import api.main"   # fastest import-chain check
```

After changing `requirements.txt`: `docker compose build backend`.

## The planning docs — read on demand, not up front

| file | when to read |
| --- | --- |
| `docs/03-BACKEND-PLAN.md` | **read only the current stage's section** from §4 before starting it |
| `docs/01-BACKEND-BRIEF.md` | the contract. Read the § for the endpoint group you're touching |
| `docs/00-MISSION.md` | product framing and the demo script. Read once if you need the "why" |
| `docs/02-FRONTEND-BRIEF.md` | only when changing a response shape |
| `docs/04-CONTRACT-NOTES.md` | what the frontend devs were told. Update if a shape changes |

These total ~41k tokens. Do not read them all. Grep or read the specific
section; the stage sections in §4 are self-contained.

## Invariants that have already cost time

**`@contract` + `from __future__ import annotations`.** Every router uses
postponed annotations, so FastAPI resolves them against `endpoint.__globals__`
— which for a decorated handler is `api/mock.py`. It does **not** raise on an
unresolvable annotation; it silently demotes the parameter to an untyped
required field, and every dependency becomes a mandatory query param returning
`422 {"db": "Field required"}`. `api/mock.py` fixes this by setting
`__signature__` from `inspect.signature(fn, eval_str=True)`. Consequences:
- pass `response_model=` explicitly on every route, never rely on inference
- a 204 route needs `response_model=None` and `response_class=Response`
- `tests/test_no_stubs.py` guards both

**Offsets are constructed, never searched.** Nothing may call `.find()`,
`.strip()` or `.split()` on document text to locate an entity. `raw_text` is
the single source of truth for every `char_start`/`char_end` in the system.
The generator tracks a running cursor; `verify_offsets()` runs on every
document.

**Band D never enters the training split.** `templates_for(..., split="train")`
raises for band D. Held-out entity names live only in `HELDOUT_*` pools. If
either leaks, vacuity goes flat and the fragility correlation — the project's
central claim — becomes noise.

**No generative text in voice or ablation.** Briefings and spoken answers are
template-filled over structured fields plus the cited sentence. Nothing in
`api/v1/voice.py` or `ml/ablation/` may call an upstream LLM.

**`BUILD_STAGE` lives in `api/mock.py`**, not in settings — deliberately not
overridable by config. Bump it when a stage lands; `tests/test_no_stubs.py`
then fails for any route that stage owns which is still `pending=True`.

**structlog.** `get_logger` binds the module name as `logger_name`, not
`logger` — `logger` is a reserved kwarg of `wrap_logger` and raises TypeError.
It must stay lazy (`structlog.get_logger(**...)`, never `.bind()` on the
proxy), or module-level loggers materialize against structlog's defaults and
bypass the redaction processor.

## Testing

- `tests/conftest.py` **forces** `MOCK_MODE=0` and `MOCK_ERROR_RATE=0.0`,
  because the container inherits `.env`. Tests wanting mock mode use the
  `mock_settings` / `mock_client` fixtures.
- Test emails must not use `.test`, `.invalid`, `.local` or `.localhost` —
  `email-validator` rejects RFC 2606 special-use names. Use `example.com`.
- `integration`-marked tests need live Postgres. `-m "not integration"` for the
  rest.
- Don't add `--maxfail`; the whole run takes seconds and one root cause usually
  explains a dozen failures.

## Style

Write the whole thing — no stubs to fill in later, unless the stage plan says
skeleton. Call out explicitly when a shortcut is fine versus when it's debt
worth recording in the plan's §5 ledger.
