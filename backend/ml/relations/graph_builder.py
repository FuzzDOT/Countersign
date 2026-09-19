"""Turning a tagged sentence into the graph both relation models consume.

Plan §4 Stage 3. Nodes are tokens; edges are dependency arcs, stored in both
directions with a direction flag, plus a self-loop per token so an isolated
node keeps its own representation through three layers of aggregation.

Node features are

    [ tagger hidden (512) ‖ entity type (7) ‖ POS (17) ‖ dependency label (45)
      ‖ relative position (2) ]

which is the plan's vector plus the dependency label. That addition is
deliberate: the plan puts only a *direction* flag on the edges, so without
the label on the node the model cannot tell an `nsubj` arc from a `dobj` one
— and the difference between "X wired Y" and "Y wired X" is exactly the thing
being classified. Attaching the label to the child node is equivalent to
putting it on the incoming arc and costs no extra edge machinery.

**The tagger's hidden states are reused, not recomputed.** They come from the
same forward pass that produced the mentions (`ml/tagger/infer.py`), which
halves the work per document and guarantees the graph describes the same
analysis the citation does.
"""

from __future__ import annotations

import torch
from torch import Tensor

from ml.relations.interface import CandidatePair, SentenceGraph, edge_id
from ml.tagger.infer import SentenceEncoding, TaggedMention
from ml.tagger.labels import ENTITY_TYPES
from ml.text.tokenize import ROOT_HEAD, TokenizedDoc

# Universal POS tags as spaCy emits them, plus SPACE. Fixed order: it is part
# of the feature layout and a checkpoint cannot survive a reordering.
POS_TAGS: tuple[str, ...] = (
    "ADJ",
    "ADP",
    "ADV",
    "AUX",
    "CCONJ",
    "DET",
    "INTJ",
    "NOUN",
    "NUM",
    "PART",
    "PRON",
    "PROPN",
    "PUNCT",
    "SCONJ",
    "SYM",
    "VERB",
    "X",
)

# The ClearNLP label set en_core_web_sm produces. Anything outside it folds
# into the trailing OTHER slot rather than being dropped, so an unexpected
# label degrades one feature instead of raising.
DEP_LABELS: tuple[str, ...] = (
    "ROOT",
    "acl",
    "acomp",
    "advcl",
    "advmod",
    "agent",
    "amod",
    "appos",
    "attr",
    "aux",
    "auxpass",
    "case",
    "cc",
    "ccomp",
    "compound",
    "conj",
    "csubj",
    "csubjpass",
    "dative",
    "dep",
    "det",
    "dobj",
    "expl",
    "intj",
    "mark",
    "meta",
    "neg",
    "nmod",
    "npadvmod",
    "nsubj",
    "nsubjpass",
    "nummod",
    "oprd",
    "parataxis",
    "pcomp",
    "pobj",
    "poss",
    "preconj",
    "predet",
    "prep",
    "prt",
    "punct",
    "quantmod",
    "relcl",
    "xcomp",
)
DEP_OTHER = "__other__"

ENTITY_SLOTS: tuple[str, ...] = ("O", *ENTITY_TYPES)

POS_INDEX = {tag: index for index, tag in enumerate(POS_TAGS)}
DEP_INDEX = {label: index for index, label in enumerate(DEP_LABELS)}
ENTITY_SLOT_INDEX = {name: index for index, name in enumerate(ENTITY_SLOTS)}

N_POS = len(POS_TAGS) + 1  # + unknown
N_DEP = len(DEP_LABELS) + 1  # + other
N_ENTITY = len(ENTITY_SLOTS)
N_POSITION = 2

# Direction of an edge: from a token to its syntactic head, the reverse, or a
# self-loop.
DIRECTION_SLOTS = 3
EDGE_FEATURE_DIM = DIRECTION_SLOTS

# A sentence with more entity mentions than this produces more ordered pairs
# than are worth classifying, and the extras are almost always value types
# repeated across a table row. Ordered pairs grow quadratically and ingest has
# a 30-second budget (brief §16).
MAX_PAIRS_PER_SENTENCE = 24

# Only these can be a relation's arguments. MONEY and DATE are evidence
# inside a sentence, not parties to a relation — the brief's relation set
# relates organizations and people.
ARGUMENT_TYPES = frozenset({"ORG", "PERSON"})


def node_feature_dim(hidden_dim: int) -> int:
    return hidden_dim + N_ENTITY + N_POS + N_DEP + N_POSITION


def build_sentence_graph(
    doc: TokenizedDoc,
    encoding: SentenceEncoding,
    mentions: tuple[TaggedMention, ...],
) -> SentenceGraph:
    """One sentence, one graph. Indices are sentence-local throughout.

    Sentence-local rather than document-global because that is the coordinate
    system `insights.attention` publishes and the ablation panel sends back:
    an `edge_id` has to mean the same thing after a re-parse of the stored
    sentence, and a document-global index would shift if an earlier sentence
    were re-segmented.
    """
    unit = encoding.unit
    offset = unit.token_offset
    length = len(unit)

    entity_slot = _entity_slots(mentions, offset, length)

    rows: list[Tensor] = []
    for local, token in enumerate(unit.tokens):
        categorical = torch.zeros(N_ENTITY + N_POS + N_DEP + N_POSITION)
        categorical[ENTITY_SLOT_INDEX[entity_slot[local]]] = 1.0
        categorical[N_ENTITY + POS_INDEX.get(token.pos, len(POS_TAGS))] = 1.0
        categorical[N_ENTITY + N_POS + DEP_INDEX.get(token.dep, len(DEP_LABELS))] = 1.0

        # Relative position: where the token sits in the sentence, and how far
        # its syntactic head is, signed and normalized. The second is what
        # distinguishes a long-range passive construction (band B) from an
        # adjacent active one (band A) without the model having to count.
        head_local = _local_head(token.head_idx, offset, length)
        categorical[-2] = local / max(length - 1, 1)
        categorical[-1] = 0.0 if head_local is None else (head_local - local) / max(length, 1)

        rows.append(torch.cat([encoding.hidden[local], categorical]))

    node_features = torch.stack(rows) if rows else torch.zeros(0, 0)

    sources: list[int] = []
    destinations: list[int] = []
    directions: list[int] = []
    identifiers: list[str] = []
    dependencies: list[str] = []

    def add(src: int, dst: int, direction: int, label: str) -> None:
        sources.append(src)
        destinations.append(dst)
        directions.append(direction)
        identifiers.append(edge_id(src, dst))
        dependencies.append(label)

    for local, token in enumerate(unit.tokens):
        head_local = _local_head(token.head_idx, offset, length)
        if head_local is not None and head_local != local:
            add(local, head_local, 0, token.dep)  # child -> head
            add(head_local, local, 1, token.dep)  # head -> child
        # Self-loop last, so an ablation that masks every real arc still
        # leaves the node able to represent itself rather than producing a
        # degenerate all-zero embedding.
        add(local, local, 2, "self")

    edge_index = torch.tensor([sources, destinations], dtype=torch.long)
    edge_features = torch.zeros(len(directions), EDGE_FEATURE_DIM)
    for position, direction in enumerate(directions):
        edge_features[position, direction] = 1.0

    return SentenceGraph(
        node_features=node_features,
        edge_index=edge_index,
        edge_features=edge_features,
        edge_ids=tuple(identifiers),
        tokens=tuple(unit.surfaces),
        dependencies=tuple(dependencies),
    )


def _local_head(head_idx: int, offset: int, length: int) -> int | None:
    if head_idx == ROOT_HEAD:
        return None
    local = head_idx - offset
    # spaCy occasionally points a token's head outside the sentence it
    # assigned the token to. Dropping the arc is right: an edge to a node the
    # graph does not contain would index out of bounds during aggregation.
    return local if 0 <= local < length else None


def _entity_slots(mentions: tuple[TaggedMention, ...], offset: int, length: int) -> list[str]:
    slots = ["O"] * length
    for mention in mentions:
        for index in range(mention.token_start - offset, mention.token_end - offset):
            if 0 <= index < length:
                slots[index] = mention.entity_type
    return slots


def candidate_pairs(
    mentions: tuple[TaggedMention, ...],
    offset: int,
    *,
    max_pairs: int = MAX_PAIRS_PER_SENTENCE,
) -> list[CandidatePair]:
    """Ordered pairs of argument-typed mentions in one sentence.

    Ordered, not unordered: `OWNED_BY` and `WIRED_FUNDS_TO` are directional,
    and "Meridian owns Advent" is a different claim from its reverse. The
    model decides direction by seeing the pair in a fixed order and choosing a
    class, rather than by a separate direction head.

    Mentions of the same entity are not paired with themselves — a sentence
    naming one company twice does not assert a relation between it and itself.
    """
    arguments = [m for m in mentions if m.entity_type in ARGUMENT_TYPES]
    pairs: list[CandidatePair] = []

    for subject in arguments:
        for obj in arguments:
            if subject is obj or subject.surface == obj.surface:
                continue
            pairs.append(
                CandidatePair(
                    subject_tokens=tuple(
                        range(subject.token_start - offset, subject.token_end - offset)
                    ),
                    object_tokens=tuple(range(obj.token_start - offset, obj.token_end - offset)),
                    subject_key=subject.surface,
                    object_key=obj.surface,
                    subject_type=subject.entity_type,
                    object_type=obj.entity_type,
                )
            )
            if len(pairs) >= max_pairs:
                return pairs
    return pairs
