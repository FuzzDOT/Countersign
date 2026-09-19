"""The structured-output contract with Nemotron. Brief §14.

The model is asked for a single JSON object and nothing else. What comes back
is parsed defensively and validated, and **a parse failure counts as an
upstream failure** — it falls through to the classical decision exactly like a
timeout would. That is the whole discipline: the LLM is a component that can
fail, not an oracle whose output we massage until it fits.

The blast radius is deliberately one enum value. A successful prompt
injection cannot exfiltrate anything, cannot change the citation and cannot
reach the database; the most it can do is pick a wrong bucket on one insight,
which the routing eval would then count against us. `rationale` is stored and
displayed but never parsed as a control signal.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# The three buckets, and nothing else. Matches `db.models.RoutingBucket`.
Decision = Literal["auto_file", "flag_for_review", "escalate_now"]

# Rationale is rendered in the audit table, so it is bounded. A model that
# returns three paragraphs is not more useful, and an unbounded string in a
# table a browser renders is an invitation.
MAX_RATIONALE_CHARS = 600

# ```json ... ``` and friends. Models emit fences despite being told not to,
# and refusing the response over punctuation would spend a retry on nothing.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class MalformedResponse(ValueError):
    """The upstream returned something we cannot read.

    A `ValueError` subclass so callers can catch it alongside `json`'s own
    errors, and named so the log line says what happened rather than
    "JSONDecodeError at position 0".
    """


class NemotronDecision(BaseModel):
    """What a single call is allowed to say."""

    model_config = ConfigDict(extra="ignore")

    decision: Decision
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=MAX_RATIONALE_CHARS)]
    # Optional and advisory. We do not route on it — our own evidential head
    # is the calibrated signal and the LLM's self-reported confidence is not
    # one. Recorded because the audit table is more interesting with it.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def strip_fences(raw: str) -> str:
    match = _FENCE.match(raw)
    return match.group(1) if match else raw.strip()


def parse_decision(raw: str) -> NemotronDecision:
    """Text in, validated decision out, or `MalformedResponse`.

    Tolerant of exactly two things — a code fence and leading prose before
    the object — and strict about everything else. Being more forgiving than
    that means accepting output we cannot reason about, and the fallback path
    already handles "we could not read it".
    """
    if not raw or not raw.strip():
        raise MalformedResponse("empty response")

    candidate = strip_fences(raw)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        # Some models prefix a sentence before the object. One salvage
        # attempt on the outermost braces, then we give up.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise MalformedResponse(f"no JSON object in {candidate[:120]!r}") from None
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise MalformedResponse(f"unparseable JSON: {exc}") from None

    if not isinstance(payload, dict):
        raise MalformedResponse(f"expected an object, got {type(payload).__name__}")

    try:
        return NemotronDecision.model_validate(payload)
    except Exception as exc:
        raise MalformedResponse(f"response failed validation: {exc}") from None
