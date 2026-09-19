"""Relation inference over a tagged document. Plan §4 Stage 3.

The runtime counterpart of `train.py`: one tagged document in, a list of
draft insights out, each carrying a byte-exact citation and the trust triple.

Two things it does *not* do, on purpose:

- **It does not resolve entities.** A draft names its subject and object by
  the mentions the tagger produced; `workers/pipeline.py` maps those onto
  `entities` rows through the coreference layer it already built for the
  document. Doing it here would mean a second resolver with its own idea of
  who is who.
- **It does not decide routing.** That is `ml/cascade/routing.py`, which
  needs the whole document set to see the ownership cycle, and Stage 6's
  cascade on top of that.

Which model runs is `RELATION_MODEL=gat|rules` (plan §1.3). Both satisfy the
same protocol and produce the same `RelationOutput`, so nothing downstream —
including the ablation endpoint — knows the difference.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
from torch import nn

from core.config import Settings, get_settings
from core.logging import get_logger
from ml.relations.graph_builder import build_sentence_graph, candidate_pairs
from ml.relations.interface import (
    RELATIONS,
    AttentionEdge,
    CandidatePair,
    EdgeMask,
    RelationOutput,
    SentenceGraph,
)
from ml.relations.rules import RuleRelationModel
from ml.tagger.infer import DocumentTagging, TaggedMention
from ml.text.tokenize import TokenizedDoc

log = get_logger(__name__)

CHECKPOINT_TEMPLATE = "relations_{name}.pt"


class RelationModelUnavailable(RuntimeError):
    """No checkpoint for the configured relation backend.

    Raised rather than falling back to random weights: an untrained relation
    model produces confident nonsense, and a demo running on it would look
    like it worked.
    """


@dataclass(slots=True)
class DraftInsight:
    """One extracted claim, before it has an entity id or a routing bucket."""

    sentence_index: int
    char_start: int
    char_end: int
    sentence_text: str
    relation: str
    subject: TaggedMention
    object: TaggedMention
    confidence: float
    vacuity: float
    dissonance: float
    logits: list[float]
    attention: list[dict[str, Any]] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)

    @property
    def pair_key(self) -> tuple[str, str, str]:
        return (self.subject.surface, self.object.surface, self.relation)


@dataclass(frozen=True, slots=True)
class SentenceContext:
    """Everything the ablation engine needs to re-infer one insight later.

    Stage 7 rebuilds this from the stored sentence rather than caching it —
    the parse is content-addressed and the tagger is deterministic, so a
    rebuild is cheap and cannot drift from what the citation says.
    """

    graph: SentenceGraph
    pair: CandidatePair
    lemmas: tuple[str, ...]


class RelationExtractor:
    """The loaded relation model plus the plumbing around it."""

    def __init__(self, model: nn.Module, name: str, settings: Settings) -> None:
        self.model = model
        self.name = name
        self.settings = settings
        # Same reasoning as the tagger's: sync handlers run in FastAPI's
        # threadpool, so two requests can enter a forward pass at once.
        self._lock = threading.Lock()

    @classmethod
    def load(cls, settings: Settings | None = None) -> RelationExtractor:
        from ml.relations.train import load as load_checkpoint

        settings = settings or get_settings()
        name = str(settings.relation_model)
        path = checkpoint_path(name, settings)
        if not path.exists():
            raise RelationModelUnavailable(
                f"no {name} relation checkpoint at {path}. Run "
                "`make train-relations` (or `python -m scripts.train_relations`)."
            )
        model, metrics = load_checkpoint(path)
        log.info(
            "relation_model_loaded",
            model=name,
            path=str(path),
            f1=metrics.get("f1"),
            parameters=sum(p.numel() for p in model.parameters()),
        )
        return cls(model, name, settings)

    # ── inference ────────────────────────────────────────────────────────────

    def extract(self, doc: TokenizedDoc, tagging: DocumentTagging) -> list[DraftInsight]:
        """Every relation this document asserts, one per (pair, sentence)."""
        drafts: list[DraftInsight] = []
        with self._lock:
            for sentence_index, encoding in enumerate(tagging.sentences):
                drafts.extend(self._extract_sentence(doc, tagging, sentence_index, encoding))
        return drafts

    def _extract_sentence(
        self,
        doc: TokenizedDoc,
        tagging: DocumentTagging,
        sentence_index: int,
        encoding: Any,
    ) -> list[DraftInsight]:
        mentions = tagging.mentions_in_sentence(sentence_index)
        offset = encoding.unit.token_offset
        pairs = candidate_pairs(mentions, offset)
        if not pairs:
            return []

        graph = build_sentence_graph(doc, encoding, mentions)
        lemmas = tuple(token.lemma for token in encoding.unit.tokens)
        logits = self._score(graph, pairs, lemmas)
        attention = self._attention(graph, pairs, lemmas)

        span = encoding.unit.span
        sentence_text = doc.text[span.char_start : span.char_end]
        by_mention = {(m.token_start - offset, m.token_end - offset): m for m in mentions}

        best: dict[tuple[str, str, str], DraftInsight] = {}
        for position, pair in enumerate(pairs):
            relation = RELATIONS[int(logits[position].argmax())]
            if relation == "NO_RELATION":
                continue

            subject = by_mention.get((pair.subject_tokens[0], pair.subject_tokens[-1] + 1))
            obj = by_mention.get((pair.object_tokens[0], pair.object_tokens[-1] + 1))
            if subject is None or obj is None:  # pragma: no cover - defensive
                continue

            from ml.evidential.uncertainty import trust_of

            trust = trust_of(logits[position])
            draft = DraftInsight(
                sentence_index=sentence_index,
                char_start=span.char_start,
                char_end=span.char_end,
                sentence_text=sentence_text,
                relation=relation,
                subject=subject,
                object=obj,
                confidence=trust.confidence,
                vacuity=trust.vacuity,
                dissonance=trust.dissonance,
                logits=[round(float(v), 6) for v in logits[position]],
                attention=[
                    edge.as_dict()
                    for edge in sorted(attention[position], key=lambda e: -e.weight)[
                        : self.settings.attention_edges_stored
                    ]
                ],
                tokens=list(graph.tokens),
            )

            # One sentence naming the same two companies twice asserts the
            # claim once. Keeping the more confident reading is arbitrary but
            # stable, and the alternative is two identical rows in the feed.
            existing = best.get(draft.pair_key)
            if existing is None or draft.confidence > existing.confidence:
                best[draft.pair_key] = draft

        return list(best.values())

    def _score(
        self, graph: SentenceGraph, pairs: list[CandidatePair], lemmas: tuple[str, ...]
    ) -> torch.Tensor:
        self.model.eval()
        with torch.no_grad():
            if isinstance(self.model, RuleRelationModel):
                return self.model.score_pairs(graph, pairs, lemmas)
            logits, _ = self.model.score_pairs(graph, pairs)
            return logits

    def _attention(
        self, graph: SentenceGraph, pairs: list[CandidatePair], lemmas: tuple[str, ...]
    ) -> list[tuple[AttentionEdge, ...]]:
        """Per-pair attention edges.

        The GAT's attention is a property of the graph, not of the pair, so
        one encode serves every pair in the sentence. The rule model's
        "attention" is the dependency path it used, which *is* per-pair.
        """
        with torch.no_grad():
            if isinstance(self.model, RuleRelationModel):
                return [self.infer(graph, pair, lemmas=lemmas).attention for pair in pairs]
            encoding = self.model.encode(graph)
            from ml.relations.gat import build_output

            shared = build_output(
                torch.zeros(len(RELATIONS)), graph, encoding.edge_attention
            ).attention
            return [shared for _ in pairs]

    # ── the ablation entry point ─────────────────────────────────────────────

    def infer(
        self,
        graph: SentenceGraph,
        pair: CandidatePair,
        edge_mask: EdgeMask | None = None,
        lemmas: tuple[str, ...] = (),
    ) -> RelationOutput:
        """Single-pair inference, optionally under a mask.

        This is what Stage 7's ablation engine calls, through the
        `RelationModel` protocol. It never learns which backend is loaded.
        """
        if isinstance(self.model, RuleRelationModel):
            return self.model.infer(graph, pair, edge_mask, lemmas=lemmas)
        return self.model.infer(graph, pair, edge_mask)


def checkpoint_path(name: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.path(settings.checkpoint_dir) / CHECKPOINT_TEMPLATE.format(name=name)


# ── process-wide handle ──────────────────────────────────────────────────────

_EXTRACTOR: RelationExtractor | None = None
_LOAD_LOCK = threading.Lock()


def get_extractor(settings: Settings | None = None) -> RelationExtractor:
    global _EXTRACTOR
    if _EXTRACTOR is None:
        with _LOAD_LOCK:
            if _EXTRACTOR is None:
                _EXTRACTOR = RelationExtractor.load(settings)
    return _EXTRACTOR


def reset_extractor() -> None:
    """Drop the cached model. Used by tests and after retraining."""
    global _EXTRACTOR
    with _LOAD_LOCK:
        _EXTRACTOR = None


def sentence_context(
    doc: TokenizedDoc,
    tagging: DocumentTagging,
    sentence_index: int,
    subject: TaggedMention,
    obj: TaggedMention,
) -> SentenceContext:
    """Rebuild the graph and pair for one stored insight (Stage 7)."""
    encoding = tagging.sentences[sentence_index]
    offset = encoding.unit.token_offset
    mentions = tagging.mentions_in_sentence(sentence_index)
    return SentenceContext(
        graph=build_sentence_graph(doc, encoding, mentions),
        pair=CandidatePair(
            subject_tokens=tuple(range(subject.token_start - offset, subject.token_end - offset)),
            object_tokens=tuple(range(obj.token_start - offset, obj.token_end - offset)),
            subject_key=subject.surface,
            object_key=obj.surface,
            subject_type=subject.entity_type,
            object_type=obj.entity_type,
        ),
        lemmas=tuple(token.lemma for token in encoding.unit.tokens),
    )
