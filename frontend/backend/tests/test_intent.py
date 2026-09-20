"""The voice intent classifier. Brief §11.

The exit criterion this stage is actually accountable to (plan §4 Stage 8):
"5 rehearsed phrasings of 'why is Meridian flagged' all resolve to the right
insight." Intent resolution is the first half of that chain — this file
covers it in isolation; `tests/test_voice_api.py` covers the full chain
through to the resolved insight.
"""

from __future__ import annotations

import pytest

from core.config import get_settings
from ml.voice.intent import (
    CONFIDENCE_QUERY,
    DISMISS,
    ENTITY_SUMMARY,
    EXPLAIN_FLAG,
    LIST_FLAGGED,
    SHOW_SOURCE,
    TRAINED_INTENTS,
    TRAINING_DATA,
    UNKNOWN,
    classify,
    get_intent_classifier,
)

# The five rehearsed phrasings, verbatim from the plan's own exit criterion.
REHEARSED_EXPLAIN_FLAG_PHRASINGS = (
    "why is Meridian flagged",
    "why is this flagged",
    "explain this flag",
    "what's suspicious about this",
    "why did you escalate this",
)


@pytest.fixture
def settings():  # type: ignore[no-untyped-def]
    return get_settings()


# ── the rehearsed phrasings, specifically ────────────────────────────────────


@pytest.mark.parametrize("phrasing", REHEARSED_EXPLAIN_FLAG_PHRASINGS)
def test_the_five_rehearsed_phrasings_resolve_to_explain_flag(phrasing, settings) -> None:  # type: ignore[no-untyped-def]
    result = classify(phrasing, settings)
    assert result.intent == EXPLAIN_FLAG


def test_the_rehearsed_phrasings_are_not_borderline() -> None:
    """Confidence comfortably above the gate, not just barely over it — a
    demo that works today because the margin was 0.41 against a 0.40 gate
    is one bad night's sleep on the training set away from failing."""
    settings = get_settings()
    margin = 0.10
    for phrasing in REHEARSED_EXPLAIN_FLAG_PHRASINGS:
        result = classify(phrasing, settings)
        assert result.confidence >= settings.voice_intent_min_confidence + margin, (
            f"{phrasing!r} only cleared the gate by "
            f"{result.confidence - settings.voice_intent_min_confidence:.3f}"
        )


# ── one phrasing per other intent ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("phrasing", "expected"),
    [
        ("show me the source", SHOW_SOURCE),
        ("what's flagged right now", LIST_FLAGGED),
        ("tell me about Kestrel Registry", ENTITY_SUMMARY),
        ("how confident are you in this call", CONFIDENCE_QUERY),
        ("never mind, dismiss it", DISMISS),
    ],
)
def test_one_phrasing_per_intent_class(phrasing, expected, settings) -> None:  # type: ignore[no-untyped-def]
    assert classify(phrasing, settings).intent == expected


# ── the confidence gate ──────────────────────────────────────────────────────


GIBBERISH_PHRASINGS = (
    "purple elephant migratory soup",
    "quantum banana whisper telescope yesterday",
    "seventeen violet umbrellas dance sideways",
)


@pytest.mark.parametrize("gibberish", GIBBERISH_PHRASINGS)
def test_gibberish_becomes_unknown(gibberish, settings) -> None:  # type: ignore[no-untyped-def]
    """Brief §11: never a confident guess. Something with no lexical overlap
    to any trained class should not clear the gate.

    Parametrized over several distinct strings rather than one — a single
    gibberish string clearing the gate is exactly what this test caught
    once already (`voice_intent_min_confidence` was raised from 0.40 to
    0.60 as a direct result, see `core/config.py`), and betting the whole
    check on one more string picked by hand risks the same near-miss
    recurring with a different specific phrase."""
    result = classify(gibberish, settings)
    assert result.intent == UNKNOWN, (
        f"{gibberish!r} scored {result.confidence:.3f} toward {result.raw_top_class!r} "
        f"and cleared the gate at {settings.voice_intent_min_confidence}"
    )


def test_a_lowered_gate_would_have_let_the_same_input_through(settings) -> None:  # type: ignore[no-untyped-def]
    """Proves the gibberish case above is actually being gated, not just
    coincidentally landing on a class named 'unknown' some other way —
    `raw_top_class` is never 'unknown' itself (see module docstring: it is
    not a trained class), so if this input reached `unknown` some other
    way, this assertion is what would have caught it."""
    result = classify("purple elephant migratory soup", settings)
    assert result.raw_top_class in TRAINED_INTENTS
    assert result.confidence < settings.voice_intent_min_confidence


def test_confidence_gate_is_configurable(settings) -> None:  # type: ignore[no-untyped-def]
    """A gate set to 0 lets everything through, including gibberish — proves
    the gate is a real threshold check and not a hardcoded special case for
    a fixed set of known-bad inputs."""
    lenient = settings.model_copy(update={"voice_intent_min_confidence": 0.0})
    result = classify("purple elephant migratory soup", lenient)
    assert result.intent != UNKNOWN
    assert result.intent == result.raw_top_class


def test_unknown_is_never_the_top_class_itself() -> None:
    """`unknown` is a gate outcome, not a label anything can be trained
    toward — see module docstring. If it ever appeared as a literal label
    in the training data, that would silently create an eighth, degenerate
    'confidently unknown' class the gate logic does not expect."""
    labels = {label for _, label in TRAINING_DATA}
    assert UNKNOWN not in labels


# ── the training set itself ──────────────────────────────────────────────────


def test_training_set_covers_every_trained_intent_with_a_reasonable_count() -> None:
    from collections import Counter

    counts = Counter(label for _, label in TRAINING_DATA)
    assert set(counts) == set(TRAINED_INTENTS)
    for intent, count in counts.items():
        assert count >= 15, f"{intent} has only {count} training utterances"


def test_training_set_has_no_duplicate_utterances() -> None:
    """A duplicated utterance silently doubles one example's weight in the
    TF-IDF fit relative to the rest of its class — worth catching as a typo,
    not shipping as a quirk of the training data."""
    texts = [text for text, _ in TRAINING_DATA]
    assert len(texts) == len(set(texts)), "duplicate utterance in the training set"


def test_classifier_is_a_singleton() -> None:
    """Process-lifetime singleton, not retrained per call — see module
    docstring for why there is no persisted checkpoint instead."""
    assert get_intent_classifier() is get_intent_classifier()


def test_classification_is_case_insensitive(settings) -> None:  # type: ignore[no-untyped-def]
    lower = classify("why is meridian flagged", settings)
    upper = classify("WHY IS MERIDIAN FLAGGED", settings)
    assert lower.intent == upper.intent == EXPLAIN_FLAG
