"""Train the GAT and the rule-based relation extractor, then report.

    docker compose exec backend python -m scripts.train_relations
    docker compose exec backend python -m scripts.train_relations --limit 60 --epochs 4

Writes `ml/checkpoints/relations_{gat,rules}.pt` and
`ml/evals/relations_report.json`. The report ends with a `recommended` block:
the hour-9 decision gate (plan §4 Stage 3) applied to the held-out number, so
the choice between `RELATION_MODEL=gat` and `rules` is made by arithmetic.
"""

from __future__ import annotations

import argparse
import json

from core.config import get_settings
from core.logging import configure_logging, get_logger
from ml.relations.train import RelationTrainConfig, train

log = get_logger(__name__)

REPORT_NAME = "relations_report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the relation models.")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--model",
        choices=("gat", "rules", "both"),
        default="both",
        help="Which to train. `both` is what the hour-9 gate compares.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)

    which = ("gat", "rules") if args.model == "both" else (args.model,)
    config = RelationTrainConfig(
        epochs=args.epochs,
        sentences_per_batch=args.batch,
        learning_rate=args.lr,
        seed=args.seed if args.seed is not None else settings.pipeline_seed,
        limit_documents=args.limit,
    )

    report = train(
        config,
        which=which,
        checkpoint_dir=settings.path(settings.checkpoint_dir),
        report_path=settings.path("ml/evals") / REPORT_NAME,
    )

    trimmed = {
        **{k: v for k, v in report.items() if k != "models"},
        "models": {
            name: {k: v for k, v in entry.items() if k != "loss_history"}
            for name, entry in report["models"].items()
        },
    }
    print(json.dumps(trimmed, indent=2))
    print(
        f"\nRELATION_MODEL={report['recommended']['relation_model']}  "
        f"— {report['recommended']['reason']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
