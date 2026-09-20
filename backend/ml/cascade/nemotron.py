"""The Nemotron client. Brief §14, plan §1.9.

Five properties, in the order they matter:

**It never blocks the pipeline.** 6-second timeout, two retries with jittered
backoff, then a hard fail that the caller turns into the classical decision
with `degraded: true`. An upstream outage costs an insight its second
opinion; it does not cost the demo its ingest.

**A parse failure is an upstream failure.** Malformed JSON falls back exactly
like a timeout. There is no path where we coax a usable answer out of
something we could not read.

**Calls are parallel with a semaphore of 6** (plan §1.9). Twenty-eight
escalations at ~800 ms sequential is 22 seconds, inside a 30-second ingest
budget that also has to do all the ML. Not survivable; six at a time brings
it to about four.

**Responses are cached by prompt digest.** The call budget is finite, a
rehearsal should not spend it, and a demo on bad wifi should replay
yesterday's decisions rather than degrade. Deterministic input, deterministic
key.

**Every call is logged**, including the failures. `nemotron_runs` is the
table the Beyond the Chatbot judges will actually open, and a table that only
contains successes is a table that is lying by omission.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from core.config import Settings, get_settings
from core.logging import get_logger
from ml.cascade import prompt as prompting
from ml.cascade.schema import MalformedResponse, NemotronDecision, parse_decision

log = get_logger(__name__)

CHAT_COMPLETIONS = "/chat/completions"

# Deterministic decisions. Temperature 0 is not a guarantee of reproducibility
# from a hosted model, but it is the strongest available signal that we want
# the same answer twice, and it makes the cache honest.
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 220

# Retry budget. Jitter is full-random rather than a fixed multiplier so a
# batch of six concurrent calls does not retry in lockstep.
BACKOFF_BASE_SECONDS = 0.4
BACKOFF_MAX_SECONDS = 2.0


class NemotronUnavailable(RuntimeError):
    """The upstream did not produce a usable decision.

    Raised for every failure mode — no key, timeout, HTTP error, malformed
    body — because the caller's response is the same in all of them: use the
    classical decision and mark the insight degraded. Distinguishing them
    here would be a distinction the cascade cannot act on; the *reason* is
    carried on the exception and logged.
    """

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class NemotronRequest:
    """One insight's worth of prompt."""

    insight_id: Any
    context: prompting.PromptContext

    def render(self, settings: Settings) -> tuple[str, str, str]:
        user = prompting.build(
            self.context, max_citation_chars=settings.nemotron_max_citation_chars
        )
        return (
            prompting.SYSTEM_PROMPT,
            user,
            prompting.digest(prompting.SYSTEM_PROMPT, user),
        )


@dataclass(frozen=True, slots=True)
class NemotronResult:
    insight_id: Any
    decision: NemotronDecision
    prompt_sha: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    cached: bool = False


class NemotronClient:
    """Async client for the NVIDIA-hosted Nemotron endpoint.

    `transport` is injectable so the whole path — retries, backoff, parsing,
    caching, logging — is exercised in tests against `httpx.MockTransport`
    without a key and without a network. That is the only honest way to test
    an integration whose credential we do not have.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self._semaphore = asyncio.Semaphore(self.settings.nemotron_max_concurrency)

    # ── availability ─────────────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """A key, or an injected transport standing in for one."""
        return bool(self.settings.nemotron_api_key) or self._transport is not None

    def _guard(self) -> None:
        if self.settings.nemotron_force_fail:
            # The Stage 10 drill. Deliberately not `retryable`: the point is
            # to exercise the degraded path, not to spend three attempts
            # getting there.
            raise NemotronUnavailable("NEMOTRON_FORCE_FAIL is set", retryable=False)
        if not self.configured:
            raise NemotronUnavailable("no NEMOTRON_API_KEY configured", retryable=False)

    # ── cache ────────────────────────────────────────────────────────────────

    def _cache_path(self, prompt_sha: str) -> Path:
        directory = self.settings.path(self.settings.nemotron_cache_dir)
        return directory / f"{prompt_sha}.json"

    def _cached(self, prompt_sha: str) -> NemotronDecision | None:
        if not self.settings.nemotron_cache_enabled:
            return None
        try:
            payload = json.loads(self._cache_path(prompt_sha).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        try:
            return NemotronDecision.model_validate(payload["decision"])
        except Exception:
            return None

    def _store(self, prompt_sha: str, decision: NemotronDecision, usage: dict[str, Any]) -> None:
        if not self.settings.nemotron_cache_enabled:
            return
        path = self._cache_path(prompt_sha)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "decision": decision.model_dump(),
                        "usage": usage,
                        "model": self.settings.nemotron_model,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - read-only volume
            log.warning("nemotron_cache_write_failed", error=str(exc))

    # ── one call ─────────────────────────────────────────────────────────────

    async def decide(self, request: NemotronRequest) -> NemotronResult:
        system, user, prompt_sha = request.render(self.settings)

        cached = self._cached(prompt_sha)
        if cached is not None:
            return NemotronResult(
                insight_id=request.insight_id,
                decision=cached,
                prompt_sha=prompt_sha,
                latency_ms=0,
                input_tokens=None,
                output_tokens=None,
                cached=True,
            )

        self._guard()

        started = time.monotonic()
        attempts = self.settings.nemotron_max_retries + 1
        last: NemotronUnavailable | None = None

        for attempt in range(attempts):
            try:
                decision, usage = await self._call(system, user)
            except NemotronUnavailable as exc:
                last = exc
                if not exc.retryable or attempt == attempts - 1:
                    break
                await asyncio.sleep(_backoff(attempt))
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            self._store(prompt_sha, decision, usage)
            return NemotronResult(
                insight_id=request.insight_id,
                decision=decision,
                prompt_sha=prompt_sha,
                latency_ms=latency_ms,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            )

        raise last or NemotronUnavailable("exhausted retries")

    async def _call(self, system: str, user: str) -> tuple[NemotronDecision, dict[str, Any]]:
        payload = {
            "model": self.settings.nemotron_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_OUTPUT_TOKENS,
            # Asked for as well as instructed. Nemotron's OpenAI-compatible
            # surface honours this; if a deployment does not, the fence
            # stripping in `schema.parse_decision` still handles it.
            "response_format": {"type": "json_object"},
            # Nemotron 3 Super reasons by default: it emits a chain-of-thought
            # into `reasoning_content` before the actual answer in `content`.
            # `MAX_OUTPUT_TOKENS` (220) is sized for the JSON answer alone —
            # with reasoning on, the trace consumes the whole budget and the
            # response gets cut off mid-thought, never reaching the JSON
            # (surfaces here as "no JSON object in '<truncated reasoning>'").
            # It also explains the occasional timeout: a longer trace on a
            # harder prompt just takes longer to generate. `enable_thinking:
            # False` skips the trace entirely; `force_nonempty_content: True`
            # is a documented pair with it — if disabling thinking ever still
            # left `content` empty, this backfills it from whatever reasoning
            # happened rather than handing back nothing. This is a top-level
            # field in the raw request body, not nested under an `extra_body`
            # wrapper — that wrapper is an OpenAI-SDK client-side convention
            # for forwarding unknown params and doesn't exist on the wire.
            "chat_template_kwargs": {
                "enable_thinking": False,
                "force_nonempty_content": True,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.nemotron_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(
            base_url=self.settings.nemotron_base_url,
            timeout=self.settings.nemotron_timeout_seconds,
            transport=self._transport,
        ) as client:
            try:
                response = await client.post(CHAT_COMPLETIONS, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise NemotronUnavailable(
                    f"timeout after " f"{self.settings.nemotron_timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                raise NemotronUnavailable(f"transport error: {exc}") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise NemotronUnavailable(f"upstream returned {response.status_code}")
        if response.status_code >= 400:
            # 4xx other than rate limiting is our bug — a bad key, a bad
            # model name. Retrying would not fix it and would spend budget.
            raise NemotronUnavailable(
                f"upstream rejected the request with {response.status_code}",
                retryable=False,
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise NemotronUnavailable(f"unreadable response envelope: {exc}") from exc

        try:
            decision = parse_decision(content)
        except MalformedResponse as exc:
            # Brief §14: a parse failure counts as an upstream failure. It is
            # retryable because a second sample often is parseable.
            raise NemotronUnavailable(f"malformed decision: {exc}") from exc

        return decision, dict(body.get("usage") or {})

    # ── many calls ───────────────────────────────────────────────────────────

    async def decide_many(
        self, requests: list[NemotronRequest]
    ) -> list[NemotronResult | NemotronUnavailable]:
        """Results in request order, with failures in place rather than raised.

        One failed call must not cancel the other twenty-seven, so this
        gathers exceptions instead of propagating them and the caller decides
        per insight.
        """

        async def one(request: NemotronRequest) -> NemotronResult | NemotronUnavailable:
            async with self._semaphore:
                try:
                    return await self.decide(request)
                except NemotronUnavailable as exc:
                    return exc

        return list(await asyncio.gather(*(one(request) for request in requests)))


def _backoff(attempt: int) -> float:
    """Full-jitter exponential backoff.

    Full jitter rather than a fixed multiplier: six concurrent calls that
    fail together would otherwise retry in lockstep and hit the same rate
    limit again.
    """
    ceiling = min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_MAX_SECONDS)
    return random.uniform(0.0, ceiling)  # noqa: S311 - jitter, not cryptography
