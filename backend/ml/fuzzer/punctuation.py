"""Punctuation and whitespace noise. Brief §15, family 5 of 5.

Ten percent of the inter-token gaps get an extra space, a stray comma, or a
line break. Entity surfaces are left alone, so the parties are still
findable and only the surrounding structure degrades.

This is the OCR family. Text that came out of a scanner has exactly this
kind of damage, and a pipeline that only works on clean generated prose will
meet it in the first real document it sees. It is also the family most likely
to expose a brittle *segmenter* rather than a brittle model — a stray line
break in the wrong place splits a sentence, and the citation goes with it.
"""

from __future__ import annotations

import random

from ml.fuzzer.base import Edit, Variant, apply_edits, overlaps, protected_spans, unchanged
from ml.tagger.infer import DocumentTagging
from ml.text.tokenize import TokenizedDoc

NAME = "punctuation"

NOISE_RATE = 0.10

# Weighted toward the damage a scanner actually does: doubled spaces and
# spurious line breaks are common, stray semicolons are not.
INSERTIONS: tuple[str, ...] = (" ", " ", "  ", "\n", " ,", " -", " .")


class PunctuationFamily:
    name = NAME

    def build(
        self,
        doc: TokenizedDoc,
        tagging: DocumentTagging,
        target: tuple[int, int],
        rng: random.Random,
    ) -> Variant:
        entities = protected_spans(tagging)
        positions = [
            token.char_end
            for token in doc.tokens
            if not token.surface.isspace()
            and not overlaps((token.char_start, token.char_end + 1), entities)
        ]
        if not positions:
            return unchanged(doc, target, NAME)

        count = max(1, round(len(positions) * NOISE_RATE))
        chosen = sorted(rng.sample(positions, min(count, len(positions))))

        edits = [
            Edit(start=position, end=position, replacement=rng.choice(INSERTIONS))
            for position in chosen
        ]
        return apply_edits(doc.text, edits, target, family=NAME)
