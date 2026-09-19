"""Sentence reordering. Brief §15, family 4 of 5.

The document's sentences are shuffled; the target sentence is moved but not
modified. It tests whether a claim's extraction depends on where in the
document it happens to sit.

**We expect this family to do almost nothing, and that is worth reporting
rather than hiding.** The relation model reads one sentence graph at a time,
so moving a sentence cannot change its parse or its prediction. What it *can*
change is coreference — entity ids are minted from the first surface form
seen, and the resolver walks documents in order — and sentence segmentation,
if the reorder puts a header line next to prose.

A near-zero flip rate here is therefore a statement about our architecture,
not a claim of robustness, and the interpretation string says so. A family
that looks like a null result is still evidence: it is what tells you the
other four are measuring something real.
"""

from __future__ import annotations

import random

from ml.fuzzer.base import Variant, unchanged
from ml.tagger.infer import DocumentTagging
from ml.text.tokenize import TokenizedDoc

NAME = "reorder"

MIN_SENTENCES = 3


class ReorderFamily:
    name = NAME

    def build(
        self,
        doc: TokenizedDoc,
        tagging: DocumentTagging,
        target: tuple[int, int],
        rng: random.Random,
    ) -> Variant:
        spans = list(doc.sentences)
        if len(spans) < MIN_SENTENCES:
            return unchanged(doc, target, NAME)

        pieces = [doc.text[span.char_start : span.char_end] for span in spans]
        order = list(range(len(pieces)))
        rng.shuffle(order)
        if order == list(range(len(pieces))):
            return unchanged(doc, target, NAME)

        target_index = next(
            (
                index
                for index, span in enumerate(spans)
                if span.char_start <= target[0] and target[1] <= span.char_end
            ),
            None,
        )
        if target_index is None:
            return unchanged(doc, target, NAME)

        # Rebuilt rather than edited: a reorder is not expressible as disjoint
        # in-place replacements, so the target's new position is computed from
        # the lengths of whatever now precedes it — still arithmetic, still
        # never a search of the new text.
        separator = "\n"
        new_start = 0
        for position in order:
            if position == target_index:
                break
            new_start += len(pieces[position]) + len(separator)

        text = separator.join(pieces[position] for position in order)
        inner = target[0] - spans[target_index].char_start

        return Variant(
            family=NAME,
            text=text,
            target_start=new_start + inner,
            target_end=new_start + inner + (target[1] - target[0]),
        )
