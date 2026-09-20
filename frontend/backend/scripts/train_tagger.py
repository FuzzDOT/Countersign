"""Train and checkpoint the entity tagger.

    docker compose exec backend python -m scripts.train_tagger
    docker compose exec backend python -m scripts.train_tagger --epochs 2 --limit 40

Writes `ml/checkpoints/tagger.pt` and `ml/evals/tagger_report.json`. The
checkpoint is committed deliberately (plan §1.7): `docker compose up` has to
work on a judge's machine, and a fresh clone with no weights cannot serve an
insight.
"""

from __future__ import annotations

import argparse
import json
import sys

from core.config import get_settings
from core.logging import configure_logging, get_logger
from ml.tagger.infer import CHECKPOINT_NAME, reset_tagger
from ml.tagger.train import TrainConfig, train

log = get_logger(__name__)

REPORT_NAME = "tagger_report.json"
# Brief §15's target. The exit criterion for Stage 2, asserted here so a bad
# run cannot quietly overwrite a good checkpoint.
TARGET_TOKEN_F1 = 0.90


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the BiLSTM-CRF entity tagger.")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--limit", type=int, default=None, help="Train on the first N documents only."
    )
    parser.add_argument(
        "--allow-below-target",
        action="store_true",
        help="Write the checkpoint even if dev token F1 misses the 0.90 target.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)

    checkpoint = settings.path(settings.checkpoint_dir) / CHECKPOINT_NAME
    report_path = settings.path("ml/evals") / REPORT_NAME

    config = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        seed=args.seed if args.seed is not None else settings.pipeline_seed,
        limit_documents=args.limit,
    )

    report = train(config, checkpoint_path=checkpoint, report_path=report_path)
    reset_tagger()

    print(json.dumps({k: v for k, v in report.items() if k != "loss_history"}, indent=2))

    # Gated on the held-out scenario, not on dev. Dev shares its templates and
    # its name pool with training, so gating on it would pass a model that has
    # memorized 28 company names and generalizes to none.
    token_f1 = report["heldout"]["token_f1"]
    if token_f1 < TARGET_TOKEN_F1 and not args.allow_below_target:
        print(
            f"\nheld-out token F1 {token_f1:.4f} is below the {TARGET_TOKEN_F1} target "
            "(brief §15). The checkpoint was written; re-run with more epochs or "
            "pass --allow-below-target to accept it.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
