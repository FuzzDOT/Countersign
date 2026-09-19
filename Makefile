# COUNTERSIGN — task runner
# Everything you need during the hackathon is in here. `make help` lists it.

SHELL := /bin/bash
COMPOSE := docker compose
COMPOSE_PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml
BE := $(COMPOSE) exec backend
FE := $(COMPOSE) exec frontend

.DEFAULT_GOAL := help
.PHONY: help init up down restart logs logs-be logs-fe ps shell-be shell-fe db \
        migrate migration seed reset-db nuke test test-be test-fe lint lint-be \
        lint-fe fmt typecheck types check prod build clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

# ── lifecycle ────────────────────────────────────────────────────────────────

init: ## First-time setup: copy .env, build images, migrate
	@test -f .env || (cp .env.example .env && echo "created .env — put your API keys in it")
	$(COMPOSE) build
	$(COMPOSE) up -d db
	@echo "waiting for postgres..."
	@until $(COMPOSE) exec -T db pg_isready -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"; do sleep 1; done
	$(COMPOSE) up -d
	@sleep 3
	-$(MAKE) migrate
	@echo ""
	@echo "  backend  → http://localhost:8000/api/v1/docs"
	@echo "  frontend → http://localhost:5173"

up: ## Start all services
	$(COMPOSE) up -d
	@echo "backend → http://localhost:8000/api/v1/docs   frontend → http://localhost:5173"

down: ## Stop all services
	$(COMPOSE) down

restart: ## Restart all services
	$(COMPOSE) restart

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Follow all logs
	$(COMPOSE) logs -f --tail=100

logs-be: ## Follow backend logs
	$(COMPOSE) logs -f --tail=200 backend

logs-fe: ## Follow frontend logs
	$(COMPOSE) logs -f --tail=200 frontend

shell-be: ## Shell into the backend container
	$(BE) bash

shell-fe: ## Shell into the frontend container
	$(FE) sh

db: ## psql into the database
	$(COMPOSE) exec db psql -U countersign -d countersign

# ── database ─────────────────────────────────────────────────────────────────

migrate: ## Apply all migrations
	$(BE) alembic upgrade head

migration: ## Create a migration: make migration m="add insights table"
	@test -n "$(m)" || (echo 'usage: make migration m="message"' && exit 1)
	$(BE) alembic revision --autogenerate -m "$(m)"

seed: ## Load a demo scenario: make seed s=meridian_shell_ring
	$(BE) python -m scripts.seed --scenario $(or $(s),meridian_shell_ring)

reset-db: ## Drop and recreate the schema, then migrate
	$(BE) alembic downgrade base
	$(BE) alembic upgrade head

nuke: ## Stop everything and delete all volumes (fresh start)
	$(COMPOSE) down -v

# ── quality ──────────────────────────────────────────────────────────────────

check: lint typecheck test ## Everything CI runs, locally

test: test-be test-fe ## Run all tests

test-be: ## Backend tests
	$(BE) pytest -q

test-fe: ## Frontend tests
	$(FE) npm run test -- --run

lint: lint-be lint-fe ## Lint everything

lint-be: ## Ruff + format check
	$(BE) ruff check .
	$(BE) ruff format --check .

lint-fe: ## ESLint + prettier check
	$(FE) npm run lint
	$(FE) npm run format:check

fmt: ## Autoformat everything
	$(BE) ruff check --fix .
	$(BE) ruff format .
	$(FE) npm run format

typecheck: ## mypy + tsc
	$(BE) mypy .
	$(FE) npm run typecheck

types: ## Regenerate frontend types from the live OpenAPI schema
	$(FE) npm run gen:types
	@echo "wrote frontend/src/api/schema.d.ts"

# ── production ───────────────────────────────────────────────────────────────

prod: ## Build and run the production stack (single origin on :8000)
	$(COMPOSE_PROD) up -d --build
	@echo "app → http://localhost:8000"

build: ## Build all images
	$(COMPOSE) build

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist frontend/.vite

# ── synthetic data and fixtures ──────────────────────────────────────────────

.PHONY: fixtures fixtures-check corpus

# These run inside the container, like every other target. Running them on the
# host picks up whatever Python is on PATH — Anaconda, typically — with package
# versions that have nothing to do with requirements.txt. `./backend` is
# bind-mounted to /app, so files written in the container appear on the host.
fixtures:  ## Regenerate backend/data/fixtures from the synthetic corpus
	$(BE) python -m scripts.gen_fixtures

fixtures-check:  ## Fail if committed fixtures are stale (used by CI)
	$(BE) python -m scripts.gen_fixtures --check

# ── models ───────────────────────────────────────────────────────────────────

.PHONY: train-tagger train-relations train tune-gate vacuity fuzz eval baseline offsets

train-tagger:  ## Train the BiLSTM-CRF and write ml/checkpoints/tagger.pt (~60s)
	$(BE) python -m scripts.train_tagger

train-relations:  ## Train the GAT and the rule fallback, and apply the hour-9 gate (~2.5min)
	$(BE) python -m scripts.train_relations

train: train-tagger train-relations  ## Retrain everything from the deterministic corpus

tune-gate:  ## Sweep the routing bands and the vacuity gate against the corpus
	$(BE) python -m scripts.tune_gate

vacuity:  ## Does vacuity rise with difficulty band? The claim-2 precondition
	$(BE) python -m scripts.vacuity_report --write

fuzz:  ## Run the adversarial fuzzer and report the fragility correlation
	$(BE) python -m scripts.run_fuzzer

eval:  ## Build the routing eval set from the generator's ground truth
	$(BE) python -m scripts.build_routing_eval

baseline:  ## Run Nemotron on every labeled case (needs NEMOTRON_API_KEY, ~100 calls)
	$(BE) python -m scripts.run_baseline

offsets:  ## Run the offset-integrity suite on its own (the load-bearing invariant)
	$(BE) pytest -q tests/test_offsets.py

corpus:  ## Print a summary of every synthetic scenario
	@for s in meridian_shell_ring clean_baseline invoice_flood train_corpus; do \
		$(BE) python -m data.synth.generate --scenario $$s --report; echo; done
