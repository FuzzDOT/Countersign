"""Building relation training examples from the gold manifest.

Two choices worth naming, because both are the kind of thing a reviewer
should be able to disagree with:

**Candidates and labels come from gold mentions; node features come from the
tagger's predictions.** Training the relation model on predicted entities
would make it fit the tagger's mistakes, and training it on gold *features*
would give it a perfect entity-type signal it will never see at inference.
Splitting them this way keeps the supervision clean and the input
distribution honest.

**Negatives are every other ordered argument pair in the sentence.** That is
what inference generates, so it is what the model has to reject. It also
means NO_RELATION dominates by roughly four to one, which is why the loss
takes class weights — unweighted, the model learns to say nothing and is
right most of the time.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor

from core.logging import get_logger
from data.synth.labels import GoldDocument
from ml.relations.graph_builder import ARGUMENT_TYPES, build_sentence_graph, candidate_pairs
from ml.relations.interface import NO_RELATION, RELATION_INDEX, CandidatePair, SentenceGraph
from ml.tagger.infer import DocumentTagging, TaggedMention
from ml.text.tokenize import TokenizedDoc

log = get_logger(__name__)


@dataclass(slots=True)
class RelationExample:
    """One sentence: its graph, its candidate pairs, and their gold classes."""

    graph: SentenceGraph
    pairs: list[CandidatePair]
    labels: list[int]
    lemmas: tuple[str, ...]
    # Kept for the eval report, not used in training.
    sentence_text: str = ""

    def __len__(self) -> int:
        return len(self.pairs)


def examples_for_document(
    doc: TokenizedDoc,
    tagging: DocumentTagging,
    gold: GoldDocument,
) -> list[RelationExample]:
    """Every trainable sentence of one document."""
    out: list[RelationExample] = []

    for sentence_index, encoding in enumerate(tagging.sentences):
        unit = encoding.unit
        span = unit.span

        gold_mentions = _gold_argument_mentions(doc, gold, span.char_start, span.char_end)
        if len(gold_mentions) < 2:
            continue

        pairs = _pairs_from(gold_mentions, unit.token_offset)
        if not pairs:
            continue

        truth = _gold_relations(gold, span.char_start, span.char_end)
        labels = [
            RELATION_INDEX[truth.get((pair.subject_key, pair.object_key), NO_RELATION)]
            for pair in pairs
        ]
        out.append(
            RelationExample(
                graph=build_sentence_graph(
                    doc, encoding, tagging.mentions_in_sentence(sentence_index)
                ),
                pairs=pairs,
                labels=labels,
                lemmas=tuple(token.lemma for token in unit.tokens),
                sentence_text=doc.text[span.char_start : span.char_end],
            )
        )

    return out


@dataclass(frozen=True, slots=True)
class _GoldMention:
    canonical: str
    entity_type: str
    token_start: int  # document-global, half-open
    token_end: int


def _gold_argument_mentions(
    doc: TokenizedDoc, gold: GoldDocument, char_start: int, char_end: int
) -> list[_GoldMention]:
    """Gold ORG/PERSON mentions inside a sentence, aligned to tokens.

    Aligned through `token_indices_in`, which compares recorded offsets — the
    span is never located by searching the text (plan §3).
    """
    found: list[_GoldMention] = []
    for mention in gold.mentions:
        if mention.entity_type not in ARGUMENT_TYPES:
            continue
        if not (char_start <= mention.char_start and mention.char_end <= char_end):
            continue
        indices = doc.token_indices_in(mention.char_start, mention.char_end)
        if not indices:
            continue
        found.append(
            _GoldMention(
                canonical=mention.canonical,
                entity_type=mention.entity_type,
                token_start=indices[0],
                token_end=indices[-1] + 1,
            )
        )
    return found


def _pairs_from(mentions: list[_GoldMention], offset: int) -> list[CandidatePair]:
    pairs: list[CandidatePair] = []
    for subject in mentions:
        for obj in mentions:
            if subject.canonical == obj.canonical:
                continue
            pairs.append(
                CandidatePair(
                    subject_tokens=tuple(
                        range(subject.token_start - offset, subject.token_end - offset)
                    ),
                    object_tokens=tuple(range(obj.token_start - offset, obj.token_end - offset)),
                    subject_key=subject.canonical,
                    object_key=obj.canonical,
                    subject_type=subject.entity_type,
                    object_type=obj.entity_type,
                )
            )
    return pairs


def _gold_relations(
    gold: GoldDocument, char_start: int, char_end: int
) -> dict[tuple[str, str], str]:
    """Gold triples whose citation sentence is this one.

    Containment rather than equality: spaCy sometimes merges two generated
    sentences into one segment, and requiring an exact span match would drop
    the label for every relation in the merged pair.
    """
    return {
        (relation.subject_canonical, relation.object_canonical): relation.relation
        for relation in gold.relations
        if char_start <= relation.char_start and relation.char_end <= char_end
    }


def predicted_pairs_for_sentence(
    tagging: DocumentTagging, sentence_index: int
) -> list[CandidatePair]:
    """What inference sees: pairs over the tagger's own mentions."""
    encoding = tagging.sentences[sentence_index]
    mentions: tuple[TaggedMention, ...] = tagging.mentions_in_sentence(sentence_index)
    return candidate_pairs(mentions, encoding.unit.token_offset)


def class_weights(labels: list[int], n_classes: int, *, cap: float = 4.0) -> Tensor:
    """Square-root inverse-frequency weights, capped.

    Square root rather than linear. Full inverse frequency corrects the
    NO_RELATION imbalance so hard that the model over-predicts every real
    class: measured at 0.84 recall against 0.59 precision, with
    SHARES_ADDRESS_WITH fired on nineteen pairs where three were real. The
    softer reweighting keeps the null class from swamping the gradient
    without paying for it in precision.

    Capped as well, because a class with three examples would otherwise earn
    a weight of several hundred and one mislabeled instance would dominate
    the epoch.
    """
    counts = torch.zeros(n_classes)
    for label in labels:
        counts[label] += 1
    weights = (counts.sum() / (counts.clamp_min(1.0) * n_classes)).sqrt()
    return weights.clamp(max=cap)
