"""Sentence units and batching for the tagger.

The tagger's unit of work is a sentence, because that is also the unit a
citation is: `insights.sentence_text` is one sentence and the CRF's transition
structure should not run across a sentence boundary it will never be asked to
decode across.

`token_offset` on every sentence is what keeps the invariant cheap. A span the
model predicts at local index 3 maps to `doc.tokens[token_offset + 3]`, whose
`char_start`/`char_end` were recorded by the tokenizer — so a character offset
is always *looked up*, never searched for (plan §3).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import torch
from torch import Tensor

from ml.tagger.labels import TokenSpan
from ml.tagger.vocab import Vocab, encode_chars, encode_words
from ml.text.tokenize import Span, Token, TokenizedDoc

# spaCy occasionally emits a "sentence" that is one newline. Nothing useful
# can be tagged in it and it would dominate a batch's padding.
MIN_SENTENCE_TOKENS = 1


@dataclass(frozen=True, slots=True)
class SentenceUnit:
    """One sentence of one document, positioned in the document's tokens."""

    doc_index: int
    sent_index: int
    span: Span
    tokens: tuple[Token, ...]

    @property
    def token_offset(self) -> int:
        return self.span.token_start

    @property
    def surfaces(self) -> list[str]:
        return [token.surface for token in self.tokens]

    def __len__(self) -> int:
        return len(self.tokens)


@dataclass(frozen=True, slots=True)
class TaggedSentence:
    """A sentence plus its BIO labels. The training example."""

    surfaces: tuple[str, ...]
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.surfaces) != len(self.tags):
            raise AssertionError(
                f"{len(self.surfaces)} tokens but {len(self.tags)} tags — "
                "an alignment bug here silently trains the model on shifted labels"
            )

    def __len__(self) -> int:
        return len(self.surfaces)


def iter_sentences(doc: TokenizedDoc, *, doc_index: int = 0) -> Iterator[SentenceUnit]:
    for sent_index, span in enumerate(doc.sentences):
        tokens = doc.tokens_in(span)
        if len(tokens) < MIN_SENTENCE_TOKENS:
            continue
        yield SentenceUnit(doc_index=doc_index, sent_index=sent_index, span=span, tokens=tokens)


# ── tensorization ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Batch:
    word_ids: Tensor  # [B, T]
    char_ids: Tensor  # [B, T, L]
    mask: Tensor  # [B, T] bool
    tag_ids: Tensor | None  # [B, T]

    def __len__(self) -> int:
        return int(self.word_ids.size(0))


def encode_batch(
    sentences: list[list[str]],
    vocab: Vocab,
    *,
    max_word_len: int,
    tag_sequences: list[list[int]] | None = None,
) -> Batch:
    """Pad to the batch's longest sentence, not to a global maximum.

    Padding to a fixed 128 would make the common 15-token sentence eight times
    more expensive than it needs to be, and training time is the budget that
    decides whether the tagger gets retrained after a corpus change.
    """
    word_index = vocab.word_index
    char_index = vocab.char_index
    width = max((len(s) for s in sentences), default=1)

    word_rows: list[list[int]] = []
    char_rows: list[list[list[int]]] = []
    mask_rows: list[list[bool]] = []
    tag_rows: list[list[int]] = []

    for position, surfaces in enumerate(sentences):
        words = encode_words(surfaces, word_index)
        chars = encode_chars(surfaces, char_index, max_len=max_word_len)
        pad = width - len(surfaces)
        word_rows.append(words + [0] * pad)
        char_rows.append(chars + [[0] * max_word_len] * pad)
        mask_rows.append([True] * len(surfaces) + [False] * pad)
        if tag_sequences is not None:
            tag_rows.append(tag_sequences[position] + [0] * pad)

    return Batch(
        word_ids=torch.tensor(word_rows, dtype=torch.long),
        char_ids=torch.tensor(char_rows, dtype=torch.long),
        mask=torch.tensor(mask_rows, dtype=torch.bool),
        tag_ids=torch.tensor(tag_rows, dtype=torch.long) if tag_sequences is not None else None,
    )


# ── gold alignment ───────────────────────────────────────────────────────────


def align_char_spans(
    doc: TokenizedDoc,
    sentences: list[SentenceUnit],
    char_spans: list[tuple[str, int, int]],
) -> list[list[TokenSpan]]:
    """Map `(entity_type, char_start, char_end)` triples onto sentence-local
    token spans.

    Used for the dev set, whose labels are the generator's gold mentions, and
    in Stage 3 for gold relation arguments. The lookup goes through
    `TokenizedDoc.token_indices_in`, which compares recorded offsets — a gold
    span that no longer lines up with a token produces no span rather than a
    wrong one, and the count is reported so a silent drop is visible.
    """
    per_sentence: list[list[TokenSpan]] = [[] for _ in sentences]
    sentence_of_token: dict[int, int] = {}
    for sentence_index, sentence in enumerate(sentences):
        for index in range(sentence.span.token_start, sentence.span.token_end + 1):
            sentence_of_token[index] = sentence_index

    for entity_type, char_start, char_end in char_spans:
        indices = doc.token_indices_in(char_start, char_end)
        if not indices:
            continue
        position: int | None = sentence_of_token.get(indices[0])
        if position is None:
            continue
        # A mention straddling a sentence boundary is a segmentation artifact,
        # not an entity. Clipping it to the first sentence would train the
        # model on a truncated name.
        if any(sentence_of_token.get(i) != position for i in indices):
            continue
        offset = sentences[position].span.token_start
        per_sentence[position].append(
            TokenSpan(
                entity_type=entity_type,
                token_start=indices[0] - offset,
                token_end=indices[-1] + 1 - offset,
            )
        )

    return per_sentence
