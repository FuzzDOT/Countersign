"""Hand-rolled graph attention. Plan §1.2 — the highest-leverage decision here.

`torch_geometric.nn.GATConv` will hand back attention with
`return_attention_weights=True`, but claim 4 needs to **mask one named edge at
inference time and re-infer**. With PyG that means either mutating
`edge_index` — which changes the graph, not the attention, and is therefore a
different counterfactual from the one we are claiming — or forking its forward
pass.

Three layers, four heads, hidden 128 over a ~40-node sentence graph is about
this much pure torch. Writing it gives:

  * `edge_mask` as a first-class argument, so masking is a feature of the
    model rather than a hack around it
  * exact per-edge, per-layer attention, which is what `insights.attention`
    and the ablation panel render
  * one fewer dependency that has to install into a slim image at hour 6

The segment softmax is `index_reduce_`/`index_add_` over the destination
index — no scatter library, no sorting.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor, nn

from ml.evidential.head import EvidentialHead
from ml.evidential.uncertainty import trust_from_logits
from ml.relations.interface import (
    RELATIONS,
    AttentionEdge,
    CandidatePair,
    EdgeMask,
    RelationOutput,
    SentenceGraph,
)

NEG_INF = -1.0e9
EPSILON = 1e-16


@dataclass(frozen=True, slots=True)
class GraphEncoding:
    """One graph, encoded once, ready to read out any number of pairs."""

    node_embeddings: Tensor  # [N, hidden]
    graph_embedding: Tensor  # [hidden]
    # [layers, edges] — per-layer head-averaged attention. Kept per layer
    # because "which edge mattered" is a different answer at layer 1 (local
    # syntax) than at layer 3 (whole-clause structure), and the UI averages.
    layer_attention: Tensor

    @property
    def edge_attention(self) -> Tensor:
        """Layer-averaged weight per edge. What the UI shows."""
        if self.layer_attention.numel() == 0:
            return self.layer_attention
        return self.layer_attention.mean(dim=0)


class GATLayer(nn.Module):
    """One multi-head graph-attention layer with maskable edges."""

    def __init__(
        self,
        in_features: int,
        out_features: int,
        heads: int,
        edge_features: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.heads = heads
        self.out_features = out_features

        self.project = nn.Linear(in_features, heads * out_features, bias=False)
        # Separate source/destination/edge attention vectors, as in the
        # original GAT plus an edge term — the direction flag has to be able
        # to change the score or a bidirectional arc pair is indistinguishable.
        self.attend_src = nn.Parameter(torch.empty(heads, out_features))
        self.attend_dst = nn.Parameter(torch.empty(heads, out_features))
        self.attend_edge = nn.Parameter(torch.empty(heads, edge_features))
        self.leaky = nn.LeakyReLU(0.2)
        self.dropout = nn.Dropout(dropout)

        nn.init.xavier_uniform_(self.project.weight)
        for parameter in (self.attend_src, self.attend_dst, self.attend_edge):
            nn.init.xavier_uniform_(parameter)

    def forward(
        self,
        x: Tensor,
        edge_index: Tensor,
        edge_features: Tensor,
        keep: Tensor | None = None,
        uniform: bool = False,
    ) -> tuple[Tensor, Tensor]:
        """Returns `(node_output [N, heads*out], attention [E, heads])`."""
        n_nodes = x.size(0)
        src, dst = edge_index[0], edge_index[1]

        projected = self.project(x).view(n_nodes, self.heads, self.out_features)

        scores = (
            (projected[src] * self.attend_src).sum(-1)
            + (projected[dst] * self.attend_dst).sum(-1)
            + edge_features @ self.attend_edge.t()
        )
        scores = self.leaky(scores)

        if keep is not None:
            # Masked edges are pushed below the softmax floor rather than
            # deleted from `edge_index`. Deleting rows would renumber the
            # edges and break the `edge_id` the caller asked about — and it
            # would answer a different question, because a removed row also
            # removes the node pair from the graph's topology.
            scores = scores.masked_fill(~keep.unsqueeze(1), NEG_INF)

        attention = _segment_softmax(scores, dst, n_nodes)

        if uniform:
            attention = _segment_uniform(dst, n_nodes, self.heads, keep, x.dtype)

        attention = self.dropout(attention)
        messages = attention.unsqueeze(-1) * projected[src]

        out = torch.zeros(n_nodes, self.heads, self.out_features, dtype=x.dtype, device=x.device)
        out.index_add_(0, dst, messages)
        return out.reshape(n_nodes, self.heads * self.out_features), attention


def _segment_softmax(scores: Tensor, index: Tensor, n_segments: int) -> Tensor:
    """Softmax over the edges sharing a destination node.

    Max-subtracted per segment before exponentiating: without it, a node with
    many incoming edges overflows in fp32 once the scores grow during
    training, and the symptom is a loss that becomes nan at an unpredictable
    epoch.
    """
    heads = scores.size(1)
    maxima = torch.full((n_segments, heads), NEG_INF, dtype=scores.dtype, device=scores.device)
    maxima.index_reduce_(0, index, scores, "amax", include_self=False)

    shifted = (scores - maxima[index]).exp()
    denominator = torch.zeros(n_segments, heads, dtype=scores.dtype, device=scores.device)
    denominator.index_add_(0, index, shifted)
    return shifted / denominator[index].clamp_min(EPSILON)


def _segment_uniform(
    index: Tensor,
    n_segments: int,
    heads: int,
    keep: Tensor | None,
    dtype: torch.dtype,
) -> Tensor:
    """Equal weight across every surviving edge into each node.

    The `uniform` ablation mode. It asks a different question from `zero` —
    not "what if this link were absent" but "what if the model could not tell
    these links apart" — and the difference between the two answers is
    informative, so both are offered.
    """
    alive = (
        torch.ones(index.size(0), dtype=dtype, device=index.device)
        if keep is None
        else keep.to(dtype)
    )
    counts = torch.zeros(n_segments, dtype=dtype, device=index.device)
    counts.index_add_(0, index, alive)
    return (alive / counts[index].clamp_min(1.0)).unsqueeze(1).expand(-1, heads)


class GATRelationModel(nn.Module):
    """Three GAT layers, a pair readout, and the shared evidential head.

    Implements `ml.relations.interface.RelationModel`. The ablation endpoint
    is typed against that protocol and never learns which implementation is
    behind it (plan §1.3).
    """

    name = "gat"

    def __init__(
        self,
        node_features: int,
        *,
        hidden: int = 128,
        heads: int = 4,
        layers: int = 3,
        dropout: float = 0.3,
        edge_features: int = 3,
        n_classes: int = len(RELATIONS),
    ) -> None:
        super().__init__()
        if hidden % heads:
            raise ValueError(f"hidden {hidden} must divide evenly across {heads} heads")

        self.config = {
            "node_features": node_features,
            "hidden": hidden,
            "heads": heads,
            "layers": layers,
            "dropout": dropout,
            "edge_features": edge_features,
            "n_classes": n_classes,
        }
        self.hidden = hidden
        self.input_projection = nn.Linear(node_features, hidden)
        self.layers = nn.ModuleList(
            GATLayer(hidden, hidden // heads, heads, edge_features, dropout) for _ in range(layers)
        )
        self.activation = nn.ELU()
        self.dropout = nn.Dropout(dropout)
        # Readout is [subject ‖ object ‖ graph mean-pool]: the pair, plus the
        # context it sits in. Without the pooled term the model cannot see
        # the rest of the clause, which is where band-C nominalizations put
        # the relation.
        self.head = EvidentialHead(hidden * 3, n_classes, hidden=hidden)

    # ── encoding ─────────────────────────────────────────────────────────────

    def encode(self, graph: SentenceGraph, edge_mask: EdgeMask | None = None) -> GraphEncoding:
        keep = graph.mask_vector(edge_mask)
        uniform = edge_mask is not None and edge_mask.mode == "uniform" and bool(edge_mask)

        x = self.dropout(self.activation(self.input_projection(graph.node_features)))
        per_layer: list[Tensor] = []

        for index, layer in enumerate(self.layers):
            out, attention = layer(
                x, graph.edge_index, graph.edge_features, keep=keep, uniform=uniform
            )
            # Residual: three rounds of attention over a 40-node graph
            # otherwise oversmooths, and every token ends up with the same
            # embedding. Applied before the nonlinearity on all but the last
            # layer, which is the usual arrangement.
            x = out + x
            if index < len(self.layers) - 1:
                x = self.dropout(self.activation(x))
            per_layer.append(attention.mean(dim=1))

        layer_attention = torch.stack(per_layer) if per_layer else torch.zeros(0, graph.n_edges)
        return GraphEncoding(
            node_embeddings=x,
            graph_embedding=x.mean(dim=0),
            layer_attention=layer_attention,
        )

    def readout(self, encoding: GraphEncoding, pairs: list[CandidatePair]) -> Tensor:
        """[P, 3*hidden] features for a batch of pairs over one graph."""
        rows: list[Tensor] = []
        for pair in pairs:
            rows.append(
                torch.cat(
                    [
                        _mean_of(encoding.node_embeddings, pair.subject_tokens),
                        _mean_of(encoding.node_embeddings, pair.object_tokens),
                        encoding.graph_embedding,
                    ]
                )
            )
        if not rows:
            return torch.zeros(0, self.hidden * 3)
        return torch.stack(rows)

    def score_pairs(
        self,
        graph: SentenceGraph,
        pairs: list[CandidatePair],
        edge_mask: EdgeMask | None = None,
    ) -> tuple[Tensor, GraphEncoding]:
        """Logits for every pair in one sentence, from a single encode."""
        encoding = self.encode(graph, edge_mask)
        return self.head(self.readout(encoding, pairs)), encoding

    # ── the protocol ─────────────────────────────────────────────────────────

    @torch.no_grad()
    def infer(
        self,
        graph: SentenceGraph,
        pair: CandidatePair,
        edge_mask: EdgeMask | None = None,
    ) -> RelationOutput:
        self.eval()
        logits, encoding = self.score_pairs(graph, [pair], edge_mask)
        return build_output(logits[0], graph, encoding.edge_attention)


def _mean_of(embeddings: Tensor, indices: tuple[int, ...]) -> Tensor:
    """Mean over an entity's tokens, or a zero vector if it has none.

    An empty span is possible when a mention straddles the sentence boundary
    the tokenizer chose. Returning zeros keeps the pair classifiable — as
    NO_RELATION, almost certainly, which is the right answer for an argument
    the graph does not contain.
    """
    valid = [i for i in indices if 0 <= i < embeddings.size(0)]
    if not valid:
        return torch.zeros(embeddings.size(1), dtype=embeddings.dtype)
    return embeddings[torch.tensor(valid, dtype=torch.long)].mean(dim=0)


def build_output(logits: Tensor, graph: SentenceGraph, attention: Tensor) -> RelationOutput:
    """Assemble the response object from one row of logits."""
    confidence, vacuity, dissonance = trust_from_logits(logits.reshape(1, -1))
    predicted = RELATIONS[int(logits.argmax())]

    edges: list[AttentionEdge] = []
    if attention.numel():
        sources = graph.edge_index[0].tolist()
        destinations = graph.edge_index[1].tolist()
        for position, identifier in enumerate(graph.edge_ids):
            src, dst = sources[position], destinations[position]
            if src == dst:
                # Self-loops carry attention but are not a syntactic link, so
                # ablating one is not a counterfactual anybody can interpret.
                continue
            edges.append(
                AttentionEdge(
                    edge_id=identifier,
                    src_token=graph.tokens[src],
                    dst_token=graph.tokens[dst],
                    weight=float(attention[position]),
                    src_idx=src,
                    dst_idx=dst,
                )
            )

    return RelationOutput(
        relation=predicted,
        logits=logits.detach(),
        confidence=float(confidence[0]),
        vacuity=float(vacuity[0]),
        dissonance=float(dissonance[0]),
        attention=tuple(edges),
    )
