"""Logging configuration and secret redaction.

This module shipped with no tests, and it cost four minutes of a 24-hour build:
`structlog.stdlib.add_logger_name` reads `logger.name`, `PrintLogger` has no
such attribute, and the failure surfaced only on the first `log.info` call —
which happens inside the lifespan, so the container refused to start.

Two things are covered here. First, that a log call at every level actually
emits, which is the cheapest possible guard against a mis-paired
processor/factory combination. Second, redaction — which is the part that
genuinely matters, because document text, `Authorization` headers and both
upstream API keys all pass through this process.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

from core.logging import (
    REDACTED,
    clear_request_context,
    configure_logging,
    get_logger,
    get_request_id,
    new_request_id,
    redact_processor,
    set_request_id,
)


@pytest.fixture(autouse=True)
def _reset_structlog() -> Any:
    """Reconfigure per test and clear context, so ordering cannot matter."""
    yield
    clear_request_context()
    structlog.reset_defaults()


def emit_and_capture(
    capsys: pytest.CaptureFixture[str], level: str = "INFO", **fields: Any
) -> dict[str, Any]:
    """Configure logging, emit one event, and return the parsed JSON line."""
    configure_logging(level=level, json_output=True)
    log = get_logger("tests.test_logging")
    log.info("test_event", **fields)
    captured = capsys.readouterr().out.strip().splitlines()
    assert captured, "nothing was written to stdout"
    return json.loads(captured[-1])


# ── the bugs that broke startup ──────────────────────────────────────────────


def test_get_logger_accepts_a_name_without_colliding() -> None:
    """`get_logger` must not pass a reserved keyword to structlog.

    `structlog.get_logger(**initial_values)` forwards into
    `wrap_logger(logger, ...)`, so binding the module name under the key
    `logger` raises `TypeError: got multiple values for argument 'logger'`.
    Because every module calls `get_logger(__name__)` at import time, that
    error surfaces as an ImportError during test collection and nothing runs
    at all — so it is worth asserting on its own, ahead of any log call.
    """
    logger = get_logger("tests.naming")
    assert logger is not None
    assert get_logger() is not None


def test_a_log_call_actually_emits(capsys: pytest.CaptureFixture[str]) -> None:
    """The regression test for the startup failure.

    Any processor that assumes a stdlib logger while the factory produces a
    `PrintLogger` raises on the first call, not at configuration time. This
    would have caught it in milliseconds.
    """
    event = emit_and_capture(capsys)
    assert event["event"] == "test_event"


@pytest.mark.parametrize("method", ["debug", "info", "warning", "error", "critical"])
def test_every_level_emits(capsys: pytest.CaptureFixture[str], method: str) -> None:
    configure_logging(level="DEBUG", json_output=True)
    log = get_logger("tests.test_logging")
    getattr(log, method)("level_probe")
    assert capsys.readouterr().out.strip(), f"log.{method} produced no output"


def test_logger_name_is_bound(capsys: pytest.CaptureFixture[str]) -> None:
    """The field `add_logger_name` used to provide, now bound in `get_logger`."""
    event = emit_and_capture(capsys)
    assert event["logger"] == "tests.test_logging"


def test_standard_fields_are_present(capsys: pytest.CaptureFixture[str]) -> None:
    event = emit_and_capture(capsys)
    assert event["level"] == "info"
    # ISO-8601, UTC.
    assert event["timestamp"].endswith("Z") or "+00:00" in event["timestamp"]


def test_module_level_loggers_pick_up_later_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every module does `log = get_logger(__name__)` at import time, long
    before the lifespan calls `configure_logging`. If `get_logger` returned an
    eagerly-materialized logger, those would all cache structlog's defaults and
    silently bypass redaction."""
    log = get_logger("tests.early")  # created BEFORE configuration
    configure_logging(level="INFO", json_output=True)
    log.info("late_config", api_key="nvapi-abcdefghijklmnop")

    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["logger"] == "tests.early"
    assert event["api_key"] == REDACTED, "a pre-configuration logger skipped redaction"


def test_console_renderer_path_also_emits(capsys: pytest.CaptureFixture[str]) -> None:
    """`json_output=False` swaps in ConsoleRenderer. Exercised because it is
    the path a developer watching a terminal actually uses."""
    configure_logging(level="DEBUG", json_output=False)
    get_logger("tests.console").info("human_readable")
    assert "human_readable" in capsys.readouterr().out


# ── redaction ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "passwd",
        "secret",
        "jwt_secret",
        "token",
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "auth_header",
        "cookie",
        "cs_refresh",
        "credential",
        "private_key",
        "session_id",
        "jwt",
        # Substring matching, case-insensitive — deliberately broad.
        "Authorization",
        "NEMOTRON_API_KEY",
        "user_password_hash",
    ],
)
def test_sensitive_keys_are_redacted(key: str) -> None:
    out = redact_processor(None, "info", {"event": "e", key: "super-secret-value"})
    assert out[key] == REDACTED, f"{key!r} was not redacted"


@pytest.mark.parametrize(
    "value",
    [
        "Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghij",
        "Authorization: Bearer abcdefghijklmnop",
        "nvapi-0123456789abcdef",
        "sk_0123456789abcdef",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9xxxx",
    ],
)
def test_credentials_inside_values_are_redacted(value: str) -> None:
    """The case that actually leaks in practice.

    An HTTP client's exception message echoes the request it made, including
    headers, and lands in a log line under an innocent key like `error`.
    """
    out = redact_processor(None, "info", {"event": "upstream_failed", "error": value})
    assert REDACTED in out["error"]
    assert "eyJ" not in out["error"] or out["error"].count(REDACTED) >= 1


def test_redaction_recurses_into_nested_structures() -> None:
    event = {
        "event": "request",
        "payload": {
            "user": {"email": "a@b.test", "password": "hunter2"},
            "headers": [{"authorization": "Bearer xyzxyzxyzxyz"}],
        },
    }
    out = redact_processor(None, "info", event)
    assert out["payload"]["user"]["password"] == REDACTED
    assert out["payload"]["headers"][0]["authorization"] == REDACTED
    # Non-sensitive values survive, or the logs become useless.
    assert out["payload"]["user"]["email"] == "a@b.test"


def test_redaction_preserves_container_types() -> None:
    out = redact_processor(None, "info", {"event": "e", "items": ["a", "b"], "pair": ("x", "y")})
    assert isinstance(out["items"], list)
    assert isinstance(out["pair"], tuple)


def test_redaction_is_depth_limited() -> None:
    """Guards against a cyclic or absurdly deep structure hanging the logger."""
    nested: dict[str, Any] = {"event": "e"}
    cursor = nested
    for _ in range(20):
        child: dict[str, Any] = {}
        cursor["next"] = child
        cursor = child
    out = redact_processor(None, "info", nested)
    assert out  # returned rather than recursing forever


def test_redaction_leaves_ordinary_scalars_alone() -> None:
    out = redact_processor(
        None, "info", {"event": "e", "count": 42, "ratio": 0.5, "ok": True, "none": None}
    )
    assert out["count"] == 42
    assert out["ratio"] == 0.5
    assert out["ok"] is True
    assert out["none"] is None


def test_redacted_secret_never_appears_in_rendered_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: the secret must not survive to stdout.

    Asserting on the processor alone would miss a renderer that re-serializes
    the original event dict.
    """
    event = emit_and_capture(capsys, jwt_secret="tops3cret-value-do-not-log")
    assert "tops3cret" not in json.dumps(event)
    assert event["jwt_secret"] == REDACTED


# ── request correlation ──────────────────────────────────────────────────────


def test_request_id_is_a_26_char_crockford_ulid() -> None:
    request_id = new_request_id()
    assert len(request_id) == 26
    # Crockford base32 excludes I, L, O and U so an id read aloud during a demo
    # cannot be misheard.
    assert not (set(request_id) & set("ILOU"))
    assert set(request_id) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_request_ids_sort_chronologically() -> None:
    """Timestamp-prefixed, so grepping "what happened just before this" works.

    The guarantee is across milliseconds, not within one: the low 80 bits are
    random, so two ids minted in the same millisecond order arbitrarily.
    Asserting `first <= second` on back-to-back calls made this test a coin
    flip that failed roughly half the time. Sleeping past a millisecond
    boundary tests the property the docstring actually claims.
    """
    import time as _time

    first = new_request_id()
    _time.sleep(0.002)
    second = new_request_id()

    assert first < second
    # The timestamp is the first 48 bits, which is the first 10 base32 chars.
    assert first[:10] <= second[:10]


def test_request_id_appears_on_every_log_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole point of the contextvar: correlation without threading an
    argument through every signature, including into threadpool handlers."""
    configure_logging(level="INFO", json_output=True)
    request_id = new_request_id()
    set_request_id(request_id)

    get_logger("tests.correlated").info("inside_request")
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["request_id"] == request_id
    assert get_request_id() == request_id


def test_clearing_context_removes_the_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_output=True)
    set_request_id(new_request_id())
    clear_request_context()

    get_logger("tests.cleared").info("outside_request")
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "request_id" not in event
    assert get_request_id() is None
