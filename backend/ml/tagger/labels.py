"""The BIO tag alphabet and the span/tag conversions.

Brief §15's tag set is six entity types, so BIO gives 13 tags. Keeping the
alphabet and the two conversions in one module matters more than it looks:
`spans_to_tags` and `tags_to_spans` have to be exact inverses on well-formed
input, and a decoder that disagrees with the encoder by one token produces an
F1 that is quietly wrong rather than obviously broken.

`tags_to_spans` is deliberately permissive about `I-` without a preceding `B-`.
Viterbi under a learned transition matrix mostly avoids that, but "mostly" is
not "never", and dropping the span would lose a real entity because of a
formatting detail.
"""

from __future__ import annotations

from dataclasses import dataclass

# Order is the storage order of the model's output layer. Appending is safe;
# reordering invalidates every checkpoint.
ENTITY_TYPES: tuple[str, ...] = (
    "ORG",
    "PERSON",
    "MONEY",
    "DATE",
    "ACCOUNT_REF",
    "TRANSACTION_TYPE",
)

OUTSIDE = "O"

TAGS: tuple[str, ...] = (OUTSIDE, *(f"{p}-{t}" for t in ENTITY_TYPES for p in ("B", "I")))
TAG_INDEX: dict[str, int] = {tag: index for index, tag in enumerate(TAGS)}
N_TAGS = len(TAGS)
OUTSIDE_INDEX = TAG_INDEX[OUTSIDE]


def tag_id(tag: str) -> int:
    return TAG_INDEX[tag]


def entity_type_of(tag: str) -> str | None:
    return None if tag == OUTSIDE else tag.split("-", 1)[1]


@dataclass(frozen=True, slots=True)
class TokenSpan:
    """An entity over a half-open range of token indices.

    Token indices rather than characters, because that is the unit the tagger
    predicts in. The pipeline converts to character offsets using the tokens'
    own recorded positions — never by searching the text for the surface.
    """

    entity_type: str
    token_start: int
    token_end: int  # exclusive
    confidence: float = 1.0

    def __len__(self) -> int:
        return self.token_end - self.token_start


def spans_to_tags(spans: list[TokenSpan] | tuple[TokenSpan, ...], length: int) -> list[str]:
    """BIO tags for a sentence of `length` tokens.

    Overlapping spans are resolved by taking the longer one: weak supervision
    produces overlaps (a gazetteer ORG inside a longer ORG, for instance) and
    silently letting the later one win would depend on dictionary iteration
    order.
    """
    tags = [OUTSIDE] * length
    owner_length = [0] * length

    for span in sorted(spans, key=lambda s: (-len(s), s.token_start)):
        if span.token_start < 0 or span.token_end > length or span.token_start >= span.token_end:
            continue
        if any(owner_length[i] for i in range(span.token_start, span.token_end)):
            continue
        tags[span.token_start] = f"B-{span.entity_type}"
        for index in range(span.token_start + 1, span.token_end):
            tags[index] = f"I-{span.entity_type}"
        for index in range(span.token_start, span.token_end):
            owner_length[index] = len(span)

    return tags


def tags_to_spans(
    tags: list[str] | tuple[str, ...],
    confidences: list[float] | tuple[float, ...] | None = None,
) -> list[TokenSpan]:
    """Decode BIO back to spans.

    Span confidence is the geometric mean of its tokens' marginal
    probabilities. Geometric rather than arithmetic because one token the
    model is unsure about should drag a multi-token entity down — a name where
    the surname is a coin flip is not a name we are confident in.
    """
    spans: list[TokenSpan] = []
    current_type: str | None = None
    start = 0

    def flush(end: int) -> None:
        if current_type is None:
            return
        if confidences is None:
            score = 1.0
        else:
            product = 1.0
            for index in range(start, end):
                product *= max(confidences[index], 1e-9)
            score = product ** (1.0 / max(end - start, 1))
        spans.append(
            TokenSpan(
                entity_type=current_type,
                token_start=start,
                token_end=end,
                confidence=min(max(score, 0.0), 1.0),
            )
        )

    for index, tag in enumerate(tags):
        if tag == OUTSIDE:
            flush(index)
            current_type = None
            continue
        prefix, _, entity_type = tag.partition("-")
        if prefix == "B" or current_type != entity_type:
            # `current_type != entity_type` covers the stray `I-` case: an
            # orphan I-ORG opens a span rather than being discarded.
            flush(index)
            current_type = entity_type
            start = index
    flush(len(tags))

    return spans
