"""Run the adversarial fuzzer over a seeded organization.

    make fuzz
    docker compose exec backend python -m scripts.run_fuzzer --limit 10

Fuzzing is a job, not part of ingest (plan §1.8). A judge uploading documents
must see insights in under 30 seconds; five perturbation families over every
insight is hundreds of full pipeline passes. We run this once before the
demo and the eval page reads finished rows.

Prints the same report `GET /api/v1/evals/fragility` serves, so the number on
the slide and the number in the API are the same number.
"""

from __future__ import annotations

import argparse
import json
import uuid

from core import ids
from core.config import get_settings
from core.logging import configure_logging, get_logger
from db.session import db_session
from ml.fuzzer.runner import FuzzRunner

log = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the adversarial fuzzer.")
    parser.add_argument(
        "--org", default=None, help="Organization UUID. Defaults to the demo tenant."
    )
    parser.add_argument("--limit", type=int, default=None, help="Fuzz the first N insights only.")
    parser.add_argument("--variants", type=int, default=1, help="Variants per perturbation family.")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)
    org_id = uuid.UUID(args.org) if args.org else ids.DEMO_ORG_ID

    with db_session() as db:
        summary = FuzzRunner(db, org_id, settings, variants_per_family=args.variants).run(
            limit=args.limit
        )

        # Read back through the same code the endpoint uses, so this is not a
        # second implementation that could disagree with the API.
        from api.v1.evals import build_fragility_eval

        report = build_fragility_eval(db, org_id)

    print(
        json.dumps(
            {
                "insights": summary.n_insights,
                "trials": summary.n_trials,
                "elapsed_ms": summary.elapsed_ms,
                "documents_before": summary.documents_before,
                "documents_after": summary.documents_after,
            },
            indent=2,
        )
    )
    print()
    print(
        json.dumps(
            {
                "n_insights": report.n_insights,
                "n_trials": report.n_trials,
                "correlation": report.correlation.model_dump(),
                "by_perturbation": [row.model_dump() for row in report.by_perturbation],
                "quartile_table": [row.model_dump() for row in report.quartile_table],
                "interpretation": report.interpretation,
            },
            indent=2,
        )
    )

    if summary.wrote_documents:
        print(
            "\nFATAL: the fuzzer changed the document count. A perturbed variant "
            "reached the documents table and every citation in the demo is now "
            "suspect (plan §3).",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
