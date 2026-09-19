"""Train the relation models. Plan §4 Stage 3.

    python -m scripts.train_relations              # both, then report
    python -m scripts.train_relations --model gat

Both the GAT and the rule extractor are trained here, on the same examples,
against the same evidential head — which is what makes the hour-9 decision
gate a config flag rather than a rewrite (plan §1.3), and what makes the
comparison in `ml/evals/relations_report.json` a fair one.

**The hour-9 gate is decided on that report, not on a feeling.** If GAT
relation F1 on the held-out demo manifest is below 0.70, `RELATION_MODEL`
flips to `rules`, the evidential head and the ablation interface stay, and
the decision goes in the commit message.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn

from core.config import get_settings
from core.logging import get_logger
from data.synth.generate import generate
from ml.evidential.head import evidential_loss
from ml.relations.dataset import RelationExample, class_weights, examples_for_document
from ml.relations.gat import GATRelationModel
from ml.relations.interface import NO_RELATION, RELATION_INDEX, RELATIONS
from ml.relations.rules import RuleRelationModel
from ml.tagger.infer import get_tagger
from ml.text.parse import parse_many

log = get_logger(__name__)

TRAIN_SCENARIO = "train_corpus"
HELDOUT_SCENARIO = "meridian_shell_ring"
DEV_SPLIT_AT = 340

NO_RELATION_INDEX = RELATION_INDEX[NO_RELATION]


@dataclass(slots=True)
class RelationTrainConfig:
    epochs: int = 12
    sentences_per_batch: int = 16
    learning_rate: float = 2e-3
    weight_decay: float = 1e-5
    grad_clip: float = 5.0
    annealing_epochs: int = 10
    seed: int = 20260919
    limit_documents: int | None = None
    model: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RelationMetrics:
    precision: float
    recall: float
    f1: float
    accuracy: float
    per_class: dict[str, dict[str, float]]
    n_pairs: int
    n_positive: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
            "per_class": {
                name: {k: round(v, 4) for k, v in stats.items()}
                for name, stats in sorted(self.per_class.items())
            },
            "n_pairs": self.n_pairs,
            "n_positive": self.n_positive,
        }


# ── data ─────────────────────────────────────────────────────────────────────


def build_examples(
    scenario: str, *, seed: int, limit_documents: int | None = None
) -> list[list[RelationExample]]:
    """Examples grouped by document, so a split never straddles one."""
    manifest = generate(scenario, seed=seed)
    documents = list(manifest.documents)
    if limit_documents is not None:
        documents = documents[:limit_documents]

    started = time.monotonic()
    parsed = parse_many([document.raw_text for document in documents])
    tagger = get_tagger()

    grouped: list[list[RelationExample]] = []
    for gold, doc in zip(documents, parsed, strict=True):
        grouped.append(examples_for_document(doc, tagger.tag(doc), gold))

    log.info(
        "relation_examples_built",
        scenario=scenario,
        documents=len(documents),
        sentences=sum(len(group) for group in grouped),
        pairs=sum(len(example) for group in grouped for example in group),
        seconds=round(time.monotonic() - started, 1),
    )
    return grouped


def flatten(grouped: list[list[RelationExample]]) -> list[RelationExample]:
    return [example for group in grouped for example in group]


# ── scoring ──────────────────────────────────────────────────────────────────


def score_example(model: nn.Module, example: RelationExample) -> Tensor:
    """Logits for one sentence's pairs, whichever model is in hand."""
    if isinstance(model, RuleRelationModel):
        return model.score_pairs(example.graph, example.pairs, example.lemmas)
    logits, _ = model.score_pairs(example.graph, example.pairs)
    return logits


def evaluate(model: nn.Module, examples: list[RelationExample]) -> RelationMetrics:
    """Micro P/R/F1 over the five real relations.

    NO_RELATION is excluded from the F1 on purpose: it is 70% of the pairs
    and including it would report ~0.9 for a model that predicts nothing.
    Accuracy over all pairs is reported alongside so the null-class behaviour
    is still visible.
    """
    model.eval()
    true_positive = false_positive = false_negative = 0
    correct = total = 0
    per_class: dict[str, list[int]] = {name: [0, 0, 0] for name in RELATIONS}

    with torch.no_grad():
        for example in examples:
            if not example.pairs:
                continue
            predictions = score_example(model, example).argmax(dim=1).tolist()
            for predicted, gold in zip(predictions, example.labels, strict=True):
                total += 1
                correct += int(predicted == gold)

                if predicted == gold != NO_RELATION_INDEX:
                    true_positive += 1
                    per_class[RELATIONS[gold]][0] += 1
                else:
                    if predicted != NO_RELATION_INDEX:
                        false_positive += 1
                        per_class[RELATIONS[predicted]][1] += 1
                    if gold != NO_RELATION_INDEX:
                        false_negative += 1
                        per_class[RELATIONS[gold]][2] += 1

    precision, recall, f1 = _prf(true_positive, false_positive, false_negative)
    typed: dict[str, dict[str, float]] = {}
    for name, (tp, fp, fn) in per_class.items():
        if name == NO_RELATION:
            continue
        class_precision, class_recall, class_f1 = _prf(tp, fp, fn)
        typed[name] = {
            "precision": class_precision,
            "recall": class_recall,
            "f1": class_f1,
            "support": float(tp + fn),
        }

    return RelationMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=correct / total if total else 0.0,
        per_class=typed,
        n_pairs=total,
        n_positive=sum(1 for e in examples for label in e.labels if label != NO_RELATION_INDEX),
    )


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


# ── end-to-end evaluation ────────────────────────────────────────────────────


def evaluate_end_to_end(model: nn.Module, scenario: str, *, seed: int) -> dict[str, Any]:
    """Relation P/R/F1 through the *whole* pipeline, on predicted mentions.

    `evaluate()` above scores the relation model in isolation: gold entity
    spans, gold-derived candidate pairs, only sentences that contain two gold
    arguments. That is the right number for comparing two relation models and
    the wrong one for describing the product, because at serving time the
    tagger chooses the mentions and every sentence is a candidate.

    This is the honest number. It is lower, and both are published.

    Triples are compared on the coreference key of each argument's surface,
    which is how the pipeline itself decides two surfaces are one company —
    so `Meridian` and `Meridian Supply LLC` match, as they should.
    """
    from ml.entities.coref import entity_key
    from ml.relations.infer import RelationExtractor
    from ml.tagger.infer import get_tagger

    manifest = generate(scenario, seed=seed)
    documents = list(manifest.documents)
    parsed = parse_many([document.raw_text for document in documents])
    tagger = get_tagger()

    extractor = RelationExtractor(model, getattr(model, "name", "?"), get_settings())

    true_positive = false_positive = false_negative = 0
    documents_with_relation = 0

    for gold, doc in zip(documents, parsed, strict=True):
        drafts = extractor.extract(doc, tagger.tag(doc))
        if drafts:
            documents_with_relation += 1

        predicted = {
            (
                entity_key(draft.subject.surface, draft.subject.entity_type),
                entity_key(draft.object.surface, draft.object.entity_type),
                draft.relation,
            )
            for draft in drafts
        }
        truth = {
            (
                entity_key(relation.subject_canonical, "ORG"),
                entity_key(relation.object_canonical, "ORG"),
                relation.relation,
            )
            for relation in gold.relations
        }

        true_positive += len(predicted & truth)
        false_positive += len(predicted - truth)
        false_negative += len(truth - predicted)

    precision, recall, f1 = _prf(true_positive, false_positive, false_negative)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "documents": len(documents),
        "documents_with_a_relation": documents_with_relation,
        "document_coverage": round(documents_with_relation / max(len(documents), 1), 4),
        "gold_triples": true_positive + false_negative,
        "predicted_triples": true_positive + false_positive,
    }


# ── training ─────────────────────────────────────────────────────────────────


def fit(
    model: nn.Module,
    train: list[RelationExample],
    config: RelationTrainConfig,
) -> list[dict[str, float]]:
    optimizer = torch.optim.Adam(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    # Cosine decay to a tenth of the starting rate. The loss curve plateaus
    # around epoch 8 and then oscillates — the decay turns that oscillation
    # into the last two points of convergence instead.
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=config.epochs, eta_min=config.learning_rate / 10
    )
    weights = class_weights(
        [label for example in train for label in example.labels], len(RELATIONS)
    )
    log.info(
        "relation_class_weights",
        model=getattr(model, "name", type(model).__name__),
        **{name: round(float(weights[index]), 3) for index, name in enumerate(RELATIONS)},
    )

    order = list(range(len(train)))
    history: list[dict[str, float]] = []

    for epoch in range(1, config.epochs + 1):
        model.train()
        random.shuffle(order)
        total_loss = 0.0
        batches = 0

        for start in range(0, len(order), config.sentences_per_batch):
            chunk = [train[index] for index in order[start : start + config.sentences_per_batch]]
            optimizer.zero_grad()

            logits: list[Tensor] = []
            targets: list[int] = []
            for example in chunk:
                if not example.pairs:
                    continue
                logits.append(score_example(model, example))
                targets.extend(example.labels)
            if not logits:
                continue

            loss = evidential_loss(
                torch.cat(logits),
                torch.tensor(targets, dtype=torch.long),
                epoch=epoch - 1,
                annealing_epochs=config.annealing_epochs,
                class_weights=weights,
            )
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
            optimizer.step()

            total_loss += float(loss)
            batches += 1

        schedule.step()
        mean = total_loss / max(batches, 1)
        history.append({"epoch": epoch, "loss": round(mean, 4)})
        log.info(
            "relation_epoch",
            model=getattr(model, "name", "?"),
            epoch=epoch,
            loss=round(mean, 4),
        )

    return history


def save(path: Path, model: nn.Module, metrics: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "format": 1,
            "name": getattr(model, "name", "?"),
            "config": getattr(model, "config", {}),
            "state_dict": model.state_dict(),
            "metrics": metrics,
        },
        path,
    )


def load(path: Path) -> tuple[nn.Module, dict[str, Any]]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if payload["name"] == "rules":
        model: nn.Module = RuleRelationModel(**payload["config"])
    else:
        model = GATRelationModel(**payload["config"])
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, payload.get("metrics", {})


# ── entry point ──────────────────────────────────────────────────────────────


def train(
    config: RelationTrainConfig | None = None,
    *,
    which: tuple[str, ...] = ("gat", "rules"),
    checkpoint_dir: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    config = config or RelationTrainConfig()
    random.seed(config.seed)
    torch.manual_seed(config.seed)

    grouped = build_examples(
        TRAIN_SCENARIO, seed=config.seed, limit_documents=config.limit_documents
    )
    split_at = min(DEV_SPLIT_AT, max(1, int(len(grouped) * 0.85)))
    train_examples = flatten(grouped[:split_at])
    dev_examples = flatten(grouped[split_at:])

    if not train_examples or not dev_examples:
        raise RuntimeError(
            f"{len(train_examples)} train and {len(dev_examples)} dev sentences — "
            "nothing to train on"
        )

    heldout_examples = flatten(build_examples(HELDOUT_SCENARIO, seed=config.seed))

    report: dict[str, Any] = {
        "train_scenario": TRAIN_SCENARIO,
        "heldout_scenario": HELDOUT_SCENARIO,
        "seed": config.seed,
        "epochs": config.epochs,
        "train_sentences": len(train_examples),
        "dev_sentences": len(dev_examples),
        "heldout_sentences": len(heldout_examples),
        "models": {},
        "note": (
            "Candidates and labels come from the generator's gold mentions; node "
            "features come from the tagger's predictions, so the input distribution "
            "matches inference while the supervision stays clean. F1 excludes "
            "NO_RELATION, which is ~70% of pairs and would otherwise flatter a model "
            "that predicts nothing. `heldout` is meridian_shell_ring: unseen company "
            "names and 10% band-D syntax. `end_to_end` is the honest product number — "
            "the tagger picks the mentions, every sentence is a candidate, and triples "
            "are matched on the coreference key. It is lower than `heldout` and both "
            "are published."
        ),
    }

    node_features = train_examples[0].graph.node_features.size(1)

    for name in which:
        started = time.monotonic()
        model: nn.Module = (
            RuleRelationModel(**config.model)
            if name == "rules"
            else GATRelationModel(node_features=node_features, **config.model)
        )
        history = fit(model, train_examples, config)
        elapsed = time.monotonic() - started

        entry: dict[str, Any] = {
            "parameters": sum(p.numel() for p in model.parameters()),
            "train_seconds": round(elapsed, 1),
            "loss_history": history,
            "dev": evaluate(model, dev_examples).to_dict(),
            "heldout": evaluate(model, heldout_examples).to_dict(),
            "end_to_end": evaluate_end_to_end(model, HELDOUT_SCENARIO, seed=config.seed),
        }
        report["models"][name] = entry

        if checkpoint_dir is not None:
            path = checkpoint_dir / f"relations_{name}.pt"
            save(path, model, entry["heldout"])
            entry["checkpoint"] = str(path)
            entry["checkpoint_bytes"] = path.stat().st_size

        log.info(
            "relation_model_trained",
            model=name,
            heldout_f1=entry["heldout"]["f1"],
            end_to_end_f1=entry["end_to_end"]["f1"],
            dev_f1=entry["dev"]["f1"],
            seconds=round(elapsed, 1),
        )

    report["recommended"] = _recommend(report["models"])

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


# The hour-9 gate from plan §4 Stage 3, as a number rather than a judgement.
GAT_MINIMUM_F1 = 0.70


def _recommend(models: dict[str, Any]) -> dict[str, Any]:
    gat = models.get("gat", {}).get("heldout", {}).get("f1")
    rules = models.get("rules", {}).get("heldout", {}).get("f1")

    if gat is None:
        return {"relation_model": "rules", "reason": "the GAT was not trained"}
    if gat < GAT_MINIMUM_F1:
        return {
            "relation_model": "rules",
            "reason": (
                f"GAT held-out F1 {gat:.3f} is below the {GAT_MINIMUM_F1} hour-9 gate "
                f"(plan §4 Stage 3); rules scores {rules:.3f}"
            ),
        }
    return {
        "relation_model": "gat",
        "reason": (
            f"GAT held-out F1 {gat:.3f} clears the {GAT_MINIMUM_F1} gate"
            + (f"; rules scores {rules:.3f}" if rules is not None else "")
        ),
    }
