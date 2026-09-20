# COUNTERSIGN

Financial risk analysis that traces every claim to the exact sentence it came from.

Classical neural extraction with no generative text, calibrated uncertainty validated against adversarial perturbation, LLM escalation only on the cases the classical model admits it doesn't understand, causally verified explanations, and a spoken briefing.

**SteelHacks XIII · Sept 19–20, 2026**

---

## Setup

Requires Docker Desktop (or Docker Engine + Compose v2). Nothing else — no local Python, no local Node.

```bash
git clone <this repo> && cd countersign
make init
```

That copies `.env.example` → `.env`, builds both images, waits for Postgres, and runs migrations.

```
backend   http://localhost:8000/api/v1/docs
frontend  http://localhost:5173
postgres  localhost:5432   (user/pass/db: countersign)
```

Put your `NEMOTRON_API_KEY` and `ELEVENLABS_API_KEY` in `.env`. Everything else has a working default.

### Frontend-only, before the backend exists

The frontend never needs to wait on the pipeline:

```bash
# in .env
MOCK_MODE=1
MOCK_ERROR_RATE=0.1

make restart
```

Fixtures are served with 80–400ms of simulated latency and a 10% injected error rate, so loading and error states get built from the first commit instead of retrofitted. See `backend/data/fixtures/README.md`.

---

## Commands

`make help` lists everything. The ones you'll actually use:

| | |
|---|---|
| `make up` / `make down` | start / stop |
| `make logs-be` / `make logs-fe` | follow one service |
| `make shell-be` / `make shell-fe` | shell into a container |
| `make db` | psql |
| `make migrate` | apply migrations |
| `make migration m="..."` | autogenerate a migration |
| `make seed s=meridian_shell_ring` | load a demo scenario |
| `make types` | regenerate frontend types from the live OpenAPI schema |
| `make check` | lint + typecheck + test, same as CI |
| `make prod` | production stack, single origin on `:8000` |
| `make nuke` | stop and wipe volumes |

---

## Layout

```
├── backend/                 Python 3.11 · FastAPI · SQLAlchemy 2 · PyTorch
│   ├── api/v1/              one router per pipeline stage
│   ├── core/                config, security, rate limiting
│   ├── ml/                  tagger · parser · relations · evidential · fuzzer · cascade · ablation
│   ├── db/                  models + alembic migrations
│   ├── data/synth/          synthetic document generators
│   ├── data/fixtures/       mock API payloads (frontend unblocking)
│   ├── workers/             ingest orchestration
│   └── tests/
│
├── frontend/                React 18 · TypeScript strict · Vite · Tailwind
│   ├── src/api/             client, interceptor, generated schema.d.ts
│   ├── src/components/      primitives/ and domain/
│   ├── src/routes/          one folder per route
│   ├── src/hooks/
│   └── src/styles/          tokens.css is the source of truth for colour
│
├── docker/postgres/init/    extensions, run once on first boot
├── .github/workflows/       ci · security · release
└── docs/                    the three briefs
```

---

## CI

Three workflows, all validated and ready.

**`ci.yml`** — path-filtered so backend changes don't run the frontend job and vice versa. Backend: ruff, mypy, pytest against a real pgvector service container with extensions pre-created. Frontend: eslint, prettier, tsc, vitest, build, plus a bundle-size table written to the run summary. Docker: builds both prod images with GHA layer caching and smoke-tests that `/health` actually comes up, printing backend logs if it doesn't.

Slow tests are excluded in CI via `-m "not slow and not ml"`. The fuzzer and any training run locally — a 24-hour build cannot afford a 15-minute pipeline. `mypy` and `alembic upgrade` are `continue-on-error` on a fresh repo; flip both to blocking once there's code and a migration.

**`security.yml`** — gitleaks on full history, `pip-audit`, `npm audit`, and a `banned-patterns` job that turns the brief's hard rules into build failures:

- `localStorage` / `sessionStorage` anywhere in `frontend/src`
- `dangerouslySetInnerHTML` anywhere in `frontend/src`
- f-string SQL in `backend/`
- committed `sk_…` or `nvapi-…` keys
- a tracked `.env`

The same three rules are enforced at edit time by `eslint.config.js` and `ruff` (bandit `S` rules are on), so you find out before you push.

**`release.yml`** — builds and pushes both images to GHCR on main and on `v*.*.*` tags. "Here's a container you can pull and run" is a better answer to a judge than "clone it and install."

---

## Production stack

```bash
make prod    # → http://localhost:8000
```

The frontend builds to static files, gets copied into a shared volume, and is served by the FastAPI container. One origin, no CORS, no separate web server. Backend runs as UID 10001 non-root with two uvicorn workers and a healthcheck.

`frontend/nginx.conf` and the `serve` stage exist if you'd rather run the frontend standalone, but the single-origin path is the one to demo.

---

## Conventions

**API contract lives in `docs/01-BACKEND-BRIEF.md`.** If the frontend and backend disagree, that file wins. When it changes, say so loudly in the PR and re-run `make types`.

**Never hand-write API types.** `make types` generates `frontend/src/api/schema.d.ts` from the live OpenAPI schema. Hand-written types drift and cost hours.

**Never hardcode a colour.** `frontend/src/styles/tokens.css` is the source of truth; Tailwind reads the same custom properties, so SVG, D3, and canvas all stay in sync. The `--stamp-*` values mean routing severity and nothing else — not a delete button, not a form error.

**Access tokens live in memory.** The refresh token is an HttpOnly cookie the frontend cannot read and must not try to. Browser storage is banned and CI enforces it.

**No API key reaches the browser.** ElevenLabs and Nemotron are called server-side only. If a task seems to need a key client-side, the design is wrong.

**Synthetic and public data only.** No real account numbers, credentials, or financial records anywhere in this repo. Both the Compound and Xtract tracks have that as a hard rule.

---

## First hour

1. `make init`, confirm both URLs load.
2. Drop the three `woff2` files into `frontend/public/fonts/` (see the README there).
3. Backend: `db/models.py`, then uncomment the two `target_metadata` lines in `db/migrations/env.py`, then `make migration m="initial schema"`.
4. Frontend: build the primitive layer and `src/styles` first — both devs compose from it, and getting it right together is what keeps the two halves looking like one product.
5. Backend: publish fixtures by hour 3. That's the hard dependency for both frontend devs.

Build order, ownership split, and per-component definitions of done are in `docs/`.
