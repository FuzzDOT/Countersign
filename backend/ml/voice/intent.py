"""Spoken-question intent classification. Brief §11.

TF-IDF + a linear SVM over ~120 hand-written utterances, six real classes.
No LLM, and deliberately not one: voice is the delivery channel for facts
this system already computed, not a place to generate new text, and a
general-purpose model guessing at intent is exactly the kind of "confident
but ungrounded" failure this whole project argues against elsewhere.

**`unknown` is not a seventh trained class.** There is no natural utterance
that means "classify me as unknown" — training on invented gibberish for
that label would teach the model to recognize gibberish, not uncertainty.
Instead `unknown` is a confidence gate applied *after* the six-way
classification: below `settings.voice_intent_min_confidence`, the top
class is discarded and `unknown` is returned instead, regardless of what it
was. That is the mechanism behind "never a confident guess."

No persisted checkpoint. `SVC(kernel="linear", probability=True)` over 120
short strings fits in milliseconds — a checkpoint file would be pure
overhead for a model that is faster to retrain than to deserialize. It is
a lazy process-lifetime singleton instead, same shape as `get_settings()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.svm import SVC

from core.config import Settings, get_settings

# ── the seven intents, brief §11 ─────────────────────────────────────────────

EXPLAIN_FLAG = "explain_flag"
SHOW_SOURCE = "show_source"
LIST_FLAGGED = "list_flagged"
ENTITY_SUMMARY = "entity_summary"
CONFIDENCE_QUERY = "confidence_query"
DISMISS = "dismiss"
UNKNOWN = "unknown"

# `unknown` is deliberately absent: see the module docstring.
TRAINED_INTENTS = (
    EXPLAIN_FLAG,
    SHOW_SOURCE,
    LIST_FLAGGED,
    ENTITY_SUMMARY,
    CONFIDENCE_QUERY,
    DISMISS,
)

# ── training utterances ──────────────────────────────────────────────────────
# ~20 per class, hand-written to the phrasing a person would actually use on a
# push-to-talk button, not a written query — contractions, no punctuation
# expected, a mix of command-form and question-form for the same intent.

_EXPLAIN_FLAG_UTTERANCES = (
    "why is this flagged",
    "why is Meridian flagged",
    "explain this flag",
    "what's suspicious about this",
    "why did you escalate this",
    "what triggered this alert",
    "tell me why this is a problem",
    "why does this look risky",
    "what's wrong with this transaction",
    "explain the risk here",
    "why was this escalated",
    "what made this suspicious",
    "break down why this was flagged",
    "give me the reason for this flag",
    "what's the concern with this insight",
    "why should I care about this one",
    "what's the issue here",
    "explain the reasoning behind this flag",
    "why is Advent Holdings flagged",
    "what's going on with this one",
    "walk me through why this got flagged",
)

_SHOW_SOURCE_UTTERANCES = (
    "show me the source",
    "where does this come from",
    "show me the document",
    "what's the citation",
    "show me the original text",
    "pull up the source sentence",
    "where did this come from",
    "show source",
    "let me see the document",
    "open the citation",
    "what document is this from",
    "show me where you found this",
    "pull the source",
    "display the original sentence",
    "where's this from",
    "show me the underlying text",
    "give me the source document",
    "let me see where this came from",
    "can you show me the actual sentence",
    "what's the original wording",
)

_LIST_FLAGGED_UTTERANCES = (
    "what's flagged",
    "show me everything flagged",
    "list flagged items",
    "what needs my attention",
    "show me the flagged insights",
    "what's escalated right now",
    "give me the flagged list",
    "what's currently under review",
    "show flagged",
    "list everything that needs review",
    "what items are flagged",
    "show me what's under review",
    "give me today's flags",
    "what's on the review queue",
    "list escalations",
    "show me what needs review",
    "what's currently flagged",
    "give me the list of flagged items",
    "what should I look at first",
    "what's waiting for me",
)

_ENTITY_SUMMARY_UTTERANCES = (
    "tell me about Meridian",
    "who is Advent Holdings",
    "summarize this entity",
    "give me a summary of Kestrel",
    "what do you know about this company",
    "tell me about this entity",
    "summarize Meridian Supply",
    "who is this person",
    "give me background on this company",
    "what's the history with this entity",
    "tell me more about this organization",
    "summarize this company's activity",
    "what do we know about them",
    "give me the entity profile",
    "who are we talking about",
    "tell me about this organization",
    "summarize this party",
    "what's known about this entity",
    "give me the rundown on Northgate",
    "who's involved here",
)

_CONFIDENCE_QUERY_UTTERANCES = (
    "how confident are you",
    "what's the confidence score",
    "how sure are you about this",
    "what's your confidence level",
    "how certain is this",
    "give me the confidence",
    "what's the vacuity score",
    "how reliable is this call",
    "what's the uncertainty here",
    "how confident is the model",
    "give me the trust score",
    "what's the confidence on this insight",
    "how sure is the system",
    "what's the certainty level",
    "give me the numbers",
    "how confident should I be",
    "what's the score on this",
    "tell me the confidence",
    "how much should I trust this",
    "what's the model's confidence here",
)

_DISMISS_UTTERANCES = (
    "dismiss this",
    "never mind",
    "cancel that",
    "forget it",
    "stop",
    "that's all",
    "I'm done",
    "close this",
    "dismiss",
    "nothing else",
    "that's fine dismiss it",
    "clear this",
    "ignore that",
    "no thanks",
    "that's enough",
    "end briefing",
    "we're done here",
    "dismiss the alert",
    "that's all for now",
    "you can stop there",
)

TRAINING_DATA: tuple[tuple[str, str], ...] = tuple(
    (text, label)
    for label, utterances in (
        (EXPLAIN_FLAG, _EXPLAIN_FLAG_UTTERANCES),
        (SHOW_SOURCE, _SHOW_SOURCE_UTTERANCES),
        (LIST_FLAGGED, _LIST_FLAGGED_UTTERANCES),
        (ENTITY_SUMMARY, _ENTITY_SUMMARY_UTTERANCES),
        (CONFIDENCE_QUERY, _CONFIDENCE_QUERY_UTTERANCES),
        (DISMISS, _DISMISS_UTTERANCES),
    )
    for text in utterances
)


@dataclass(frozen=True, slots=True)
class IntentResult:
    intent: str
    confidence: float
    # The trained class the model actually picked, even when the confidence
    # gate downgraded the answer to `unknown`. Kept for logging/debugging —
    # "it heard confidence_query at 0.31" is a more useful log line than
    # "it heard unknown" when someone is trying to improve the training set.
    raw_top_class: str


def _fit() -> Pipeline:
    texts = [text for text, _ in TRAINING_DATA]
    labels = [label for _, label in TRAINING_DATA]
    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    # Utterances are short; a min_df above 1 would drop
                    # legitimate rare-but-distinctive words like "vacuity"
                    # or "Kestrel" that only appear a couple of times but
                    # are exactly the words that should carry the most
                    # signal for their class.
                    min_df=1,
                ),
            ),
            (
                "svm",
                # probability=True (Platt scaling) is what makes the
                # confidence gate possible at all — a plain LinearSVC has
                # no calibrated notion of "how sure", only a margin.
                SVC(kernel="linear", probability=True, random_state=20260919),
            ),
        ]
    )
    pipeline.fit(texts, labels)
    return pipeline


@lru_cache(maxsize=1)
def get_intent_classifier() -> Pipeline:
    """Process-lifetime singleton. Fits in milliseconds; see module docstring
    for why this is not a persisted checkpoint."""
    return _fit()


def classify(text: str, settings: Settings | None = None) -> IntentResult:
    """Classify one utterance, applying the confidence gate.

    `text` is expected to already be lowercase-normalized STT output; this
    does its own `.lower()` regardless, since the training data and the
    vectorizer are both already case-folded and a caller should not have to
    know that to get a correct answer.
    """
    settings = settings or get_settings()
    pipeline = get_intent_classifier()

    probabilities = pipeline.predict_proba([text.lower()])[0]
    classes = pipeline.classes_
    best_index = int(probabilities.argmax())
    raw_top_class = str(classes[best_index])
    confidence = float(probabilities[best_index])

    if confidence < settings.voice_intent_min_confidence:
        return IntentResult(intent=UNKNOWN, confidence=confidence, raw_top_class=raw_top_class)
    return IntentResult(intent=raw_top_class, confidence=confidence, raw_top_class=raw_top_class)
