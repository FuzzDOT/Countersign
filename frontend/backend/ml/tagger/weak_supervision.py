"""Weak labels for the tagger's training split.

Plan §4 Stage 2: regex for `MONEY`/`DATE`/`ACCOUNT_REF`, gazetteer for
`ORG`/`PERSON` from the **training** name pool. ~2,400 auto-labeled sentences
rather than the brief's 400, because generation is free and more data makes
the F1 claim less fragile.

Why weak labels at all, when the generator already knows every gold span?
Because training on generator gold and then evaluating on generator gold
measures the generator, not the model. Weak supervision is the realistic
setting: an imperfect labeling function over unlabeled text, with the gold
manifest held back as the *dev* set. The gap between the two is the honest
number, and it is the one written to `ml/evals/tagger_report.json`.

**Matching never searches the document text.** Candidates are token n-grams;
the surface compared against a pattern is
`text[tokens[i].char_start : tokens[j].char_end]`, a slice at offsets the
tokenizer recorded. So a weak label's character span is exact by construction
in the same way the generator's is (plan §3) — there is no `.find()` anywhere
in this file, and `tests/test_offsets.py` greps for one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from data.synth import names
from ml.tagger.data import SentenceUnit
from ml.tagger.labels import TokenSpan
from ml.text.tokenize import TokenizedDoc

# Longest gazetteer entry is "Gantry & Poole Fittings Co" at five tokens;
# "Mar 14, 2026" is four. Six leaves headroom without quadratic blowup.
MAX_NGRAM = 6

# Marks spaCy keeps glued to an abbreviation at a sentence boundary.
TRAILING_PUNCTUATION = ".,;:"

MONTHS_FULL = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
MONTHS_ABBR = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"

# Anchored: a pattern matches a candidate n-gram in full or not at all. An
# unanchored search would tag "2026" inside "2026-03-14" as its own DATE.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MONEY", re.compile(r"\$\d[\d,]*(?:\.\d{2})?\Z")),
    (
        "DATE",
        re.compile(
            rf"(?:\d{{1,2}} (?:{MONTHS_FULL}) \d{{4}}"
            rf"|\d{{4}}-\d{{2}}-\d{{2}}"
            rf"|(?:{MONTHS_ABBR}) \d{{1,2}}, \d{{4}})\Z"
        ),
    ),
    # The five shapes in names.ACCOUNT_FORMATS, loosened one digit either way
    # so a widened format does not silently stop being labeled.
    (
        "ACCOUNT_REF",
        re.compile(r"(?:(?:INV|AP|ACCT)-\d{3,6}(?:-X)?|REF/\d{3,6}/[A-Z]|PO \d{4,8})\Z"),
    ),
)


@dataclass(frozen=True, slots=True)
class Gazetteer:
    """Normalized surface -> entity type.

    Built from the pools the split is allowed to see. Calling this with the
    held-out pool would label band-D entities the model is supposed to be
    uncertain about, which is the contamination plan §0 exists to prevent.
    """

    entries: dict[str, str]

    @property
    def max_tokens(self) -> int:
        return MAX_NGRAM

    def lookup(self, surface: str) -> str | None:
        return self.entries.get(_normalize(surface))


def _normalize(surface: str) -> str:
    """Collapse whitespace and casefold, for comparison only.

    This value is never used to compute an offset — the offset comes from the
    tokens the candidate was built from. Normalizing for a lookup is fine;
    normalizing and then locating is what plan §3 forbids.
    """
    return " ".join(surface.split()).casefold()


def training_gazetteer() -> Gazetteer:
    """ORG/PERSON/TRANSACTION_TYPE entries visible to the training split."""
    entries: dict[str, str] = {}
    for org in names.TRAIN_ORGS:
        for alias in names.aliases_for(org):
            entries[_normalize(alias)] = "ORG"
    for person in names.TRAIN_PERSONS:
        entries[_normalize(person)] = "PERSON"
    for transaction in names.TRANSACTION_TYPES:
        entries[_normalize(transaction)] = "TRANSACTION_TYPE"
    return Gazetteer(entries=entries)


def gazetteer_from(
    orgs: tuple[str, ...], persons: tuple[str, ...], *, transactions: bool = True
) -> Gazetteer:
    """Explicit pools. Used by the weak-label quality report, never by training."""
    entries: dict[str, str] = {}
    for org in orgs:
        for alias in names.aliases_for(org):
            entries[_normalize(alias)] = "ORG"
    for person in persons:
        entries[_normalize(person)] = "PERSON"
    if transactions:
        for transaction in names.TRANSACTION_TYPES:
            entries[_normalize(transaction)] = "TRANSACTION_TYPE"
    return Gazetteer(entries=entries)


def label_sentence(
    doc: TokenizedDoc, sentence: SentenceUnit, gazetteer: Gazetteer
) -> list[TokenSpan]:
    """Weak spans for one sentence, in *local* token indices.

    Longest-match-first, left to right, non-overlapping. Greedy rather than
    exhaustive because the alternative — all maximal matches, then a conflict
    resolution pass — costs more code than it buys on a corpus where the only
    real ambiguity is a gazetteer ORG nested inside a longer one.
    """
    length = len(sentence.tokens)
    spans: list[TokenSpan] = []
    start = 0

    while start < length:
        # A candidate may not open or close on a whitespace token. spaCy emits
        # `\n\n` as its own token between an invoice header and its body, and
        # without this the n-gram slice starts two characters early — a span
        # that matches the gazetteer after normalization but whose offsets
        # cover a newline the entity does not include.
        if sentence.tokens[start].surface.isspace():
            start += 1
            continue

        matched = False
        for size in range(min(MAX_NGRAM, length - start), 0, -1):
            end = start + size
            # A span may not close on a token with no alphanumeric character.
            # spaCy splits `book transfer,` into three tokens, and without
            # this the candidate slice is `book transfer,`, which then matches
            # the gazetteer once the comma is trimmed — producing a span one
            # token longer than the entity. The opening token is allowed to be
            # punctuation, because `$` opens every MONEY mention.
            if not any(char.isalnum() for char in sentence.tokens[end - 1].surface):
                continue
            surface = doc.text[
                sentence.tokens[start].char_start : sentence.tokens[end - 1].char_end
            ]

            entity_type = _match(surface, gazetteer)
            if entity_type is None:
                continue

            spans.append(TokenSpan(entity_type=entity_type, token_start=start, token_end=end))
            start = end
            matched = True
            break
        if not matched:
            start += 1

    return spans


def _match(surface: str, gazetteer: Gazetteer) -> str | None:
    """Gazetteer first, then the patterns, each with and without terminal
    punctuation.

    The second attempt exists because spaCy keeps sentence-final punctuation
    attached to abbreviations: `Quarrymead Aggregates Co.` is one token whose
    text includes the period, and the gold span — derived from the same
    tokens — covers it too. Refusing to match would drop the entity because of
    a tokenizer detail, so the candidate is retried with the trailing marks
    removed while the *span* still covers the whole token.
    """
    for candidate in _punctuation_variants(surface):
        entity_type = gazetteer.lookup(candidate)
        if entity_type is not None:
            return entity_type
        entity_type = _pattern_type(candidate)
        if entity_type is not None:
            return entity_type
    return None


def _punctuation_variants(surface: str) -> tuple[str, ...]:
    trimmed = surface
    while trimmed and trimmed[-1] in TRAILING_PUNCTUATION:
        trimmed = trimmed[:-1]
    return (surface,) if trimmed == surface else (surface, trimmed)


def _pattern_type(surface: str) -> str | None:
    # No whitespace collapsing here: the patterns encode exactly one space
    # where a space belongs, and a candidate spanning a line break is not the
    # date it superficially resembles.
    for entity_type, pattern in PATTERNS:
        if pattern.match(surface):
            return entity_type
    return None


def label_document(
    doc: TokenizedDoc, sentences: list[SentenceUnit], gazetteer: Gazetteer
) -> list[list[TokenSpan]]:
    return [label_sentence(doc, sentence, gazetteer) for sentence in sentences]
