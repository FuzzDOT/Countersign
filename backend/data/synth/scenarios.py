"""Scenario specifications.

Declarative rather than hand-authored: a scenario names its cast and its fraud,
and `generate.py` turns that into documents. Hand-writing 34 documents would be
slower, harder to rebalance when the band mix comes out wrong, and impossible
to regenerate deterministically after an edit.

Four scenarios, three of them servable:

  meridian_shell_ring  the demo path — 34 docs, a 3-hop ownership loop, two
                       timing anomalies, one red herring, one planted failure
  clean_baseline       28 docs, no fraud. Proves we do not cry wolf, and
                       catches a gate threshold tuned too aggressively
  invoice_flood        120 docs, volume and latency stress
  train_corpus         400 docs, bands A–C only, disjoint name pool, NEVER
                       served through the API

The red herring is worth its line of code. A system that flags everything
suspicious-looking is useless, and `Northgate Logistics` exists specifically to
look adjacent to the ring — same freight corridor, overlapping dates — while
being entirely legitimate. If the pipeline escalates Northgate, the gate is
wrong, and we would rather learn that at hour 12 than on stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from data.synth import names
from data.synth.templates import Band

# ── routing ground truth ─────────────────────────────────────────────────────
# What a competent human analyst would decide, which is what the Stage 6
# confusion matrix is scored against.

AUTO_FILE = "auto_file"
FLAG = "flag_for_review"
ESCALATE = "escalate_now"


@dataclass(frozen=True, slots=True)
class RelationPlan:
    """One relation the scenario asserts, with its ground-truth routing."""

    relation: str
    subject: str
    object: str
    band: Band
    routing: str
    # Pin a specific template instead of sampling. Used for the demo sentence
    # and the planted failure, both of which are quoted in a rehearsed script.
    template_id: str | None = None
    failure_note: str | None = None
    # Place the fixed exculpatory sentence immediately after this relation.
    with_adjacent_context: bool = False
    # Pin slot values that a rehearsed script or a prerecorded transcript
    # quotes verbatim — the $48,200 in the demo sentence, the 14 days in the
    # planted failure. Everything unpinned is sampled deterministically.
    fixed: tuple[tuple[str, str], ...] = ()
    # Pin which document this relation lands in, so related evidence can be
    # co-located in one source document the way it would be in reality.
    document_index: int | None = None
    # Suppress alias substitution and always write the canonical name. Set on
    # relations whose sentence is quoted verbatim somewhere outside the code —
    # the rehearsed demo script, the prerecorded fallback transcript — where
    # "Advent" instead of "Advent Holdings" would be a mismatch on stage.
    canonical_surfaces: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedRelations:
    """Bulk relations sampled from the pools, for scenarios too large to list.

    `train_corpus` needs ~1,200 relations; enumerating them by hand would be
    both tedious and less uniform than sampling.
    """

    relation: str
    band: Band
    count: int
    routing: str = AUTO_FILE


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    name: str
    split: str
    n_documents: int
    # Document type mix, as (source, count). Must sum to n_documents.
    source_mix: tuple[tuple[str, int], ...]
    orgs: tuple[str, ...]
    persons: tuple[str, ...]
    addresses: tuple[str, ...]
    relations: tuple[RelationPlan, ...] = field(default_factory=tuple)
    generated: tuple[GeneratedRelations, ...] = field(default_factory=tuple)
    # Inclusive range of NO_RELATION filler sentences per document.
    filler_range: tuple[int, int] = (2, 4)
    # Pin a document's account reference, which also drives its title. Keeps
    # "INV-4471 Meridian Supply" stable across regeneration.
    pinned_accounts: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        total = sum(n for _, n in self.source_mix)
        if total != self.n_documents:
            raise ValueError(
                f"{self.name}: source_mix sums to {total}, expected {self.n_documents}"
            )

    @property
    def planned_relation_count(self) -> int:
        return len(self.relations) + sum(g.count for g in self.generated)


# ── the demo scenario ────────────────────────────────────────────────────────

MERIDIAN = "Meridian Supply LLC"
ADVENT = "Advent Holdings"
KESTREL = "Kestrel Registry Ltd"
NORTHGATE = "Northgate Logistics Inc"
PINEBROOK = "Pinebrook Freight Co"
HALCYON = "Halcyon Tooling LLC"

DARA = "Dara Okonkwo"
MILES = "Miles Vantree"
PRIYA = "Priya Raghunathan"

SHARED_ADDRESS = names.HELDOUT_ADDRESSES[0]

MERIDIAN_SHELL_RING = ScenarioSpec(
    name="meridian_shell_ring",
    split="demo",
    n_documents=34,
    source_mix=(
        ("invoice", 12),
        ("email", 10),
        ("press_release", 3),
        ("rss", 3),
        ("gdelt", 2),
        ("note", 2),
        ("transaction_log", 2),
    ),
    orgs=(MERIDIAN, ADVENT, KESTREL, NORTHGATE, PINEBROOK, HALCYON),
    persons=(DARA, MILES, PRIYA),
    addresses=names.HELDOUT_ADDRESSES,
    # Document 0 is the invoice the demo script opens. Both the routed-payment
    # sentence and the planted timing failure live in it.
    pinned_accounts=((0, "INV-4471"),),
    relations=(
        # ── the 3-hop ownership cycle ────────────────────────────────────────
        # Meridian -> Advent -> Kestrel -> Meridian. No single edge is damning;
        # the loop is. This is what Tarjan SCC finds server-side and what the
        # graph canvas renders with emphasis.
        RelationPlan("OWNED_BY", MERIDIAN, ADVENT, Band.A, ESCALATE),
        RelationPlan("OWNED_BY", ADVENT, KESTREL, Band.C, ESCALATE),
        RelationPlan("OWNED_BY", KESTREL, MERIDIAN, Band.D, ESCALATE),
        # ── the demo sentence, pinned ────────────────────────────────────────
        # "Payment of $48,200 was routed through Advent Holdings on behalf of
        # Meridian Supply LLC." Third-party routing between two parties that
        # also share an ownership loop and a registered address.
        RelationPlan(
            "WIRED_FUNDS_TO",
            MERIDIAN,
            ADVENT,
            Band.C,
            ESCALATE,
            template_id="wired.c1",
            fixed=(("amount", "$48,200"),),
            document_index=0,
            canonical_surfaces=True,
        ),
        # ── shared registered address, the corroborating evidence ────────────
        RelationPlan("SHARES_ADDRESS_WITH", MERIDIAN, ADVENT, Band.A, FLAG),
        RelationPlan("SHARES_ADDRESS_WITH", ADVENT, KESTREL, Band.B, FLAG),
        # ── one person signing for all three, which is the human tell ────────
        RelationPlan("SIGNATORY_OF", DARA, MERIDIAN, Band.A, FLAG),
        RelationPlan("SIGNATORY_OF", DARA, ADVENT, Band.B, ESCALATE),
        RelationPlan("SIGNATORY_OF", DARA, KESTREL, Band.D, ESCALATE),
        # ── layering: funds moving around the ring ───────────────────────────
        RelationPlan("WIRED_FUNDS_TO", ADVENT, KESTREL, Band.B, ESCALATE),
        RelationPlan("WIRED_FUNDS_TO", KESTREL, MERIDIAN, Band.D, ESCALATE),
        RelationPlan("WIRED_FUNDS_TO", MERIDIAN, KESTREL, Band.C, FLAG),
        # ── the planted failure (plan §0) ────────────────────────────────────
        # Timing anomaly in the citation sentence, benign contractual
        # explanation in the sentence immediately after it. The cascade passes
        # one sentence, so the exculpation is invisible to it and it
        # over-escalates. Ground truth is flag_for_review.
        RelationPlan(
            "INVOICED",
            MERIDIAN,
            NORTHGATE,
            Band.B,
            FLAG,
            template_id="invoiced.timing",
            with_adjacent_context=True,
            fixed=(("days", "14"),),
            document_index=0,
            canonical_surfaces=True,
            failure_note=(
                "Nemotron over-escalates on timing anomalies when the counterparty has any "
                "prior flag, even where a benign contractual explanation is present in the "
                "adjacent sentence. The cascade passes only the single citation sentence plus "
                "graph context, not neighbouring sentences, so the exculpatory context is "
                "invisible to it. The fix is a two-sentence citation window, which we would "
                "do with more time; we are reporting the failure rather than widening the "
                "window and quietly removing the evidence of it."
            ),
        ),
        # ── second timing anomaly, this one genuinely worth flagging ─────────
        # No adjacent explanation. Same surface shape as the planted failure,
        # which is what makes the failure interesting rather than arbitrary.
        RelationPlan(
            "INVOICED",
            ADVENT,
            MERIDIAN,
            Band.B,
            ESCALATE,
            template_id="invoiced.timing",
            fixed=(("days", "9"),),
        ),
        # ── the red herring ──────────────────────────────────────────────────
        # Northgate transacts with ring members and shares nothing else. Ground
        # truth is auto_file. If the pipeline escalates these, the gate is
        # mistaking proximity for complicity.
        RelationPlan("INVOICED", NORTHGATE, MERIDIAN, Band.A, AUTO_FILE),
        RelationPlan("WIRED_FUNDS_TO", NORTHGATE, MERIDIAN, Band.A, AUTO_FILE),
        RelationPlan("INVOICED", NORTHGATE, PINEBROOK, Band.B, AUTO_FILE),
        RelationPlan("SIGNATORY_OF", MILES, NORTHGATE, Band.A, AUTO_FILE),
        # ── ordinary business, so the feed is not all alarm ──────────────────
        RelationPlan("INVOICED", PINEBROOK, MERIDIAN, Band.A, AUTO_FILE),
        RelationPlan("INVOICED", HALCYON, MERIDIAN, Band.A, AUTO_FILE),
        RelationPlan("INVOICED", PINEBROOK, NORTHGATE, Band.A, AUTO_FILE),
        RelationPlan("INVOICED", HALCYON, NORTHGATE, Band.B, AUTO_FILE),
        RelationPlan("WIRED_FUNDS_TO", MERIDIAN, PINEBROOK, Band.A, AUTO_FILE),
        RelationPlan("WIRED_FUNDS_TO", MERIDIAN, HALCYON, Band.A, AUTO_FILE),
        RelationPlan("WIRED_FUNDS_TO", NORTHGATE, HALCYON, Band.B, AUTO_FILE),
        RelationPlan("SIGNATORY_OF", PRIYA, PINEBROOK, Band.A, AUTO_FILE),
        RelationPlan("SIGNATORY_OF", MILES, HALCYON, Band.C, AUTO_FILE),
        RelationPlan("INVOICED", HALCYON, PINEBROOK, Band.C, AUTO_FILE),
        RelationPlan("WIRED_FUNDS_TO", PINEBROOK, HALCYON, Band.D, AUTO_FILE),
        RelationPlan("OWNED_BY", PINEBROOK, NORTHGATE, Band.A, AUTO_FILE),
        RelationPlan("INVOICED", MERIDIAN, HALCYON, Band.A, AUTO_FILE),
        RelationPlan("INVOICED", MERIDIAN, PINEBROOK, Band.B, AUTO_FILE),
        RelationPlan("SHARES_ADDRESS_WITH", PINEBROOK, HALCYON, Band.C, AUTO_FILE),
        RelationPlan("INVOICED", NORTHGATE, HALCYON, Band.A, AUTO_FILE),
        RelationPlan("WIRED_FUNDS_TO", HALCYON, PINEBROOK, Band.A, AUTO_FILE),
        RelationPlan("INVOICED", PINEBROOK, HALCYON, Band.A, AUTO_FILE),
        RelationPlan("WIRED_FUNDS_TO", NORTHGATE, PINEBROOK, Band.C, AUTO_FILE),
        RelationPlan("SIGNATORY_OF", PRIYA, HALCYON, Band.B, AUTO_FILE),
        RelationPlan("INVOICED", HALCYON, MERIDIAN, Band.D, AUTO_FILE),
    ),
    # Ordinary business, sampled rather than enumerated. Brings the feed to
    # roughly 60 insights so it looks like a real week of documents instead of
    # a curated fraud exhibit — and so the escalation *rate* is a believable
    # denominator rather than flattered by a feed of nothing but flags.
    generated=(
        GeneratedRelations("INVOICED", Band.A, 7),
        GeneratedRelations("INVOICED", Band.B, 4),
        GeneratedRelations("WIRED_FUNDS_TO", Band.A, 5),
        GeneratedRelations("WIRED_FUNDS_TO", Band.C, 2),
        GeneratedRelations("SIGNATORY_OF", Band.A, 2),
        GeneratedRelations("INVOICED", Band.D, 2),
    ),
)

# ── the control scenario ─────────────────────────────────────────────────────

CLEAN_BASELINE = ScenarioSpec(
    name="clean_baseline",
    split="demo",
    n_documents=28,
    source_mix=(
        ("invoice", 14),
        ("email", 8),
        ("note", 3),
        ("transaction_log", 3),
    ),
    orgs=(
        "Saltmarsh Provisioning Ltd",
        "Verity Yard Services Co",
        "Windlass Marine Supply Inc",
        "Tessellate Print Group Ltd",
    ),
    persons=("Teodora Lascu", "Ansel Kirkbride"),
    addresses=names.HELDOUT_ADDRESSES[1:],
    generated=(
        # Only INVOICED, WIRED_FUNDS_TO and SIGNATORY_OF: no ownership loops,
        # no shared addresses, nothing a competent analyst would escalate.
        # Bands still span A–D, because a clean corpus where the model is
        # uniformly certain would not test the gate's false-positive rate on
        # genuinely unfamiliar-looking-but-innocent text.
        GeneratedRelations("INVOICED", Band.A, 14),
        GeneratedRelations("INVOICED", Band.B, 8),
        GeneratedRelations("WIRED_FUNDS_TO", Band.A, 8),
        GeneratedRelations("WIRED_FUNDS_TO", Band.C, 5),
        GeneratedRelations("SIGNATORY_OF", Band.A, 4),
        GeneratedRelations("WIRED_FUNDS_TO", Band.D, 3),
        GeneratedRelations("INVOICED", Band.D, 2),
    ),
    filler_range=(3, 5),
)

# ── the stress scenario ──────────────────────────────────────────────────────

INVOICE_FLOOD = ScenarioSpec(
    name="invoice_flood",
    split="demo",
    n_documents=120,
    source_mix=(
        ("invoice", 96),
        ("email", 16),
        ("transaction_log", 8),
    ),
    orgs=names.HELDOUT_ORGS,
    persons=names.HELDOUT_PERSONS,
    addresses=names.HELDOUT_ADDRESSES,
    generated=(
        GeneratedRelations("INVOICED", Band.A, 90),
        GeneratedRelations("INVOICED", Band.B, 60),
        GeneratedRelations("INVOICED", Band.C, 30),
        GeneratedRelations("WIRED_FUNDS_TO", Band.A, 40),
        GeneratedRelations("WIRED_FUNDS_TO", Band.B, 24),
        GeneratedRelations("WIRED_FUNDS_TO", Band.C, 14, FLAG),
        GeneratedRelations("SIGNATORY_OF", Band.A, 16),
        GeneratedRelations("SHARES_ADDRESS_WITH", Band.B, 8, FLAG),
        GeneratedRelations("INVOICED", Band.D, 12),
        GeneratedRelations("WIRED_FUNDS_TO", Band.D, 8, FLAG),
    ),
    filler_range=(1, 3),
)

# ── the training corpus ──────────────────────────────────────────────────────

TRAIN_CORPUS = ScenarioSpec(
    name="train_corpus",
    split="train",
    n_documents=400,
    source_mix=(
        ("invoice", 180),
        ("email", 120),
        ("press_release", 30),
        ("rss", 24),
        ("gdelt", 16),
        ("note", 16),
        ("transaction_log", 14),
    ),
    orgs=names.TRAIN_ORGS,
    persons=names.TRAIN_PERSONS,
    addresses=names.TRAIN_ADDRESSES,
    generated=(
        # Bands A–C only. Requesting band D here raises in templates.py rather
        # than silently producing a contaminated training set.
        GeneratedRelations("WIRED_FUNDS_TO", Band.A, 150),
        GeneratedRelations("WIRED_FUNDS_TO", Band.B, 110),
        GeneratedRelations("WIRED_FUNDS_TO", Band.C, 70),
        GeneratedRelations("INVOICED", Band.A, 150),
        GeneratedRelations("INVOICED", Band.B, 110),
        GeneratedRelations("INVOICED", Band.C, 70),
        GeneratedRelations("OWNED_BY", Band.A, 70),
        GeneratedRelations("OWNED_BY", Band.B, 50),
        GeneratedRelations("OWNED_BY", Band.C, 40),
        GeneratedRelations("SHARES_ADDRESS_WITH", Band.A, 60),
        GeneratedRelations("SHARES_ADDRESS_WITH", Band.B, 45),
        GeneratedRelations("SHARES_ADDRESS_WITH", Band.C, 35),
        GeneratedRelations("SIGNATORY_OF", Band.A, 70),
        GeneratedRelations("SIGNATORY_OF", Band.B, 50),
        GeneratedRelations("SIGNATORY_OF", Band.C, 40),
    ),
    filler_range=(2, 5),
)


SCENARIOS: dict[str, ScenarioSpec] = {
    spec.name: spec
    for spec in (MERIDIAN_SHELL_RING, CLEAN_BASELINE, INVOICE_FLOOD, TRAIN_CORPUS)
}

SERVABLE_SCENARIOS: tuple[str, ...] = tuple(
    name for name, spec in SCENARIOS.items() if spec.split == "demo"
)


def get_scenario(name: str) -> ScenarioSpec:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise LookupError(
            f"unknown scenario {name!r}. Available: {sorted(SCENARIOS)}"
        ) from None
