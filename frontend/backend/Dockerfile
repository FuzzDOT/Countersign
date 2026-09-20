# ──────────────────────────────────────────────────────────────────────────────
# COUNTERSIGN backend
#
# Stages:
#   base  — python + system deps, shared by everything
#   deps  — installed python packages (cached layer; only busts on lock change)
#   dev   — live-reload uvicorn, dev tooling included
#   prod  — non-root, no dev tooling, gunicorn/uvicorn workers
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VIRTUALENVS_CREATE=false

# libpq for psycopg, build-essential for any wheel that needs compiling,
# curl for healthchecks, git because some ML packages want it at install time.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      curl \
      git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app


# ── deps ─────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt requirements-dev.txt ./

# CPU-only torch. The CUDA wheels are ~2.5GB and we do not need them —
# the models here are small and run fine on CPU. If you get a GPU box,
# drop the --index-url line.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
      torch==2.5.1 \
 && pip install -r requirements.txt


# ── dev ──────────────────────────────────────────────────────────────────────
FROM deps AS dev

RUN pip install -r requirements-dev.txt
# spaCy model is a runtime download; bake it in so first boot is fast.
RUN python -m spacy download en_core_web_sm
# WordNet, for the adversarial fuzzer's synonym family. nltk.download() at
# runtime is a network call, and the fuzzer dying at hour 12 inside a container
# with no internet is an avoidable disaster. ~10 MB baked in.
RUN python -m nltk.downloader -d /usr/share/nltk_data wordnet omw-1.4
ENV NLTK_DATA=/usr/share/nltk_data

COPY . .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app"]


# ── prod ─────────────────────────────────────────────────────────────────────
FROM deps AS prod

RUN python -m spacy download en_core_web_sm
RUN python -m nltk.downloader -d /usr/share/nltk_data wordnet omw-1.4
ENV NLTK_DATA=/usr/share/nltk_data

# Non-root. Do this before COPY so ownership is right in one layer.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

COPY --chown=app:app . .

# Writable dirs the app needs at runtime.
RUN mkdir -p /app/data/audio /app/data/cache/parses /app/static \
 && chown -R app:app /app/data /app/static

USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=5 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# 2 workers: the ML models are loaded per-worker and each costs memory.
# Raise only if you've measured that you're request-bound, not memory-bound.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
