"""Configuration guardrails and log redaction.

Two failure modes this guards against, both of which have shipped in real
products: signing production tokens with the placeholder secret from the
example env file, and writing a bearer token into the application logs.
"""

from __future__ import annotations

import pytest

from core.config import PLACEHOLDER_JWT_SECRET, Environment, Settings
from core.logging import REDACTED, new_request_id, redact_processor


def _production(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": Environment.production,
        "jwt_secret": "f" * 64,
        "refresh_cookie_secure": True,
        "cors_origins": "https://countersign.example",
        "mock_mode": False,
        "mock_error_rate": 0.0,
        "db_echo": False,
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


# ── production guardrails ────────────────────────────────────────────────────


def test_clean_production_config_passes() -> None:
    assert _production().check_production_safety() == []


def test_development_config_is_never_blocked() -> None:
    """Dev defaults must not trip the guardrails, or nobody can run the app."""
    dev = Settings(environment=Environment.development, jwt_secret=PLACEHOLDER_JWT_SECRET)
    assert dev.check_production_safety() == []


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"jwt_secret": PLACEHOLDER_JWT_SECRET}, "placeholder"),
        ({"jwt_secret": "too-short"}, "at least 32"),
        ({"refresh_cookie_secure": False}, "REFRESH_COOKIE_SECURE"),
        ({"cors_origins": ""}, "CORS_ORIGINS"),
        ({"mock_mode": True}, "MOCK_MODE"),
        ({"mock_error_rate": 0.1}, "MOCK_ERROR_RATE"),
        ({"db_echo": True}, "DB_ECHO"),
    ],
)
def test_production_rejects_unsafe_values(
    overrides: dict[str, object], expected_fragment: str
) -> None:
    problems = _production(**overrides).check_production_safety()
    assert any(expected_fragment in p for p in problems), problems


def test_wildcard_cors_origin_is_rejected_outright() -> None:
    """Not a warning: `*` is incompatible with allow_credentials=True."""
    with pytest.raises(ValueError, match=r"\*"):
        Settings(cors_origins="*")


def test_asymmetric_jwt_algorithm_is_rejected() -> None:
    """RS256 with an HMAC secret is a known JWT footgun."""
    with pytest.raises(ValueError, match="HS256"):
        Settings(jwt_algorithm="RS256")


def test_bad_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        Settings(log_level="CHATTY")


def test_inverted_mock_latency_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="MOCK_LATENCY_MAX_MS"):
        Settings(mock_latency_min_ms=500, mock_latency_max_ms=100)


# ── derived values ───────────────────────────────────────────────────────────


def test_cors_origins_parsed_and_normalized() -> None:
    settings = Settings(cors_origins="http://a.test/, http://b.test ,")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_statement_timeout_reaches_the_connection() -> None:
    settings = Settings(db_statement_timeout_ms=9_000)
    assert "statement_timeout=9000" in settings.sqlalchemy_connect_args["options"]


def test_relative_paths_resolve_under_the_backend_root() -> None:
    settings = Settings()
    assert settings.path("data/fixtures").is_absolute()
    assert settings.path("/tmp/absolute").as_posix() == "/tmp/absolute"


# ── log redaction ────────────────────────────────────────────────────────────


def test_sensitive_keys_are_redacted() -> None:
    event = {
        "event": "login",
        "password": "hunter22hunter22",
        "api_key": "nvapi-abcdef123456",
        "authorization": "Bearer abc.def.ghi",
        "email": "ops@meridian.example",
    }
    scrubbed = redact_processor(None, "info", event)  # type: ignore[arg-type]
    assert scrubbed["password"] == REDACTED
    assert scrubbed["api_key"] == REDACTED
    assert scrubbed["authorization"] == REDACTED
    # Non-secrets survive, or the logs become useless.
    assert scrubbed["email"] == "ops@meridian.example"
    assert scrubbed["event"] == "login"


def test_nested_secrets_are_redacted() -> None:
    event = {"event": "upstream", "request": {"headers": {"Authorization": "Bearer xyz"}}}
    scrubbed = redact_processor(None, "info", event)  # type: ignore[arg-type]
    assert scrubbed["request"]["headers"]["Authorization"] == REDACTED


def test_secrets_inside_values_are_redacted() -> None:
    """A token embedded in an exception message still gets scrubbed."""
    event = {
        "event": "nemotron_failed",
        "error": "401 from https://api/v1 with header Bearer eyJhbGciOiJIUzI1NiJ9abcdefghij",
    }
    scrubbed = redact_processor(None, "info", event)  # type: ignore[arg-type]
    assert "eyJhbGciOiJIUzI1NiJ9" not in scrubbed["error"]
    assert REDACTED in scrubbed["error"]


def test_nvidia_and_elevenlabs_key_shapes_are_redacted() -> None:
    event = {"event": "x", "msg": "keys nvapi-1234567890 and sk_abcdef123456 leaked"}
    scrubbed = redact_processor(None, "info", event)  # type: ignore[arg-type]
    assert "nvapi-1234567890" not in scrubbed["msg"]
    assert "sk_abcdef123456" not in scrubbed["msg"]


def test_redaction_survives_deep_recursion() -> None:
    """A self-referential structure must not hang the logger."""
    deep: dict[str, object] = {"event": "x"}
    node = deep
    for _ in range(20):
        child: dict[str, object] = {}
        node["child"] = child
        node = child
    redact_processor(None, "info", deep)  # type: ignore[arg-type]


# ── request ids ──────────────────────────────────────────────────────────────


def test_request_ids_are_ulids() -> None:
    rid = new_request_id()
    assert len(rid) == 26
    assert set(rid) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    # No I, L, O or U — so an id read aloud during a demo is unambiguous.
    assert not (set(rid) & set("ILOU"))


def test_request_ids_sort_chronologically() -> None:
    """The 48-bit timestamp prefix is what sorts, not the whole id.

    A ULID's low 80 bits are random, so 200 ids minted inside one millisecond
    are NOT sorted as whole strings — the original form of this test only
    passed by accident when a run happened to straddle a millisecond boundary.
    48 bits of timestamp is exactly 10 Crockford base32 characters.
    """
    ids = [new_request_id() for _ in range(200)]
    prefixes = [i[:10] for i in ids]
    assert prefixes == sorted(prefixes), "timestamp prefixes are not monotonic"


def test_request_ids_are_unique() -> None:
    assert len({new_request_id() for _ in range(2_000)}) == 2_000


# ── settings attribute hygiene ───────────────────────────────────────────────


def test_every_settings_attribute_access_exists_on_settings() -> None:
    """Static guard against reading a setting that is not there.

    Pydantic models raise `AttributeError` on unknown attributes at *access*
    time, not at import. A stale `settings.foo` therefore survives every
    import-level check and every type-level glance, then fails at whatever
    moment first touches that line — for `api/main.py` that is application
    startup, which means the container never comes up.

    This walks the source for `settings.<name>` and `self.settings.<name>` and
    asserts each name is a real field, property or method on `Settings`. It
    runs without a database, without pydantic validation, and in milliseconds.
    """
    import ast
    from pathlib import Path as _Path

    backend = _Path(__file__).resolve().parents[1]

    tree = ast.parse((backend / "core" / "config.py").read_text(encoding="utf-8"))
    fields: set[str] = set()
    members: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
                elif isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    members.add(item.name)

    assert fields, "could not parse any fields off Settings — has the class been renamed?"

    # Pydantic's own surface, which is legitimate to call on a Settings object.
    pydantic_api = {
        "model_dump",
        "model_dump_json",
        "model_copy",
        "model_validate",
        "model_fields",
        "model_fields_set",
        "model_config",
    }
    known = fields | members | pydantic_api

    offenders: list[str] = []
    for path in sorted(backend.rglob("*.py")):
        if "migrations/versions" in path.as_posix():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Attribute):
                continue
            base = node.value
            # Only `settings.x` and `<something>.settings.x`. A bare one-letter
            # alias is too loose and produces false hits on unrelated loop
            # variables.
            is_settings = (isinstance(base, ast.Name) and base.id == "settings") or (
                isinstance(base, ast.Attribute) and base.attr == "settings"
            )
            if is_settings and node.attr not in known:
                offenders.append(
                    f"{path.relative_to(backend)}:{node.lineno} -> settings.{node.attr}"
                )

    assert not offenders, "these read attributes that do not exist on Settings:\n  " + "\n  ".join(
        offenders
    )


# ── test environment isolation ───────────────────────────────────────────────


def test_suite_runs_in_live_mode_not_mock_mode(settings: Settings) -> None:
    """The default `settings`/`client` fixtures must be live, never mock.

    `tests/conftest.py` forces this, and the forcing matters: the container
    inherits the developer's .env, and a local `MOCK_MODE=1` puts every
    integration test against canned fixtures instead of the database. The
    failures then look like application bugs rather than configuration bleed,
    which is exactly what happened.
    """
    assert settings.mock_mode is False, (
        "the test suite is in MOCK_MODE — integration tests would assert "
        "against fixtures instead of the database"
    )


def test_suite_does_not_inject_random_errors(settings: Settings) -> None:
    """A non-zero MOCK_ERROR_RATE makes the suite intermittently red.

    Worse than a consistent failure: it produces flakes that get re-run until
    they pass, which trains everyone to ignore the suite.
    """
    assert (
        settings.mock_error_rate == 0.0
    ), f"MOCK_ERROR_RATE is {settings.mock_error_rate} — the suite would fail at random"
