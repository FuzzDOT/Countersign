"""Build the routing eval set from the generator's ground truth.

    make eval
    docker compose exec backend python -m scripts.build_routing_eval --org <uuid>

One case per gold relation the pipeline actually extracted. A gold relation
with no matching insight is a *recall* failure, which the relation eval
already reports; counting it here would mix two different mistakes into one
confusion matrix.

**The ground truth is generator-authored.** We wrote the fraud, so we know
the answer. That is not human adjudication on real documents, it is a real
limitation of a project with no annotators, and it is stated here, in the
module that produces the labels, as well as in the writeup.
"""

from __future__ import annotations

import argparse
import json
import uuid

from core import ids
from core.config import get_settings
from core.logging import configure_logging, get_logger
from db.session import db_session
from ml.cascade.evalset import build

log = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the routing eval set.")
    parser.add_argument("--org", default=None, help="Defaults to the demo tenant.")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)
    org_id = uuid.UUID(args.org) if args.org else ids.DEMO_ORG_ID

    with db_session() as db:
        result = build(db, org_id, settings)
        db.commit()

        from api.v1.evals import build_routing_eval

        report = build_routing_eval(db, org_id)

    print(
        json.dumps(
            {
                "scenarios": result.scenarios,
                "cases": result.matched,
                "gold_relations_not_extracted": result.unmatched,
                "misroutes": result.failures,
            },
            indent=2,
        )
    )
    print()
    print(
        json.dumps(
            {
                "n_cases": report.n_cases,
                "accuracy": report.accuracy,
                "macro_f1": report.macro_f1,
                "confusion_matrix": report.confusion_matrix.model_dump(),
                "per_class": [row.model_dump() for row in report.per_class],
                "cascade_baseline": report.cascade_baseline.model_dump(),
                "documented_failures": len(report.documented_failures),
            },
            indent=2,
            default=str,
        )
    )
    for failure in report.documented_failures[:2]:
        print(f"\n[{failure.ground_truth} -> {failure.predicted}] {failure.sentence_text[:90]}")
        print(f"  {failure.note[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
