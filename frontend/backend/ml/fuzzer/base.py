"""The offset-safe primitive every perturbation is built on. Plan §3.

**The fuzzer trap.** Perturbation changes text, which changes every offset in
it. An insight's citation is a `(char_start, char_end)` into the *original*
`documents.raw_text`, so after a perturbation there is no relationship
between the two coordinate systems unless one is maintained deliberately.

So no perturbation edits a string directly. Each one produces a list of
`Edit`s over disjoint ranges of the original text, and `apply_edits`
reconstructs both the new text *and* the new position of the target span by
accumulating the length deltas. The target span is carried through by
arithmetic, never located by searching the perturbed text — which is the same
rule the generator and the tokenizer follow, for the same reason.

The other half of the trap is persistence: **a perturbed variant must never
reach the `documents` table.** If one did, every citation in the demo would
silently point at the wrong sentence. Variants are in-memory only,
`fragility_trials` stores no offsets at all, and
`tests/test_fuzzer.py::test_the_fuzzer_writes_no_documents` asserts it.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Protocol

from ml.tagger.infer import DocumentTagging
from ml.text.tokenize import TokenizedDoc

# Perturbation families, in the order brief §15 lists them. The order is the
# report's column order and the `by_perturbation` order.
FAMILIES: tuple[str, ...] = (
    "synonym",
    "rename",
    "boilerplate",
    "reorder",
    "punctuation",
)


@dataclass(frozen=True, slots=True)
class Edit:
    """Replace `text[start:end]` with `replacement`.

    Ranges must be disjoint; overlapping edits are a bug in the family that
    produced them, and `apply_edits` raises rather than silently picking one.
    """

    start: int
    end: int
    replacement: str

    @property
    def delta(self) -> int:
        return len(self.replacement) - (self.end - self.start)


@dataclass(frozen=True, slots=True)
class Variant:
    """One perturbed document, with the target span tracked through the edit."""

    family: str
    text: str
    target_start: int
    target_end: int
    # Cluster-key substitutions the `rename` family made, so the runner can
    # recognize the renamed company as the entity it used to be.
    renames: dict[str, str] = field(default_factory=dict)
    # Set when a family had nothing to change on this document — a sentence
    # with no substitutable content word, say. Counted and reported rather
    # than silently scored as "robust", which would be a free win.
    vacuous: bool = False

    @property
    def target_text(self) -> str:
        return self.text[self.target_start : self.target_end]


class Family(Protocol):
    """One perturbation family."""

    name: str

    def build(
        self,
        doc: TokenizedDoc,
        tagging: DocumentTagging,
        target: tuple[int, int],
        rng: random.Random,
    ) -> Variant: ...


def apply_edits(
    text: str, edits: list[Edit], target: tuple[int, int], *, family: str, **extra: object
) -> Variant:
    """Rebuild the text and carry the target span through.

    Edits are applied left to right over the original coordinates, so a
    caller never has to reason about how an earlier edit moved a later one —
    which is exactly the mistake that would put a citation two characters off
    and be invisible until a judge clicked it.
    """
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    for earlier, later in itertools.pairwise(ordered):
        if later.start < earlier.end:
            raise AssertionError(
                f"{family}: overlapping edits [{earlier.start}:{earlier.end}] and "
                f"[{later.start}:{later.end}]"
            )

    target_start, target_end = target
    pieces: list[str] = []
    cursor = 0
    shift_start = 0
    shift_end = 0

    for edit in ordered:
        pieces.append(text[cursor : edit.start])
        pieces.append(edit.replacement)
        cursor = edit.end
        # An edit strictly before the target moves both bounds; an edit
        # inside it moves only the end.
        if edit.end <= target_start:
            shift_start += edit.delta
            shift_end += edit.delta
        elif edit.start >= target_end:
            pass
        else:
            shift_end += edit.delta
    pieces.append(text[cursor:])

    return Variant(
        family=family,
        text="".join(pieces),
        target_start=target_start + shift_start,
        target_end=target_end + shift_end,
        **extra,  # type: ignore[arg-type]
    )


def protected_spans(tagging: DocumentTagging) -> list[tuple[int, int]]:
    """Character ranges a perturbation must not touch.

    Entity surfaces. The point of every family except `rename` is to change
    the *context* and see whether the relation survives; mangling the company
    name as well would confound the two and make the per-family breakdown
    meaningless.
    """
    return [(mention.char_start, mention.char_end) for mention in tagging.mentions]


def overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in spans)


def unchanged(doc: TokenizedDoc, target: tuple[int, int], family: str) -> Variant:
    """A variant for a document the family could not perturb."""
    return Variant(
        family=family,
        text=doc.text,
        target_start=target[0],
        target_end=target[1],
        vacuous=True,
    )
