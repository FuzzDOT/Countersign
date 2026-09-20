"""Train the BiLSTM-CRF on `train_corpus`.

    python -m scripts.train_tagger

**The split discipline is the point.** Training text comes only from
`train_corpus` — bands A-C, `TRAIN_*` name pools — and the labels are the weak
ones from `weak_supervision.py`. The dev set is a held-out slice of the same
scenario labeled with the generator's *gold* mentions, so the reported F1
measures the model against truth rather than against the labeling function
that trained it.

The demo scenarios are never touched here. If a held-out name reached this
function, vacuity would go flat on band D and the fragility correlation — the
project's central claim — would become noise (plan §0). `assert_no_heldout`
checks it rather than trusting the call site.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
from torch import nn

from core.logging import get_logger
from data.synth import names
from data.synth.generate import generate
from ml.tagger.data import (
    Batch,
    SentenceUnit,
    TaggedSentence,
    align_char_spans,
    encode_batch,
    iter_sentences,
)
from ml.tagger.labels import (
    OUTSIDE,
    TAG_INDEX,
    TAGS,
    spans_to_tags,
    tags_to_spans,
)
from ml.tagger.model import BiLSTMCRFTagger, TaggerConfig, save_checkpoint
from ml.tagger.vocab import build_vocab
from ml.tagger.weak_supervision import training_gazetteer
from ml.text.parse import parse_many

log = get_logger(__name__)

TRAIN_SCENARIO = "train_corpus"
# Documents 0..DEV_SPLIT_AT-1 train, the rest are dev. A document-level split,
# not a sentence-level one: sentences from the same document share entities
# and a random sentence split would leak them across the boundary.
DEV_SPLIT_AT = 340


@dataclass(slots=True)
class TrainConfig:
    epochs: int = 10
    batch_size: int = 32
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    grad_clip: float = 5.0
    # Fraction of word ids replaced with <unk> each epoch. THE parameter that
    # decides whether this model generalizes at all.
    #
    # The training corpus has a closed vocabulary, so <unk> would otherwise
    # occur only on the handful of singletons below MIN_WORD_COUNT. The model
    # then learns to read the word embedding and ignore the character CNN —
    # which works perfectly on dev and collapses on the demo scenario, where
    # every held-out company name is <unk>. Measured: without this, ORG span
    # F1 on meridian_shell_ring is 0.00 and the tagger emits `Supply LLC` for
    # `Meridian Supply LLC`. With it, the character CNN has to carry the
    # unknown token and the span survives.
    word_dropout: float = 0.3
    seed: int = 20260919
    # Documents, not sentences. Lowered by the fast smoke test in the suite.
    limit_documents: int | None = None
    model: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Metrics:
    token_precision: float
    token_recall: float
    token_f1: float
    span_precision: float
    span_recall: float
    span_f1: float
    per_type: dict[str, dict[str, float]]
    n_sentences: int
    n_gold_spans: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_precision": round(self.token_precision, 4),
            "token_recall": round(self.token_recall, 4),
            "token_f1": round(self.token_f1, 4),
            "span_precision": round(self.span_precision, 4),
            "span_recall": round(self.span_recall, 4),
            "span_f1": round(self.span_f1, 4),
            "per_type": {
                name: {k: round(v, 4) for k, v in stats.items()}
                for name, stats in sorted(self.per_type.items())
            },
            "n_sentences": self.n_sentences,
            "n_gold_spans": self.n_gold_spans,
        }


# ── corpus preparation ───────────────────────────────────────────────────────


def assert_no_heldout(texts: list[str]) -> None:
    """Fail loudly if a held-out name reached the training text.

    A substring check over training documents, which is not an offset
    computation and therefore not covered by plan §3's prohibition. It is a
    contamination assertion, and the thing it guards against — band-D names in
    the training split — is unrecoverable at hour 13.
    """
    offenders: set[str] = set()
    for text in texts:
        for canonical in (*names.HELDOUT_ORGS, *names.HELDOUT_PERSONS):
            for alias in names.aliases_for(canonical):
                if alias in text:
                    offenders.add(alias)
    if offenders:
        raise AssertionError(
            f"held-out names {sorted(offenders)} appear in the training corpus. "
            "Vacuity would go flat on band D and the fragility correlation would "
            "be measuring nothing."
        )


@dataclass(slots=True)
class PreparedCorpus:
    train: list[TaggedSentence]
    dev: list[TaggedSentence]
    weak_dev: list[TaggedSentence]
    n_train_documents: int
    n_dev_documents: int


def prepare_corpus(*, seed: int, limit_documents: int | None = None) -> PreparedCorpus:
    manifest = generate(TRAIN_SCENARIO, seed=seed)
    documents = list(manifest.documents)
    if limit_documents is not None:
        documents = documents[:limit_documents]

    texts = [document.raw_text for document in documents]
    assert_no_heldout(texts)

    started = time.monotonic()
    parsed = parse_many(texts)
    log.info(
        "train_corpus_parsed",
        documents=len(parsed),
        seconds=round(time.monotonic() - started, 1),
    )

    gazetteer = training_gazetteer()
    split_at = min(DEV_SPLIT_AT, max(1, int(len(documents) * 0.85)))

    train: list[TaggedSentence] = []
    dev: list[TaggedSentence] = []
    weak_dev: list[TaggedSentence] = []

    for index, (document, doc) in enumerate(zip(documents, parsed, strict=True)):
        sentences = list(iter_sentences(doc, doc_index=index))
        if not sentences:
            continue
        weak = _weak_tagged(doc, sentences, gazetteer)
        if index < split_at:
            train.extend(weak)
            continue

        gold_spans = align_char_spans(
            doc,
            sentences,
            [(m.entity_type, m.char_start, m.char_end) for m in document.mentions],
        )
        for sentence, spans in zip(sentences, gold_spans, strict=True):
            dev.append(
                TaggedSentence(
                    surfaces=tuple(sentence.surfaces),
                    tags=tuple(spans_to_tags(spans, len(sentence))),
                )
            )
        # The same dev sentences under weak labels, so the report can separate
        # "the model is wrong" from "the labeling function is wrong".
        weak_dev.extend(weak)

    return PreparedCorpus(
        train=train,
        dev=dev,
        weak_dev=weak_dev,
        n_train_documents=split_at,
        n_dev_documents=len(documents) - split_at,
    )


def _weak_tagged(doc: Any, sentences: list[SentenceUnit], gazetteer: Any) -> list[TaggedSentence]:
    from ml.tagger.weak_supervision import label_document

    labeled = label_document(doc, sentences, gazetteer)
    return [
        TaggedSentence(
            surfaces=tuple(sentence.surfaces),
            tags=tuple(spans_to_tags(spans, len(sentence))),
        )
        for sentence, spans in zip(sentences, labeled, strict=True)
    ]


# ── evaluation ───────────────────────────────────────────────────────────────


def _f1(true_positive: int, false_positive: int, false_negative: int) -> tuple[float, float, float]:
    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def score(predicted: list[list[str]], gold: list[list[str]]) -> Metrics:
    """Token-level and exact-span-level micro F1.

    Token F1 is over non-`O` positions only. Counting `O` would put the score
    above 0.95 on any corpus simply because most tokens are not entities,
    which is the kind of number that looks good and means nothing.
    """
    token_tp = token_fp = token_fn = 0
    span_tp = span_fp = span_fn = 0
    per_type: dict[str, list[int]] = {}
    gold_span_total = 0

    for predicted_tags, gold_tags in zip(predicted, gold, strict=True):
        for predicted_tag, gold_tag in zip(predicted_tags, gold_tags, strict=True):
            if predicted_tag == gold_tag:
                if gold_tag != OUTSIDE:
                    token_tp += 1
                continue
            if predicted_tag != OUTSIDE:
                token_fp += 1
            if gold_tag != OUTSIDE:
                token_fn += 1

        predicted_spans = {
            (s.entity_type, s.token_start, s.token_end) for s in tags_to_spans(predicted_tags)
        }
        gold_spans = {(s.entity_type, s.token_start, s.token_end) for s in tags_to_spans(gold_tags)}
        gold_span_total += len(gold_spans)

        span_tp += len(predicted_spans & gold_spans)
        span_fp += len(predicted_spans - gold_spans)
        span_fn += len(gold_spans - predicted_spans)

        for entity_type, _, _ in predicted_spans | gold_spans:
            per_type.setdefault(entity_type, [0, 0, 0])
        for span in predicted_spans & gold_spans:
            per_type[span[0]][0] += 1
        for span in predicted_spans - gold_spans:
            per_type[span[0]][1] += 1
        for span in gold_spans - predicted_spans:
            per_type[span[0]][2] += 1

    token_precision, token_recall, token_f1 = _f1(token_tp, token_fp, token_fn)
    span_precision, span_recall, span_f1 = _f1(span_tp, span_fp, span_fn)

    typed: dict[str, dict[str, float]] = {}
    for entity_type, (tp, fp, fn) in per_type.items():
        precision, recall, f1 = _f1(tp, fp, fn)
        typed[entity_type] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(tp + fn),
        }

    return Metrics(
        token_precision=token_precision,
        token_recall=token_recall,
        token_f1=token_f1,
        span_precision=span_precision,
        span_recall=span_recall,
        span_f1=span_f1,
        per_type=typed,
        n_sentences=len(gold),
        n_gold_spans=gold_span_total,
    )


def predict_tags(
    model: BiLSTMCRFTagger,
    sentences: list[list[str]],
    vocab: Any,
    *,
    batch_size: int = 64,
) -> list[list[str]]:
    model.eval()
    out: list[list[str]] = []
    for start in range(0, len(sentences), batch_size):
        chunk = sentences[start : start + batch_size]
        batch = encode_batch(chunk, vocab, max_word_len=model.config.max_word_len)
        paths, _, _, _ = model.decode(batch.word_ids, batch.char_ids, batch.mask)
        out.extend([[TAGS[tag] for tag in path] for path in paths])
    return out


# ── out-of-distribution evaluation ───────────────────────────────────────────

# The demo scenario. Evaluated here, never trained on: its names are the
# HELDOUT_* pools and 10% of its relations are band D, so this is the only
# number in the report that says anything about generalization.
HELDOUT_SCENARIO = "meridian_shell_ring"


def evaluate_scenario(model: BiLSTMCRFTagger, vocab: Any, scenario: str, *, seed: int) -> Metrics:
    """Score the model against a scenario's gold mentions.

    Measurement, not training — nothing here touches an optimizer. Running it
    on `meridian_shell_ring` is the difference between reporting "F1 1.00 on
    sentences drawn from the same 36 templates and 28 company names the model
    trained on" and reporting what happens when the company has never been
    seen and the syntax was held out on purpose.
    """
    manifest = generate(scenario, seed=seed)
    documents = list(manifest.documents)
    parsed = parse_many([document.raw_text for document in documents])

    surfaces: list[list[str]] = []
    gold: list[list[str]] = []

    for document, doc in zip(documents, parsed, strict=True):
        sentences = list(iter_sentences(doc))
        if not sentences:
            continue
        spans = align_char_spans(
            doc,
            sentences,
            [(m.entity_type, m.char_start, m.char_end) for m in document.mentions],
        )
        for sentence, sentence_spans in zip(sentences, spans, strict=True):
            surfaces.append(sentence.surfaces)
            gold.append(spans_to_tags(sentence_spans, len(sentence)))

    return score(predict_tags(model, surfaces, vocab), gold)


# ── training loop ────────────────────────────────────────────────────────────


def _apply_word_dropout(word_ids: Any, mask: Any, probability: float) -> Any:
    """Replace a random share of real tokens with <unk>.

    Resampled every epoch, so a given token is sometimes visible and
    sometimes not — which is what teaches the model that the character CNN is
    the fallback rather than an ornament. Padding is left alone: dropping a
    pad to <unk> would put a real embedding where the mask says there is
    nothing.
    """
    if probability <= 0.0:
        return word_ids
    from ml.tagger.vocab import UNK  # noqa: F401 - index is fixed at 1 by build_vocab

    drop = (torch.rand(word_ids.shape) < probability) & mask
    return word_ids.masked_fill(drop, 1)


def _batches(examples: list[TaggedSentence], vocab: Any, config: TrainConfig) -> list[Batch]:
    # Length-bucketed, then shuffled at the batch level. Grouping similar
    # lengths cuts padding roughly in half on this corpus; shuffling the
    # batches keeps the gradient from seeing all the short sentences first.
    ordered = sorted(range(len(examples)), key=lambda i: len(examples[i]))
    batches: list[Batch] = []
    for start in range(0, len(ordered), config.batch_size):
        indices = ordered[start : start + config.batch_size]
        chunk = [examples[i] for i in indices]
        batches.append(
            encode_batch(
                [list(example.surfaces) for example in chunk],
                vocab,
                max_word_len=config.model.get("max_word_len", 20),
                tag_sequences=[[TAG_INDEX[t] for t in example.tags] for example in chunk],
            )
        )
    return batches


def train(
    config: TrainConfig | None = None,
    *,
    checkpoint_path: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    config = config or TrainConfig()

    random.seed(config.seed)
    torch.manual_seed(config.seed)

    corpus = prepare_corpus(seed=config.seed, limit_documents=config.limit_documents)
    if not corpus.train or not corpus.dev:
        raise RuntimeError(
            f"corpus produced {len(corpus.train)} train and {len(corpus.dev)} dev "
            "sentences — nothing to train on"
        )

    vocab = build_vocab([list(example.surfaces) for example in corpus.train])
    model_config = TaggerConfig(n_words=vocab.n_words, n_chars=vocab.n_chars, **config.model)
    model = BiLSTMCRFTagger(model_config)

    optimizer = torch.optim.Adam(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    batches = _batches(corpus.train, vocab, config)

    log.info(
        "tagger_training_start",
        train_sentences=len(corpus.train),
        dev_sentences=len(corpus.dev),
        batches=len(batches),
        vocab_words=vocab.n_words,
        vocab_chars=vocab.n_chars,
        parameters=sum(p.numel() for p in model.parameters()),
    )

    started = time.monotonic()
    history: list[dict[str, float]] = []

    for epoch in range(1, config.epochs + 1):
        model.train()
        random.shuffle(batches)
        total = 0.0
        for batch in batches:
            optimizer.zero_grad()
            assert batch.tag_ids is not None
            word_ids = _apply_word_dropout(batch.word_ids, batch.mask, config.word_dropout)
            loss = model.loss(word_ids, batch.char_ids, batch.mask, batch.tag_ids)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
            optimizer.step()
            total += float(loss) * len(batch)

        mean_loss = total / max(sum(len(b) for b in batches), 1)
        history.append({"epoch": epoch, "loss": round(mean_loss, 4)})
        log.info("tagger_epoch", epoch=epoch, loss=round(mean_loss, 4))

    elapsed = time.monotonic() - started

    dev_surfaces = [list(example.surfaces) for example in corpus.dev]
    dev_gold = [list(example.tags) for example in corpus.dev]
    dev_metrics = score(predict_tags(model, dev_surfaces, vocab), dev_gold)

    # How good the labeling function itself is on the dev documents. The
    # difference between this and dev_metrics is the model's contribution, and
    # publishing both is what keeps "F1 0.9x" from being a claim about the
    # generator dressed up as a claim about the tagger.
    weak_metrics = score([list(e.tags) for e in corpus.weak_dev], dev_gold)

    heldout_metrics = evaluate_scenario(model, vocab, HELDOUT_SCENARIO, seed=config.seed)

    report: dict[str, Any] = {
        "scenario": TRAIN_SCENARIO,
        "seed": config.seed,
        "epochs": config.epochs,
        "word_dropout": config.word_dropout,
        "train_documents": corpus.n_train_documents,
        "dev_documents": corpus.n_dev_documents,
        "train_sentences": len(corpus.train),
        "dev_sentences": len(corpus.dev),
        "parameters": sum(p.numel() for p in model.parameters()),
        "train_seconds": round(elapsed, 1),
        "loss_history": history,
        "dev": dev_metrics.to_dict(),
        "weak_supervision_on_dev": weak_metrics.to_dict(),
        "heldout_scenario": HELDOUT_SCENARIO,
        "heldout": heldout_metrics.to_dict(),
        "note": (
            "`dev` is a held-out slice of train_corpus: same 36 templates, same TRAIN_* "
            "name pool. On a closed-vocabulary synthetic corpus the weak labeling "
            "functions are exact against the generator's gold (see "
            "weak_supervision_on_dev), so `dev` measures how well the model fits a "
            "distribution it has fully seen and nothing more — read it as a floor, not "
            "as a result. `heldout` is the number worth quoting: meridian_shell_ring "
            "uses the HELDOUT_* name pools and 10% band-D syntax, neither of which the "
            "model or the gazetteer has ever seen. Both corpora are ones we authored; "
            "the writeup says so rather than implying real-world performance."
        ),
    }

    if checkpoint_path is not None:
        save_checkpoint(checkpoint_path, model, vocab.to_dict(), report["dev"])
        report["checkpoint"] = str(checkpoint_path)
        report["checkpoint_bytes"] = checkpoint_path.stat().st_size

    if report_path is not None:
        import json

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    log.info(
        "tagger_training_done",
        # Named to avoid `core/logging.py`'s redaction pattern, which matches
        # "token" as a substring anywhere in a key on purpose — the tradeoff
        # is stated there ("a false positive costs one unreadable log field,
        # a false negative costs a leaked credential") and is correct as a
        # policy. It just doesn't know these two are tagger token-level F1
        # scores, not an auth token, and was swallowing them on every run.
        # The JSON report below keeps `dev.token_f1` / `heldout.token_f1` —
        # that field name is real and meaningful (token-level vs span-level),
        # and `print(json.dumps(report))` never goes through structlog, so
        # nothing there needed to change.
        dev_tagging_f1=report["dev"]["token_f1"],
        heldout_tagging_f1=report["heldout"]["token_f1"],
        heldout_span_f1=report["heldout"]["span_f1"],
        seconds=round(elapsed, 1),
    )
    return report
