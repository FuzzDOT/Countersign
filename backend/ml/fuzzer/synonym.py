"""Synonym substitution. Brief §15, family 1 of 5.

WordNet synonyms for 15% of the non-entity content words. This is the gentlest
family: it changes the wording around a claim while leaving the claim, its
arguments and its syntax intact. A model that loses the relation here is
keying on surface vocabulary rather than structure.

**WordNet is baked into the image**, not downloaded at runtime — the
Dockerfile runs `nltk.downloader wordnet omw-1.4` in both stages. A fuzzer
dying at hour 12 inside a container with no internet is an avoidable
disaster, and the curated finance lexicon below is the second line of defence
if the corpus is missing anyway.
"""

from __future__ import annotations

import random
from functools import lru_cache
from typing import Any

from core.logging import get_logger
from ml.fuzzer.base import Edit, Variant, apply_edits, overlaps, protected_spans, unchanged
from ml.tagger.infer import DocumentTagging
from ml.text.tokenize import TokenizedDoc

log = get_logger(__name__)

NAME = "synonym"

# Share of eligible tokens replaced, per brief §15.
SUBSTITUTION_RATE = 0.15

# Universal POS tags that carry meaning rather than structure. Swapping a
# determiner or a preposition changes grammaticality, not semantics, and the
# punctuation family already covers "does the model survive noise".
CONTENT_POS = frozenset({"NOUN", "VERB", "ADJ", "ADV"})

WORDNET_POS = {"NOUN": "n", "VERB": "v", "ADJ": "a", "ADV": "r"}

# Hard fallback if the WordNet corpus is unavailable. Curated for this domain
# rather than general: these are the words the templates actually use, and a
# fuzzer that silently substitutes nothing would report every insight as
# perfectly robust — a free win, and a false one.
FINANCE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "payment": ("remittance", "disbursement"),
    "paid": ("remitted", "settled"),
    "pay": ("remit", "settle"),
    "wired": ("transferred", "remitted"),
    "wire": ("transfer", "remittance"),
    "sent": ("dispatched", "forwarded"),
    "invoice": ("bill", "statement"),
    "invoiced": ("billed", "charged"),
    "billed": ("invoiced", "charged"),
    "submitted": ("filed", "lodged"),
    "issued": ("raised", "produced"),
    "amount": ("sum", "total"),
    "funds": ("monies", "capital"),
    "account": ("ledger", "record"),
    "owned": ("held", "controlled"),
    "owns": ("holds", "controls"),
    "subsidiary": ("affiliate", "unit"),
    "parent": ("holder", "owner"),
    "ownership": ("proprietorship", "title"),
    "registered": ("recorded", "listed"),
    "address": ("premises", "location"),
    "signatory": ("signer", "authorizer"),
    "director": ("officer", "principal"),
    "contract": ("agreement", "arrangement"),
    "schedule": ("timetable", "plan"),
    "period": ("term", "interval"),
    "settlement": ("clearance", "discharge"),
    "receivables": ("debtors", "claims"),
    "obligation": ("liability", "commitment"),
    "transaction": ("dealing", "operation"),
    "routed": ("channelled", "directed"),
    "attributed": ("ascribed", "assigned"),
    "recognized": ("recorded", "booked"),
    "services": ("work", "provision"),
    "rendered": ("provided", "supplied"),
    "early": ("premature", "advance"),
    "ahead": ("in advance", "beforehand"),
    "days": ("business days", "working days"),
    "filing": ("submission", "return"),
    "holding": ("parent", "controlling"),
    "structure": ("arrangement", "framework"),
    "intermediate": ("intervening", "interposed"),
    "interest": ("stake", "holding"),
    "beneficial": ("equitable", "underlying"),
}


@lru_cache(maxsize=1)
def _wordnet() -> Any | None:
    try:
        from nltk.corpus import wordnet

        wordnet.synsets("payment")  # forces the corpus to load, or raises
        return wordnet
    except Exception as exc:
        log.warning(
            "wordnet_unavailable",
            error=str(exc),
            effect="synonym family falls back to the curated finance lexicon",
        )
        return None


def candidates(surface: str, pos: str) -> tuple[str, ...]:
    """Synonyms for one word, WordNet first and the lexicon second."""
    lowered = surface.casefold()

    corpus = _wordnet()
    if corpus is not None and pos in WORDNET_POS:
        found: list[str] = []
        for synset in corpus.synsets(lowered, pos=WORDNET_POS[pos]):
            for lemma in synset.lemmas():
                word = lemma.name().replace("_", " ")
                if word.casefold() != lowered and word.isascii() and " " not in word:
                    found.append(word)
        if found:
            # Sorted so a seeded rng picks the same synonym on every machine;
            # WordNet's own lemma order is stable but not guaranteed across
            # corpus versions.
            return tuple(sorted(dict.fromkeys(found)))

    return FINANCE_SYNONYMS.get(lowered, ())


def _match_case(original: str, replacement: str) -> str:
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


class SynonymFamily:
    name = NAME

    def build(
        self,
        doc: TokenizedDoc,
        tagging: DocumentTagging,
        target: tuple[int, int],
        rng: random.Random,
    ) -> Variant:
        entities = protected_spans(tagging)
        eligible = [
            token
            for token in doc.tokens
            if token.pos in CONTENT_POS
            and token.surface.isalpha()
            and not overlaps((token.char_start, token.char_end), entities)
            and candidates(token.surface, token.pos)
        ]
        if not eligible:
            return unchanged(doc, target, NAME)

        count = max(1, round(len(eligible) * SUBSTITUTION_RATE))
        chosen = rng.sample(eligible, min(count, len(eligible)))

        edits = [
            Edit(
                start=token.char_start,
                end=token.char_end,
                replacement=_match_case(
                    token.surface, rng.choice(candidates(token.surface, token.pos))
                ),
            )
            for token in chosen
        ]
        return apply_edits(doc.text, edits, target, family=NAME)
