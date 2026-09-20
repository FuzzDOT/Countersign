"""Generate `backend/data/fixtures/` from the real corpus.

    python -m scripts.gen_fixtures          # write
    python -m scripts.gen_fixtures --check  # verify without writing (CI)

**The validation step is the whole point.** Every payload is passed through the
exact Pydantic model its endpoint declares as `response_model` before it is
written. A fixture that has drifted from the response shape cannot be written,
so the frontend can never be developing against a contract the backend has
already changed underneath it. `--check` runs the same validation and diffs
against what is on disk, so CI catches a stale fixture that someone forgot to
regenerate.

The second guarantee is coverage: the key set of `build_all()` must exactly
match the fixture names declared by `@contract(...)` across every route. Adding
a route with a fixture nobody generates, or generating a fixture no route
serves, both fail here rather than at hour 14 in front of a frontend dev.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from api.main import create_app
from api.mock import declared_fixtures
from api.v1.schemas import (
    AblationResponse,
    AskResponse,
    BriefingResponse,
    CalibrationEval,
    DocumentDetail,
    DocumentSummary,
    EntityDetail,
    ErrorEnvelope,
    FragilityEval,
    FuzzerRunResponse,
    GraphResponse,
    InsightDetail,
    InsightOut,
    InsightStats,
    JobOut,
    MemberOut,
    MeResponse,
    NemotronRunOut,
    Paginated,
    RecalibrateResponse,
    RoutingEval,
    RoutingSummary,
    UploadResponse,
)
from core.logging import configure_logging, get_logger
from data.synth.fixtures import build_all

log = get_logger(__name__)

# Each fixture's authoritative model. Kept explicit rather than inferred from
# the route table: an inferred mapping would silently validate a fixture
# against the wrong model if a decorator were mis-typed, and the declaration is
# the thing we want reviewed.
FIXTURE_MODELS: dict[str, Any] = {
    "auth.me.json": MeResponse,
    "org.members.json": list[MemberOut],
    "documents.upload.json": UploadResponse,
    "documents.seed.json": UploadResponse,
    "documents.list.json": Paginated[DocumentSummary],
    "documents.detail.json": DocumentDetail,
    "ingest.jobs.json": Paginated[JobOut],
    "ingest.job.queued.json": JobOut,
    "ingest.job.tagging.json": JobOut,
    "ingest.job.relating.json": JobOut,
    "ingest.job.done.json": JobOut,
    "insights.list.json": Paginated[InsightOut],
    "insights.detail.json": InsightDetail,
    "insights.stats.json": InsightStats,
    "graph.full.json": GraphResponse,
    "graph.entity.json": EntityDetail,
    "ablation.run.json": AblationResponse,
    "routing.summary.json": RoutingSummary,
    "routing.runs.json": Paginated[NemotronRunOut],
    "evals.fragility.json": FragilityEval,
    "evals.fragility.run.json": FuzzerRunResponse,
    "evals.routing.json": RoutingEval,
    "evals.calibration.json": CalibrationEval,
    "calibration.recalibrate.json": RecalibrateResponse,
    "voice.briefing.json": BriefingResponse,
    "voice.fallback.json": BriefingResponse,
    "voice.ask.json": AskResponse,
}

ERROR_FIXTURES = (
    "errors/401.token_expired.json",
    "errors/403.forbidden.json",
    "errors/422.validation.json",
    "errors/429.rate_limited.json",
    "errors/503.nemotron_unavailable.json",
)


def model_for(name: str) -> Any:
    if name.startswith("errors/"):
        return ErrorEnvelope
    try:
        return FIXTURE_MODELS[name]
    except KeyError:
        raise LookupError(
            f"no response model declared for fixture {name!r}. Add it to FIXTURE_MODELS "
            "so the payload is validated before it reaches the frontend."
        ) from None


def validate(name: str, payload: Any) -> Any:
    """Round-trip the payload through its response model.

    Returns the *model's* serialization rather than the input dict, so what
    lands on disk is byte-identical to what FastAPI would emit — including
    field ordering, datetime formatting and defaults the builder omitted.
    """
    model = model_for(name)
    try:
        if isinstance(model, type) and issubclass(model, BaseModel):
            instance = model.model_validate(payload)
            return instance.model_dump(mode="json")
        adapter = TypeAdapter(model)
        return adapter.dump_python(adapter.validate_python(payload), mode="json")
    except ValidationError as error:
        raise SystemExit(
            f"\nfixture {name!r} does not satisfy {model}:\n{error}\n\n"
            "Either the builder in data/synth/fixtures.py is wrong, or the response model "
            "changed and the builder has not caught up. Do not loosen the model to make "
            "this pass — the frontend is generating its types from it."
        ) from error


def check_coverage(payloads: dict[str, Any]) -> None:
    """Fixture names in the route table must match the ones we generate."""
    app = create_app()
    declared = declared_fixtures(app) | set(ERROR_FIXTURES)
    built = set(payloads)

    missing = declared - built
    orphaned = built - declared

    problems: list[str] = []
    if missing:
        problems.append(
            "declared by a @contract(...) but not generated: " + ", ".join(sorted(missing))
        )
    if orphaned:
        problems.append("generated but no route serves it: " + ", ".join(sorted(orphaned)))
    if problems:
        raise SystemExit("\nfixture coverage mismatch:\n  " + "\n  ".join(problems) + "\n")


def write(payloads: dict[str, Any], directory: Path) -> int:
    written = 0
    for name, payload in sorted(payloads.items()):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Trailing newline so the files are diffable and pre-commit-friendly.
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written += 1
    return written


def diff_against_disk(payloads: dict[str, Any], directory: Path) -> list[str]:
    stale: list[str] = []
    for name, payload in sorted(payloads.items()):
        path = directory / name
        if not path.exists():
            stale.append(f"{name} (missing on disk)")
            continue
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        if on_disk != payload:
            stale.append(f"{name} (differs from generated)")
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "fixtures",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate and compare against disk without writing. Used by CI.",
    )
    parser.add_argument("--scenario", default="meridian_shell_ring")
    args = parser.parse_args()

    configure_logging()

    raw = build_all(args.scenario)
    check_coverage(raw)
    validated = {name: validate(name, payload) for name, payload in raw.items()}

    if args.check:
        stale = diff_against_disk(validated, args.out)
        if stale:
            print("fixtures are out of date:", file=sys.stderr)
            for entry in stale:
                print(f"  - {entry}", file=sys.stderr)
            print("\nRun `make fixtures` and commit the result.", file=sys.stderr)
            return 1
        print(f"{len(validated)} fixtures validated and up to date.")
        return 0

    count = write(validated, args.out)
    print(f"wrote {count} fixtures to {args.out}")
    print(f"  scenario: {args.scenario}")
    print("  every payload validated through its production response model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
