"""Name pools for the synthetic corpus.

The split here is the load-bearing part, and it is the reason the fragility
claim can be made at all.

`TRAIN_*` pools are the only names the tagger and relation model ever see.
`HELDOUT_*` pools appear exclusively in band-D documents of the servable
scenarios and in the fuzzer's `rename` perturbation. That separation is what
distinguishes two things a single pool would confuse:

- a model that has learned *structure* — "the thing before 'wired' is a payer"
- a model that has memorized *strings* — "Meridian Supply is a payer"

If every name were in training, vacuity would be flat near zero, the rename
perturbation would measure nothing, and the vacuity-versus-fragility
correlation would come out as noise. There is no fix for that at hour 13, which
is why the pools are disjoint at hour 1.

`assert_pools_disjoint()` is called by tests/test_synth.py. A name that drifts
across the boundary contaminates every number the project reports, and it is
the kind of mistake that is invisible until a judge asks the right question.
"""

from __future__ import annotations

# ── organizations ────────────────────────────────────────────────────────────

TRAIN_ORGS: tuple[str, ...] = (
    "Brightwater Industrial LLC",
    "Calderon Freight Co",
    "Dunmore Fabrication Inc",
    "Elmridge Components LLC",
    "Fairhaven Logistics Ltd",
    "Granville Tooling Corp",
    "Harborline Supply Co",
    "Ironvale Metals LLC",
    "Junction Park Trading Inc",
    "Keswick Materials Ltd",
    "Lambourne Distribution LLC",
    "Marchmont Equipment Co",
    "Netherby Plastics Inc",
    "Orrindale Packaging LLC",
    "Pemberton Castings Ltd",
    "Quarrymead Aggregates Co",
    "Rothsay Instruments Inc",
    "Sedgefield Bearings LLC",
    "Thornbury Coatings Ltd",
    "Upminster Hydraulics Co",
    "Vanbrugh Assemblies Inc",
    "Wrenfield Textiles LLC",
    "Yarborough Chemicals Ltd",
    "Ashcombe Valve Co",
    "Bellingham Sheet Metal Inc",
    "Crowhurst Adhesives LLC",
    "Deverill Gasket Co",
    "Eastmarch Conveyors Ltd",
)

# Never in a training split. These are the names the model has genuinely never
# seen, and the ones the `rename` perturbation swaps in.
HELDOUT_ORGS: tuple[str, ...] = (
    "Meridian Supply LLC",
    "Advent Holdings",
    "Kestrel Registry Ltd",
    "Northgate Logistics Inc",
    "Pinebrook Freight Co",
    "Halcyon Tooling LLC",
    "Saltmarsh Provisioning Ltd",
    "Verity Yard Services Co",
    "Windlass Marine Supply Inc",
    "Ozimandias Crate Works LLC",
    "Tessellate Print Group Ltd",
    "Gantry & Poole Fittings Co",
)

# Surface variants the coreference layer is expected to merge onto one entity.
# Deliberately includes the punctuation-and-suffix cases that char-3gram cosine
# handles well, and NOT the genuinely hard cases — we do not claim to solve
# those, and the alias list in the API shows a judge exactly what got merged.
ALIASES: dict[str, tuple[str, ...]] = {
    "Meridian Supply LLC": ("Meridian Supply", "Meridian Supply, LLC", "Meridian"),
    "Advent Holdings": ("Advent Holdings Ltd", "Advent"),
    "Kestrel Registry Ltd": ("Kestrel Registry", "Kestrel Registry Limited"),
    "Northgate Logistics Inc": ("Northgate Logistics", "Northgate"),
    "Pinebrook Freight Co": ("Pinebrook Freight",),
    "Halcyon Tooling LLC": ("Halcyon Tooling",),
}

# ── people ───────────────────────────────────────────────────────────────────

TRAIN_PERSONS: tuple[str, ...] = (
    "Alan Prescott",
    "Bianca Moreau",
    "Colin Ravensworth",
    "Deborah Ashworth",
    "Emeka Nwachukwu",
    "Frances Lindqvist",
    "Gerald Mbeki",
    "Hannah Okada",
    "Ivan Petrescu",
    "Joanna Fitzalan",
    "Kwame Boateng",
    "Lucia Marchetti",
    "Malcolm Tarrant",
    "Nadia Haddad",
    "Oliver Strand",
    "Petra Novakova",
)

HELDOUT_PERSONS: tuple[str, ...] = (
    "Dara Okonkwo",
    "Miles Vantree",
    "Priya Raghunathan",
    "Teodora Lascu",
    "Ansel Kirkbride",
    "Rosalind Achebe",
)

# ── addresses ────────────────────────────────────────────────────────────────
# Addresses are rendered as literal text, not tagged mentions: the tag set in
# brief §15 has no ADDRESS label, and SHARES_ADDRESS_WITH relates two ORGs with
# the address as evidence rather than as an argument.

TRAIN_ADDRESSES: tuple[str, ...] = (
    "114 Cargill Row, Sheffield S9 2LP",
    "2200 Dockside Avenue, Suite 410, Cleveland OH 44113",
    "8 Ellesmere Trading Estate, Birkenhead CH41 1DT",
    "5510 Foundry Street, Pittsburgh PA 15201",
    "33 Waverley Industrial Park, Leeds LS11 5AR",
)

HELDOUT_ADDRESSES: tuple[str, ...] = (
    # The shared-address evidence in the demo scenario. One string, three
    # counterparties — which is the whole point.
    "17 Calder Wharf, Unit 3B, Pittsburgh PA 15222",
    "940 Lorimer Street, Floor 6, Newark NJ 07102",
    "61 Ashgrove Quay, Liverpool L3 4BQ",
)

# ── transaction vocabulary ───────────────────────────────────────────────────

TRANSACTION_TYPES: tuple[str, ...] = (
    "wire transfer",
    "ACH debit",
    "remittance",
    "book transfer",
    "standing order",
    "intercompany settlement",
)

# ── account reference formats ────────────────────────────────────────────────
# Multiple shapes on purpose: the tagger's weak supervision uses a regex for
# ACCOUNT_REF, and a single format would make that regex trivially perfect in a
# way that inflates the reported F1.

ACCOUNT_FORMATS: tuple[str, ...] = (
    "INV-{n:04d}",
    "AP-{n:05d}",
    "REF/{n:04d}/B",
    "PO {n:06d}",
    "ACCT-{n:04d}-X",
)


def canonical_orgs(*, split: str) -> tuple[str, ...]:
    return TRAIN_ORGS if split == "train" else HELDOUT_ORGS


def canonical_persons(*, split: str) -> tuple[str, ...]:
    return TRAIN_PERSONS if split == "train" else HELDOUT_PERSONS


def aliases_for(canonical: str) -> tuple[str, ...]:
    """Surface forms for an entity, canonical form first."""
    return (canonical, *ALIASES.get(canonical, ()))


def assert_pools_disjoint() -> None:
    """Fail loudly if a name has drifted across the train/held-out boundary.

    Checked on every generator run and asserted in tests/test_synth.py. The
    comparison is casefolded because "advent holdings" leaking into the
    training pool contaminates the eval just as thoroughly as "Advent Holdings".
    """
    pairs = (
        ("orgs", TRAIN_ORGS, HELDOUT_ORGS),
        ("persons", TRAIN_PERSONS, HELDOUT_PERSONS),
        ("addresses", TRAIN_ADDRESSES, HELDOUT_ADDRESSES),
    )
    for label, train, heldout in pairs:
        overlap = {n.casefold() for n in train} & {n.casefold() for n in heldout}
        if overlap:
            raise AssertionError(
                f"{label} pools overlap on {sorted(overlap)} — every eval number "
                "derived from this corpus would be contaminated."
            )

    # An alias of a held-out entity must not appear in the training pool
    # either; the model would have seen the string under a different name.
    train_all = {n.casefold() for n in (*TRAIN_ORGS, *TRAIN_PERSONS)}
    for canonical in HELDOUT_ORGS:
        for alias in aliases_for(canonical):
            if alias.casefold() in train_all:
                raise AssertionError(
                    f"alias {alias!r} of held-out entity {canonical!r} is in the training pool"
                )
