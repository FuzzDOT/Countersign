"""The rule-based relation extractor. Plan §1.3 and §4 Stage 3.

Written in the same stage as the GAT rather than held in reserve, because the
brief's hour-9 cut list says to fall back to rules if the GAT is shaky — and a
fallback you have not written is a plan, not a fallback. Having both means the
decision gate is `RELATION_MODEL=gat|rules` in config instead of a rewrite at
the worst possible hour.

It is a *featurizer*, not an if-ladder. Verb lemmas, prepositions, voice and
the shape of the dependency path between the two arguments become a feature
vector, which goes through the **same evidential head** as the GAT. That is
what keeps the two commensurable: confidence, vacuity and dissonance mean the
same thing whichever is running, so the gate, the fragility correlation and
the calibration curve do not have to care.

**It honours `edge_mask`.** Masked arcs are removed from the adjacency before
the path search, so ablating an edge produces a real counterfactual here too —
the path either lengthens, reroutes, or disappears, and the features change
accordingly. That is the whole reason claim 4 survives the hour-9 cut.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import torch
from torch import Tensor, nn

from ml.evidential.head import EvidentialHead
from ml.evidential.uncertainty import trust_from_logits
from ml.relations.gat import build_output
from ml.relations.interface import (
    RELATIONS,
    CandidatePair,
    EdgeMask,
    RelationOutput,
    SentenceGraph,
)

# Verbs that carry the relation set. Lemmas, so `wired`/`wires`/`wiring` all
# land on `wire`. Curated rather than learned: the corpus is ours and this is
# the vocabulary its templates use, which is exactly the honest limitation of
# a rule model and is stated as such.
VERB_LEXICON: tuple[str, ...] = (
    "wire",
    "transfer",
    "remit",
    "send",
    "pay",
    "route",
    "settle",
    "disburse",
    "invoice",
    "bill",
    "charge",
    "submit",
    "issue",
    "own",
    "hold",
    "control",
    "acquire",
    "register",
    "incorporate",
    "share",
    "list",
    "operate",
    "sign",
    "authorize",
    "execute",
    "certify",
)

PREPOSITIONS: tuple[str, ...] = (
    "to",
    "from",
    "through",
    "by",
    "for",
    "on",
    "with",
    "of",
    "behalf",
    "under",
)

# Nouns that nominalize a relation. Band C constructions ("payment routing
# through Z on behalf of X") have no finite verb linking the arguments, so
# without these the path features are empty for exactly the hardest band.
NOUN_CUES: tuple[str, ...] = (
    "payment",
    "invoice",
    "transfer",
    "remittance",
    "owner",
    "ownership",
    "subsidiary",
    "parent",
    "address",
    "premises",
    "signatory",
    "director",
    "behalf",
    "registration",
    "shareholder",
    "settlement",
)

ARGUMENT_DEPS: tuple[str, ...] = (
    "nsubj",
    "nsubjpass",
    "dobj",
    "pobj",
    "compound",
    "conj",
    "poss",
    "appos",
)

PATH_BUCKETS = 5
ENTITY_TYPES_ORDER: tuple[str, ...] = ("ORG", "PERSON")

FEATURE_DIM = (
    len(VERB_LEXICON)
    + len(PREPOSITIONS)
    + len(NOUN_CUES)
    + 2 * (len(ARGUMENT_DEPS) + 1)
    + 2 * len(ENTITY_TYPES_ORDER)
    + PATH_BUCKETS
    + 5  # passive, subject-first, path found, direct arc, normalized length
)


@dataclass(frozen=True, slots=True)
class DependencyPath:
    """The arc path between two argument tokens, if one survives the mask."""

    nodes: tuple[int, ...]
    found: bool

    @property
    def length(self) -> int:
        return max(len(self.nodes) - 1, 0)


def _adjacency(graph: SentenceGraph, edge_mask: EdgeMask | None) -> dict[int, list[int]]:
    """Undirected adjacency over the surviving dependency arcs.

    Undirected because a dependency *path* between two tokens routes up to a
    common ancestor and back down; following arc direction only would find
    nothing for the overwhelmingly common subject/object case.
    """
    keep = graph.mask_vector(edge_mask)
    neighbours: dict[int, list[int]] = {}
    sources = graph.edge_index[0].tolist()
    destinations = graph.edge_index[1].tolist()

    for position, (src, dst) in enumerate(zip(sources, destinations, strict=True)):
        if src == dst:
            continue
        if keep is not None and not bool(keep[position]):
            continue
        neighbours.setdefault(src, []).append(dst)
        neighbours.setdefault(dst, []).append(src)
    return neighbours


def shortest_path(
    graph: SentenceGraph, start: int, goal: int, edge_mask: EdgeMask | None = None
) -> DependencyPath:
    """BFS over the surviving arcs. Sentences are tens of nodes, so BFS is it."""
    if start == goal:
        return DependencyPath(nodes=(start,), found=True)

    neighbours = _adjacency(graph, edge_mask)
    previous: dict[int, int] = {start: start}
    queue = deque([start])

    while queue:
        node = queue.popleft()
        for neighbour in neighbours.get(node, ()):
            if neighbour in previous:
                continue
            previous[neighbour] = node
            if neighbour == goal:
                path = [goal]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return DependencyPath(nodes=tuple(reversed(path)), found=True)
            queue.append(neighbour)

    return DependencyPath(nodes=(), found=False)


def head_token(graph: SentenceGraph, span: tuple[int, ...]) -> int:
    """The token in a span that attaches to the rest of the sentence.

    For `Meridian Supply LLC` that is `LLC`, whose head is the verb; the other
    two are `compound` children of it. Taking the first token instead would
    make every path two arcs longer and blur the subject/object distinction.
    """
    inside = set(span)
    for index in span:
        head = _syntactic_head(graph, index)
        if head is None or head not in inside:
            return index
    return span[-1] if span else 0


def _syntactic_head(graph: SentenceGraph, index: int) -> int | None:
    """The destination of the child→head arc leaving `index`, if any."""
    sources = graph.edge_index[0].tolist()
    destinations = graph.edge_index[1].tolist()
    directions = graph.edge_features.argmax(dim=1).tolist()
    for position, source in enumerate(sources):
        if source == index and directions[position] == 0:
            return destinations[position]
    return None


def extract_features(
    graph: SentenceGraph,
    pair: CandidatePair,
    lemmas: tuple[str, ...],
    edge_mask: EdgeMask | None = None,
) -> Tensor:
    """The hand-crafted feature vector. [FEATURE_DIM]"""
    features = torch.zeros(FEATURE_DIM)
    cursor = 0

    subject = head_token(graph, pair.subject_tokens)
    obj = head_token(graph, pair.object_tokens)
    path = shortest_path(graph, subject, obj, edge_mask)
    on_path = set(path.nodes)

    # Verbs on the path.
    for index, verb in enumerate(VERB_LEXICON):
        if any(_lemma(lemmas, node) == verb for node in on_path):
            features[cursor + index] = 1.0
    cursor += len(VERB_LEXICON)

    # Prepositions on the path.
    for index, preposition in enumerate(PREPOSITIONS):
        if any(_lemma(lemmas, node) == preposition for node in on_path):
            features[cursor + index] = 1.0
    cursor += len(PREPOSITIONS)

    # Nominalization cues anywhere in the sentence, not just on the path: a
    # band-C construction puts the relation in a noun that may hang off the
    # clause rather than sit between the arguments.
    sentence_lemmas = set(lemmas)
    for index, noun in enumerate(NOUN_CUES):
        if noun in sentence_lemmas:
            features[cursor + index] = 1.0
    cursor += len(NOUN_CUES)

    # Dependency label of each argument's head token.
    for index in (subject, obj):
        label = graph.dependencies[_first_outgoing(graph, index)] if graph.n_edges else ""
        slot = ARGUMENT_DEPS.index(label) if label in ARGUMENT_DEPS else len(ARGUMENT_DEPS)
        features[cursor + slot] = 1.0
        cursor += len(ARGUMENT_DEPS) + 1

    # Argument entity types. `SIGNATORY_OF` is the only relation with a person
    # subject, so this one feature carries most of that class.
    for entity_type in (pair.subject_type, pair.object_type):
        if entity_type in ENTITY_TYPES_ORDER:
            features[cursor + ENTITY_TYPES_ORDER.index(entity_type)] = 1.0
        cursor += len(ENTITY_TYPES_ORDER)

    # Path length, bucketed and continuous.
    bucket = min(max(path.length - 1, 0), PATH_BUCKETS - 1) if path.found else PATH_BUCKETS - 1
    features[cursor + bucket] = 1.0
    cursor += PATH_BUCKETS

    passive = any(
        graph.dependencies[position] in ("nsubjpass", "auxpass")
        for position in range(graph.n_edges)
        if graph.edge_index[0][position].item() in on_path
    )
    features[cursor + 0] = float(passive)
    features[cursor + 1] = float(subject < obj)
    features[cursor + 2] = float(path.found)
    features[cursor + 3] = float(path.length == 1)
    features[cursor + 4] = min(path.length / 10.0, 1.0) if path.found else 1.0

    return features


def _lemma(lemmas: tuple[str, ...], index: int) -> str:
    return lemmas[index] if 0 <= index < len(lemmas) else ""


def _first_outgoing(graph: SentenceGraph, node: int) -> int:
    sources = graph.edge_index[0].tolist()
    for position, source in enumerate(sources):
        if source == node:
            return position
    return 0


class RuleRelationModel(nn.Module):
    """Hand-crafted features into the shared evidential head.

    Implements `ml.relations.interface.RelationModel`. Roughly 1,500x smaller
    than the GAT and trains in seconds, which is what makes it a credible
    thing to switch to at hour 9 rather than a gesture.
    """

    name = "rules"

    def __init__(self, *, hidden: int = 64, n_classes: int = len(RELATIONS)) -> None:
        super().__init__()
        self.config = {"hidden": hidden, "n_classes": n_classes}
        self.head = EvidentialHead(FEATURE_DIM, n_classes, hidden=hidden)

    def score_pairs(
        self,
        graph: SentenceGraph,
        pairs: list[CandidatePair],
        lemmas: tuple[str, ...],
        edge_mask: EdgeMask | None = None,
    ) -> Tensor:
        if not pairs:
            return torch.zeros(0, len(RELATIONS))
        rows = torch.stack([extract_features(graph, pair, lemmas, edge_mask) for pair in pairs])
        return self.head(rows)

    @torch.no_grad()
    def infer(
        self,
        graph: SentenceGraph,
        pair: CandidatePair,
        edge_mask: EdgeMask | None = None,
        lemmas: tuple[str, ...] = (),
    ) -> RelationOutput:
        """Same signature as the GAT's, plus the lemmas it needs.

        `lemmas` is keyword-with-default rather than positional so the
        protocol's three-argument call still type-checks; the inference
        wrapper in `ml/relations/infer.py` always supplies them.
        """
        self.eval()
        logits = self.score_pairs(graph, [pair], lemmas, edge_mask)[0]
        # Attention is a GAT concept. Rather than fabricate one, the rule
        # model reports the *path* as its evidence: the arcs it actually used,
        # weighted equally. The ablation panel renders the same shape and the
        # counterfactual is just as real — masking one of these arcs reroutes
        # the path and changes the features.
        return build_output(logits, graph, self._path_attention(graph, pair, edge_mask))

    def _path_attention(
        self, graph: SentenceGraph, pair: CandidatePair, edge_mask: EdgeMask | None
    ) -> Tensor:
        path = shortest_path(
            graph,
            head_token(graph, pair.subject_tokens),
            head_token(graph, pair.object_tokens),
            edge_mask,
        )
        on_path = set(zip(path.nodes, path.nodes[1:], strict=False))
        weights = torch.zeros(graph.n_edges)
        if not on_path:
            return weights

        sources = graph.edge_index[0].tolist()
        destinations = graph.edge_index[1].tolist()
        for position, (src, dst) in enumerate(zip(sources, destinations, strict=True)):
            if (src, dst) in on_path or (dst, src) in on_path:
                weights[position] = 1.0
        total = weights.sum()
        return weights / total if total > 0 else weights


def trust_of_logits(logits: Tensor) -> tuple[float, float, float]:
    confidence, vacuity, dissonance = trust_from_logits(logits.reshape(1, -1))
    return float(confidence[0]), float(vacuity[0]), float(dissonance[0])
