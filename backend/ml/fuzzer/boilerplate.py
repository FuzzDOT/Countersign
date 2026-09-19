"""Boilerplate injection. Brief §15, family 3 of 5.

Legal and footer text is wrapped around the claim — the kind of thing a real
document is full of and a benchmark corpus is not. The claim itself is
untouched.

It tests two things at once: whether the sentence segmenter still isolates
the citation when it is surrounded by noise, and whether the relation model's
graph read-out is disturbed by material it should be ignoring. A model that
loses the relation here is sensitive to *position in the document*, which is
a real failure mode for anything that will meet a scanned PDF.
"""

from __future__ import annotations

import random

from ml.fuzzer.base import Edit, Variant, apply_edits
from ml.tagger.infer import DocumentTagging
from ml.text.tokenize import TokenizedDoc

NAME = "boilerplate"

BEFORE: tuple[str, ...] = (
    "This communication is confidential and intended solely for the addressee.",
    "Terms and conditions available on request. E&OE.",
    "Registered office details are held on the public register.",
    "All figures are stated exclusive of applicable taxes.",
    "Reference should be quoted on all correspondence relating to this matter.",
)

AFTER: tuple[str, ...] = (
    "Any dispute arising shall be governed by the laws of the issuing jurisdiction.",
    "Please retain this document for your records; no further copy will be issued.",
    "Payment terms are net thirty days from the date of issue unless agreed otherwise.",
    "This page was produced automatically and has not been individually reviewed.",
    "Errors and omissions excepted.",
)


class BoilerplateFamily:
    name = NAME

    def build(
        self,
        doc: TokenizedDoc,
        tagging: DocumentTagging,
        target: tuple[int, int],
        rng: random.Random,
    ) -> Variant:
        start, end = target
        # Inserted as zero-width edits immediately either side of the target,
        # so `apply_edits` shifts the span by exactly the inserted lengths and
        # the citation still slices to the same sentence.
        edits = [
            Edit(start=start, end=start, replacement=f"{rng.choice(BEFORE)}\n"),
            Edit(start=end, end=end, replacement=f"\n{rng.choice(AFTER)}"),
        ]
        return apply_edits(doc.text, edits, target, family=NAME)
