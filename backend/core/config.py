"""Application settings.

Every value comes from the environment. There are no literals in this file that
a deployment would need to change, and nothing here reads a secret from disk
outside of `.env`.

Two deliberate choices worth knowing about:

1. `cors_origins` and friends are declared as `str`, not `list[str]`. pydantic-
   settings tries to JSON-decode complex types before the field validator runs,
   so a plain comma-separated env value like `a,b` raises a parse error rather
   than reaching your validator. Keeping the raw string and exposing a parsed
   property sidesteps that entirely.

2. `check_production_safety()` is called from the app factory, not from the
   model validator, so that tests and scripts can build a Settings object with
   development defaults without tripping the production guardrails.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value shipped in .env.example. If this reaches production, the app refuses
# to start rather than signing tokens with a public secret.
PLACEHOLDER_JWT_SECRET = "change-me-openssl-rand-hex-32"

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Environment(StrEnum):
    development = "development"
    test = "test"
    production = "production"


class RelationBackend(StrEnum):
    """Which relation extractor the pipeline uses.

    Set by the hour-9 decision gate (docs/03-BACKEND-PLAN.md §4 Stage 3). Both
    implement the same `RelationModel` protocol, including edge masking, so the
    ablation endpoint is unaffected by the choice.
    """

    gat = "gat"
    rules = "rules"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── runtime ──────────────────────────────────────────────────────────────
    environment: Environment = Environment.development
    log_level: str = "INFO"
    pipeline_seed: int = 20260919

    # ── database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://countersign:countersign@localhost:5432/countersign"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_echo: bool = False
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)

    # ── auth ─────────────────────────────────────────────────────────────────
    jwt_secret: str = PLACEHOLDER_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "countersign"
    jwt_audience: str = "countersign-web"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    refresh_token_ttl_seconds: int = Field(default=604_800, ge=3_600)
    refresh_cookie_name: str = "cs_refresh"
    refresh_cookie_secure: bool = False
    refresh_cookie_path: str = "/api/v1/auth"
    login_max_attempts: int = Field(default=5, ge=1, le=50)
    login_lockout_minutes: int = Field(default=15, ge=1)
    # Login responses are padded to this wall-clock duration regardless of
    # whether the email exists. Prevents user enumeration by timing.
    login_timing_target_ms: int = Field(default=250, ge=0, le=5_000)
    password_min_length: int = Field(default=12, ge=8)
    common_passwords_path: str = "data/common_passwords.txt"

    # ── cors ─────────────────────────────────────────────────────────────────
    # Comma-separated. Explicit origins only; `*` is incompatible with
    # credentialed requests and is rejected below.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ── nemotron ─────────────────────────────────────────────────────────────
    nemotron_api_key: str = ""
    nemotron_base_url: str = "https://integrate.api.nvidia.com/v1"
    nemotron_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1"
    nemotron_timeout_seconds: float = Field(default=6.0, gt=0)
    nemotron_max_retries: int = Field(default=2, ge=0, le=5)
    nemotron_max_concurrency: int = Field(default=6, ge=1, le=32)
    nemotron_force_fail: bool = False
    # The single citation sentence is the only document-derived text that ever
    # reaches the prompt, and it is truncated to this many characters.
    nemotron_max_citation_chars: int = Field(default=400, ge=80, le=2_000)

    # ── elevenlabs ───────────────────────────────────────────────────────────
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_timeout_seconds: float = Field(default=8.0, gt=0)
    voice_fallback_audio: str = "data/fixtures/audio/fallback_briefing.mp3"
    voice_fallback_transcript: str = "data/fixtures/voice.fallback.json"
    audio_dir: str = "data/audio"
    audio_url_ttl_seconds: int = Field(default=600, ge=30)

    # ── pipeline tuning ──────────────────────────────────────────────────────
    relation_model: RelationBackend = RelationBackend.gat
    vacuity_gate_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    ablation_load_bearing_delta: float = Field(default=0.10, ge=0.0, le=1.0)
    entity_coref_threshold: float = Field(default=0.86, ge=0.0, le=1.0)
    checkpoint_dir: str = "ml/checkpoints"
    parse_cache_dir: str = "data/cache/parses"

    # ── upload limits ────────────────────────────────────────────────────────
    max_upload_bytes: int = Field(default=2_097_152, ge=1_024)
    max_upload_files: int = Field(default=50, ge=1, le=500)
    # Aggregate cap across a whole multipart request. The per-file cap alone
    # does not stop 50 files x 2 MB from arriving at once.
    max_request_bytes: int = Field(default=110_100_480, ge=1_024)
    max_pdf_pages: int = Field(default=30, ge=1, le=2_000)
    # A 2 MB PDF can extract to far more text than a 2 MB .txt file. That
    # asymmetry is the actual DoS vector, so the char ceiling is separate.
    max_pdf_chars: int = Field(default=400_000, ge=1_000)
    pdf_timeout_seconds: float = Field(default=10.0, gt=0)

    # ── mock mode (frontend unblocking, brief §12) ───────────────────────────
    mock_mode: bool = False
    mock_latency_min_ms: int = Field(default=80, ge=0)
    mock_latency_max_ms: int = Field(default=400, ge=0)
    mock_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    fixtures_dir: str = "data/fixtures"

    # ── static serving (single-origin prod build) ────────────────────────────
    serve_static: bool = False
    static_dir: str = "/app/static"

    # ── validators ───────────────────────────────────────────────────────────

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        level = v.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {v!r}")
        return level

    @field_validator("jwt_algorithm")
    @classmethod
    def _symmetric_algorithm_only(cls, v: str) -> str:
        # We mint and verify with the same key. An asymmetric alg here would
        # silently mean "verify with the HMAC secret as a public key", which is
        # a known JWT footgun.
        if v not in {"HS256", "HS384", "HS512"}:
            raise ValueError(f"JWT_ALGORITHM must be HS256/HS384/HS512, got {v!r}")
        return v

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, v: str) -> str:
        if "*" in v:
            raise ValueError(
                "CORS_ORIGINS cannot contain '*'. Wildcard origins are incompatible "
                "with allow_credentials=True, which we need for the refresh cookie."
            )
        return v

    @field_validator("mock_latency_max_ms")
    @classmethod
    def _latency_window_ordered(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        lo = info.data.get("mock_latency_min_ms", 0)
        if v < lo:
            raise ValueError("MOCK_LATENCY_MAX_MS must be >= MOCK_LATENCY_MIN_MS")
        return v

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.production

    @property
    def is_test(self) -> bool:
        return self.environment is Environment.test

    def path(self, value: str) -> Path:
        """Resolve a configured path relative to the backend root if relative."""
        p = Path(value)
        return p if p.is_absolute() else (BACKEND_ROOT / p)

    @property
    def sqlalchemy_connect_args(self) -> dict[str, str]:
        # A runaway eval aggregation should die, not hold a connection open
        # until the demo ends.
        return {"options": f"-c statement_timeout={self.db_statement_timeout_ms}"}

    # ── startup guardrails ───────────────────────────────────────────────────

    def check_production_safety(self) -> list[str]:
        """Return a list of fatal misconfigurations. Empty list means safe.

        Called by the app factory. Kept out of the validators so that tests and
        offline scripts can construct Settings freely.
        """
        problems: list[str] = []
        if not self.is_production:
            return problems

        if self.jwt_secret == PLACEHOLDER_JWT_SECRET:
            problems.append("JWT_SECRET is still the .env.example placeholder")
        if len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be at least 32 characters (openssl rand -hex 32)")
        if not self.refresh_cookie_secure:
            problems.append("REFRESH_COOKIE_SECURE must be true in production")
        if not self.cors_origin_list:
            problems.append("CORS_ORIGINS must list at least one explicit origin")
        if self.mock_mode:
            problems.append("MOCK_MODE must be 0 in production — it serves fixtures, not real data")
        if self.mock_error_rate > 0:
            problems.append("MOCK_ERROR_RATE must be 0 in production")
        if self.db_echo:
            problems.append("DB_ECHO must be false in production — it logs full SQL including values")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that `Depends(get_settings)` does not re-read `.env` per request.
    Tests clear the cache via the `settings` fixture in tests/conftest.py.
    """
    return Settings()


def fatal_config_exit(problems: list[str]) -> None:
    """Print misconfigurations to stderr and exit non-zero.

    Deliberately not a logger call: this runs before logging is configured and
    the operator needs to see it in `docker logs` without a JSON decoder.
    """
    print("FATAL: refusing to start with an unsafe configuration:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    raise SystemExit(78)  # EX_CONFIG
