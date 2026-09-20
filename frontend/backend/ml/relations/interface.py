"""The relation-model contract, defined before either implementation exists.

Plan §1.3. The brief's cut list says: if the GAT is shaky at hour 9, fall back
to rule-based extraction over dependency paths. Taken literally that kills
claim 4 — rule-based extraction has no attention to ablate, and claim 4 is one
of the two things nobody else in the room can say.

So **ablation targets this interface, not the GAT**. Both implementations
accept an `EdgeMask` and produce a real counterfactual:

  * `GATRelationModel` zeroes the masked edges' attention logits and
    renormalizes the segment-softmax (`mode="zero"`), or replaces the
    surviving weights with uniform attention (`mode="uniform"`).
  * `RuleRelationModel` deletes those dependency arcs from the path-feature
    extractor before computing features, then runs the same evidential head
    over the resulting vector.

`POST /ablation/insights/{id}` never learns which model is behind it, and the
hour-9 decision gate becomes a config flag (`RELATION_MODEL=gat|rules`)
rather than a rewrite.

Edge ids are strings of the form `"src->dst"` over *sentence-local* token
indices, because that is what the ablation panel sends back and what
`insights.attention` stores. They have to be stable across a re-parse of the
same sentence, which they are: the tokenizer is deterministic and the ids are
derived from token positions rather than from anything model-side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import torch
from torch import Tensor

# Brief §15's relation set plus the null class. Order is the output layer's
# order: appending is safe, reordering invalidates every checkpoint.
RELATIONS: tuple[str, ...] = (
    "NO_RELATION",
    "WIRED_FUNDS_TO",
    "OWNED_BY",
    "INVOICED",
    "SHARES_ADDRESS_WITH",
    "SIGNATORY_OF",
)
RELATION_INDEX: dict[str, int] = {name: index for index, name in enumerate(RELATIONS)}
N_RELATIONS = len(RELATIONS)
NO_RELATION = "NO_RELATION"

MaskMode = Literal["zero", "uniform"]


def edge_id(src_idx: int, dst_idx: int) -> str:
    """The identifier the API and the ablation panel exchange."""
    return f"{src_idx}->{dst_idx}"


def parse_edge_id(value: str) -> tuple[int, int] | None:
    """Inverse of `edge_id`, tolerant of anything that is not one.

    Returns None rather than raising: `masked_edges` is client input, and an
    unrecognized id should produce "that edge is not in this graph" at the
    endpoint rather than a 500 from a parser.
    """
    head, separator, tail = value.partition("->")
    if not separator or not head.isdigit() or not tail.isdigit():
        return None
    return int(head), int(tail)


@dataclass(frozen=True, slots=True)
class EdgeMask:
    """Which edges to remove, and how.

    `zero` is the honest counterfactual — the edge contributes nothing and the
    remaining attention renormalizes, which is what "remove this syntactic
    link" means. `uniform` answers a different question: what if the model
    could not tell these edges apart? Both are offered because the difference
    between them is informative, and the response records which was used.
    """

    edge_ids: frozenset[str]
    mode: MaskMode = "zero"

    def __bool__(self) -> bool:
        return bool(self.edge_ids)

    @classmethod
    def of(cls, ids: list[str] | tuple[str, ...], mode: MaskMode = "zero") -> EdgeMask:
        return cls(edge_ids=frozenset(ids), mode=mode)


@dataclass(frozen=True, slots=True)
class AttentionEdge:
    """One edge's attention weight, as `insights.attention` stores it."""

    edge_id: str
    src_token: str
    dst_token: str
    weight: float
    src_idx: int
    dst_idx: int

    def as_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "src_token": self.src_token,
            "dst_token": self.dst_token,
            "weight": round(self.weight, 6),
            "src_idx": self.src_idx,
            "dst_idx": self.dst_idx,
        }


@dataclass(frozen=True, slots=True)
class RelationOutput:
    """What both models return for one candidate pair.

    `logits` is kept raw because Stage 9's temperature scaling refits on
    logged logits rather than re-running inference over the corpus, and
    `insights.evidence_logits` is where they live.
    """

    relation: str
    logits: Tensor  # [N_RELATIONS], pre-softplus evidence logits
    confidence: float
    vacuity: float
    dissonance: float
    attention: tuple[AttentionEdge, ...] = ()

    @property
    def is_relation(self) -> bool:
        return self.relation != NO_RELATION

    def top_edges(self, limit: int = 12) -> tuple[AttentionEdge, ...]:
        return tuple(sorted(self.attention, key=lambda e: -e.weight)[:limit])


@dataclass(frozen=True, slots=True)
class CandidatePair:
    """Two entity mentions in one sentence, in subject-object order."""

    subject_tokens: tuple[int, ...]  # sentence-local token indices
    object_tokens: tuple[int, ...]
    subject_key: str  # the coref cluster key, for bookkeeping upstream
    object_key: str
    subject_type: str
    object_type: str


@dataclass(frozen=True, slots=True)
class SentenceGraph:
    """A parsed sentence as a graph the relation models consume.

    Nodes are tokens. Edges are dependency arcs, stored bidirectionally with a
    direction flag, plus a self-loop per node so an isolated token keeps its
    own representation through the layers.
    """

    # [N, F] node features: tagger hidden ‖ entity one-hot ‖ POS one-hot ‖ position
    node_features: Tensor
    # [2, E] source and destination indices, sentence-local
    edge_index: Tensor
    # [E, edge_feature_dim] direction flag and dependency-label features
    edge_features: Tensor
    # Parallel to the edge columns, so an edge can be masked by name.
    edge_ids: tuple[str, ...]
    tokens: tuple[str, ...]
    dependencies: tuple[str, ...] = field(default=())

    @property
    def n_nodes(self) -> int:
        return int(self.node_features.size(0))

    @property
    def n_edges(self) -> int:
        return int(self.edge_index.size(1))

    def mask_vector(self, mask: EdgeMask | None) -> Tensor | None:
        """[E] boolean: True for edges that survive.

        Returns None when nothing is masked, so the models can skip the whole
        branch on the hot path — every ingest inference is unmasked.
        """
        if mask is None or not mask:
            return None
        keep = [identifier not in mask.edge_ids for identifier in self.edge_ids]
        return torch.tensor(keep, dtype=torch.bool)

    def known_edge_ids(self) -> frozenset[str]:
        return frozenset(self.edge_ids)


@runtime_checkable
class RelationModel(Protocol):
    """What `POST /ablation/insights/{id}` talks to.

    Deliberately narrow: one method, one optional mask. Anything wider would
    let the ablation engine reach into the GAT's internals and stop working
    the day the config flag flips to `rules`.
    """

    name: str

    def infer(
        self,
        graph: SentenceGraph,
        pair: CandidatePair,
        edge_mask: EdgeMask | None = None,
    ) -> RelationOutput: ...
