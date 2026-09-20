"""Tagger inference: a parsed document in, mentions with exact offsets out.

One forward pass produces three things the rest of the pipeline needs, so it
happens once and is carried around rather than recomputed:

  * BIO paths -> entity spans
  * per-token CRF marginals -> `mentions.tagger_conf`
  * BiLSTM hidden states -> Stage 3's sentence-graph node features

**Character offsets are looked up, never searched.** A predicted span is a
range of token indices; its offsets are `tokens[i].char_start` and
`tokens[j].char_end`, recorded by the tokenizer against the document's own
text. `surface` is then a slice at those offsets, so
`raw_text[char_start:char_end] == surface` holds by construction and
`verify()` proves it on every document.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor

from core.config import Settings, get_settings
from core.logging import get_logger
from ml.tagger.data import SentenceUnit, encode_batch, iter_sentences
from ml.tagger.labels import TAG_INDEX, TAGS, TokenSpan, tags_to_spans
from ml.tagger.model import BiLSTMCRFTagger, load_checkpoint
from ml.tagger.vocab import Vocab
from ml.text.tokenize import TokenizedDoc

log = get_logger(__name__)

CHECKPOINT_NAME = "tagger.pt"

# Entity types that name a party. A company or a person is capitalized in
# English, and the tagger's residual errors on unseen text are almost all
# lowercase common nouns promoted to ORG — `remittance`, `attributable`. Each
# one becomes a node in the graph and a candidate in every pair in its
# sentence, so the cost of keeping them is paid twice.
#
# Applied here rather than inside the model, and deliberately *not* applied
# in `ml/tagger/train.py`'s evaluation: the reported F1 should measure the
# model, not the model plus a filter.
NAMED_TYPES = frozenset({"ORG", "PERSON"})

# Sentences per forward pass. Bounded because a 120-document scenario has
# ~900 sentences and one batch of those is a 900 x 60 x 512 hidden tensor.
INFER_BATCH_SIZE = 64


class TaggerUnavailable(RuntimeError):
    """No checkpoint on disk.

    Raised rather than falling back to an untrained model: an untrained tagger
    produces plausible-looking garbage spans, and a demo that quietly runs on
    random weights is worse than one that refuses to start.
    """


@dataclass(frozen=True, slots=True)
class TaggedMention:
    """One predicted entity, in document coordinates."""

    surface: str
    entity_type: str
    char_start: int
    char_end: int
    confidence: float
    sentence_index: int
    # Global token indices into `TokenizedDoc.tokens`, half-open.
    token_start: int
    token_end: int


@dataclass(frozen=True, slots=True)
class SentenceEncoding:
    """Everything the model produced for one sentence."""

    unit: SentenceUnit
    tags: tuple[str, ...]
    spans: tuple[TokenSpan, ...]
    # [T, n_tags] posterior; [T, 2*hidden] encoder states; [T, local_dim]
    # pre-BiLSTM features. All trimmed to the sentence's real length, so no
    # consumer has to know about padding.
    #
    # `local` is what Stage 3's sentence graph is built from. `hidden`
    # already encodes the whole sentence, which would make the graph's edges
    # redundant and its attention non-causal — see
    # `BiLSTMCRFTagger.embed_tokens`.
    marginals: Tensor
    hidden: Tensor
    local: Tensor


@dataclass(frozen=True, slots=True)
class DocumentTagging:
    doc: TokenizedDoc
    sentences: tuple[SentenceEncoding, ...]
    mentions: tuple[TaggedMention, ...]

    def verify(self) -> None:
        """Every mention must slice back to the surface it claims."""
        from ml.text.tokenize import OffsetIntegrityError

        for mention in self.mentions:
            actual = self.doc.text[mention.char_start : mention.char_end]
            if actual != mention.surface:
                raise OffsetIntegrityError(
                    f"mention [{mention.char_start}:{mention.char_end}] claims "
                    f"{mention.surface!r} but the text holds {actual!r}"
                )

    def mentions_in_sentence(self, sentence_index: int) -> tuple[TaggedMention, ...]:
        return tuple(m for m in self.mentions if m.sentence_index == sentence_index)


class EntityTagger:
    """Loaded model plus its vocabulary. Thread-safe for inference."""

    def __init__(self, model: BiLSTMCRFTagger, vocab: Vocab, metrics: dict[str, Any]) -> None:
        self.model = model
        self.vocab = vocab
        self.metrics = metrics
        # Route handlers run in FastAPI's threadpool (plan §1.1), so two
        # requests can enter a forward pass at once. torch modules are not
        # guaranteed re-entrant; one lock is cheaper than the class of bug it
        # prevents, and inference here is milliseconds.
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Path) -> EntityTagger:
        if not path.exists():
            raise TaggerUnavailable(
                f"no tagger checkpoint at {path}. Run `make train-tagger` "
                "(or `python -m scripts.train_tagger`) to build one."
            )
        model, vocab_payload, metrics = load_checkpoint(path)
        log.info(
            "tagger_loaded",
            path=str(path),
            # Same collision as `ml/tagger/train.py`'s `tagger_training_done`
            # event, and the more frequent of the two: this fires on every
            # command that loads the checkpoint, not just a training run.
            tagging_f1=metrics.get("token_f1"),
            parameters=sum(p.numel() for p in model.parameters()),
        )
        return cls(model, Vocab.from_dict(vocab_payload), metrics)

    # ── inference ────────────────────────────────────────────────────────────

    def tag(self, doc: TokenizedDoc) -> DocumentTagging:
        units = list(iter_sentences(doc))
        if not units:
            return DocumentTagging(doc=doc, sentences=(), mentions=())

        encodings: list[SentenceEncoding] = []
        with self._lock:
            for start in range(0, len(units), INFER_BATCH_SIZE):
                encodings.extend(self._tag_batch(units[start : start + INFER_BATCH_SIZE]))

        mentions = tuple(
            mention
            for position, encoding in enumerate(encodings)
            for mention in self._mentions_of(doc, encoding, position)
            if is_plausible(mention)
        )
        tagging = DocumentTagging(doc=doc, sentences=tuple(encodings), mentions=mentions)
        # Cheap, and it is the assertion that keeps every citation in the demo
        # honest. Loud failure, no tolerance (plan §3).
        tagging.verify()
        return tagging

    def _tag_batch(self, units: list[SentenceUnit]) -> list[SentenceEncoding]:
        batch = encode_batch(
            [unit.surfaces for unit in units],
            self.vocab,
            max_word_len=self.model.config.max_word_len,
        )
        paths, marginals, hidden, local = self.model.decode(
            batch.word_ids, batch.char_ids, batch.mask
        )

        out: list[SentenceEncoding] = []
        for position, unit in enumerate(units):
            length = len(unit)
            tags = tuple(TAGS[tag] for tag in paths[position][:length])
            token_marginals = marginals[position, :length]
            # Confidence per token is the posterior of the tag Viterbi chose,
            # not the max — those differ whenever the transition structure
            # overrides a locally preferred tag, and reporting the max there
            # would overstate certainty.
            confidences = [
                float(token_marginals[index, TAG_INDEX[tag]]) for index, tag in enumerate(tags)
            ]
            out.append(
                SentenceEncoding(
                    unit=unit,
                    tags=tags,
                    spans=tuple(tags_to_spans(list(tags), confidences)),
                    marginals=token_marginals.clone(),
                    hidden=hidden[position, :length].clone(),
                    local=local[position, :length].clone(),
                )
            )
        return out

    @staticmethod
    def _mentions_of(
        doc: TokenizedDoc, encoding: SentenceEncoding, sentence_index: int
    ) -> list[TaggedMention]:
        offset = encoding.unit.token_offset
        mentions: list[TaggedMention] = []
        for span in encoding.spans:
            first = doc.tokens[offset + span.token_start]
            last = doc.tokens[offset + span.token_end - 1]
            mentions.append(
                TaggedMention(
                    surface=doc.text[first.char_start : last.char_end],
                    entity_type=span.entity_type,
                    char_start=first.char_start,
                    char_end=last.char_end,
                    confidence=span.confidence,
                    sentence_index=sentence_index,
                    token_start=offset + span.token_start,
                    token_end=offset + span.token_end,
                )
            )
        return mentions


# ── process-wide handle ──────────────────────────────────────────────────────

_TAGGER: EntityTagger | None = None
_LOAD_LOCK = threading.Lock()


def is_plausible(mention: TaggedMention) -> bool:
    """Reject a named-entity span that cannot be a name.

    One rule: a party's surface has to contain a capitalized token. It costs
    nothing on real names — every company and person in the corpus is
    capitalized, including the held-out pool — and it removes the tagger's
    characteristic out-of-distribution error, which is to promote an unseen
    lowercase noun to ORG.

    Value types are left alone. `$48,200` and `2026-09-14` are not
    capitalized and are not supposed to be.
    """
    if mention.entity_type not in NAMED_TYPES:
        return True
    return any(character.isupper() for character in mention.surface)


def checkpoint_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.path(settings.checkpoint_dir) / CHECKPOINT_NAME


def get_tagger(settings: Settings | None = None) -> EntityTagger:
    """Load the tagger once per process.

    Not an `lru_cache`, because the double-checked lock is what stops two
    concurrent ingest jobs from each paying the load cost on a cold start —
    `lru_cache` is not atomic across threads for a slow factory.
    """
    global _TAGGER
    if _TAGGER is None:
        with _LOAD_LOCK:
            if _TAGGER is None:
                _TAGGER = EntityTagger.load(checkpoint_path(settings))
    return _TAGGER


def reset_tagger() -> None:
    """Drop the cached model. Used by tests and by `scripts/train_tagger.py`."""
    global _TAGGER
    with _LOAD_LOCK:
        _TAGGER = None


@torch.no_grad()
def tag_document(doc: TokenizedDoc, settings: Settings | None = None) -> DocumentTagging:
    return get_tagger(settings).tag(doc)
